from __future__ import annotations

import json
import re
import unicodedata
from html import unescape

from elasticsearch import AsyncElasticsearch
from fastapi import HTTPException, FastAPI, Query
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from redis.asyncio import Redis

from services.shared.app.config import get_settings

app = FastAPI(title='dataset')
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware('http')
async def cdn_headers(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'public, max-age=120, s-maxage=600, stale-while-revalidate=60'
    response.headers['Vary'] = 'Accept-Encoding'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return response


class DatasetRecord(BaseModel):
    url: str
    title: str
    timestamp: str
    structured_content: dict[str, str]
    clean_text: str


class DatasetResponse(BaseModel):
    query: str
    total: int
    records: list[DatasetRecord]


@app.on_event('startup')
async def startup() -> None:
    settings = get_settings()
    app.state.es = AsyncElasticsearch(settings.elasticsearch_url)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.es.close()
    await app.state.redis.close()


@app.get('/dataset', response_model=DatasetResponse)
async def dataset(query: str = Query(..., min_length=1), size: int = Query(25, ge=1, le=200)) -> DatasetResponse:
    query = _sanitize_query(query)
    cache_key = f'cache:dataset:{query}:{size}'
    cached = await app.state.redis.get(cache_key)
    if cached:
        raw = json.loads(cached)
        return DatasetResponse(**raw)

    settings = get_settings()
    response = await app.state.es.search(
        index=settings.search_index_name,
        size=size,
        query={
            'multi_match': {
                'query': query,
                'fields': ['title^2', 'content', 'snippet'],
            }
        },
        track_total_hits=True,
    )

    hits = response.get('hits', {}).get('hits', [])
    records = [_to_record(hit.get('_source', {})) for hit in hits]
    total = int(response.get('hits', {}).get('total', {}).get('value', 0))
    payload = DatasetResponse(query=query, total=total, records=records)

    await app.state.redis.set(cache_key, payload.model_dump_json(), ex=120)
    return payload


@app.get('/security/status')
async def security_status() -> dict[str, str]:
    return {'lockdown': 'true' if await app.state.redis.get('sec:lockdown:active') else 'false'}


def _to_record(source: dict[str, object]) -> DatasetRecord:
    title = str(source.get('title', ''))
    content = str(source.get('content', ''))
    snippet = str(source.get('snippet', ''))

    clean_content = _clean_text(content)
    clean_snippet = _clean_text(snippet)
    clean_title = _clean_text(title)

    return DatasetRecord(
        url=str(source.get('url', '')),
        title=clean_title,
        timestamp=str(source.get('timestamp', '')),
        structured_content={
            'title': clean_title,
            'snippet': clean_snippet,
            'content': clean_content,
        },
        clean_text=_for_ml_training(clean_title, clean_snippet, clean_content),
    )


def _clean_text(raw: str) -> str:
    text = unescape(raw)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _for_ml_training(title: str, snippet: str, content: str) -> str:
    merged = f"title: {title}\nsnippet: {snippet}\ncontent: {content}"
    return merged.strip()


def _sanitize_query(text: str) -> str:
    normalized = unicodedata.normalize('NFKC', text)
    normalized = re.sub(r'[\x00-\x1f\x7f]', ' ', normalized)
    lowered = normalized.lower()
    if any(token in lowered for token in ('<script', 'javascript:', 'onerror=', 'onload=', '</', '<iframe')):
        raise HTTPException(status_code=400, detail='potential script injection blocked')
    return normalized.strip()[:512]
