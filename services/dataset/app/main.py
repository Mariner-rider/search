from __future__ import annotations

import re
from html import unescape

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Query
from pydantic import BaseModel

from services.shared.app.config import get_settings

app = FastAPI(title='dataset')


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


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.es.close()


@app.get('/dataset', response_model=DatasetResponse)
async def dataset(query: str = Query(..., min_length=1), size: int = Query(25, ge=1, le=200)) -> DatasetResponse:
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
    return DatasetResponse(query=query, total=total, records=records)


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
