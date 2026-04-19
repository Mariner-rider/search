from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Query

app = FastAPI(title='yacy-integration')


@dataclass(slots=True)
class RankedResult:
    title: str
    url: str
    snippet: str
    source: str
    weighted_score: float


@app.on_event('startup')
async def startup() -> None:
    app.state.es = AsyncElasticsearch('http://elasticsearch:9200')
    app.state.index = 'search_documents'
    app.state.yacy_base = 'http://yacy:8090'
    app.state.internal_weight = 1.0
    app.state.yacy_weight = 0.35


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.es.close()


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/yacy/search')
async def yacy_search(q: str = Query(..., min_length=1), page_size: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    internal = await _internal_search(q, page_size)
    yacy = await _yacy_search(q, page_size)

    merged = _merge_ranked(internal, yacy, page_size)
    return {
        'query': q,
        'weights': {
            'internal_weight': app.state.internal_weight,
            'yacy_weight': app.state.yacy_weight,
            'policy': 'internal_score > yacy_score',
        },
        'results': [
            {
                'title': r.title,
                'url': r.url,
                'snippet': r.snippet,
                'source': r.source,
                'weighted_score': round(r.weighted_score, 6),
            }
            for r in merged
        ],
    }


async def _internal_search(q: str, page_size: int) -> list[RankedResult]:
    response = await app.state.es.search(
        index=app.state.index,
        size=page_size,
        query={'multi_match': {'query': q, 'fields': ['title^2', 'content', 'snippet']}},
        track_total_hits=False,
    )
    hits = response.get('hits', {}).get('hits', [])
    return [
        RankedResult(
            title=str(hit.get('_source', {}).get('title', '')),
            url=str(hit.get('_source', {}).get('url', '')),
            snippet=str(hit.get('_source', {}).get('snippet', '')),
            source='internal',
            weighted_score=float(hit.get('_score', 0.0)) * app.state.internal_weight,
        )
        for hit in hits
    ]


async def _yacy_search(q: str, page_size: int) -> list[RankedResult]:
    params = {
        'query': q,
        'maximumRecords': page_size,
        'resource': 'global',
        'verify': 'false',
        'contentdom': 'text',
        'nav': 'none',
        'wt': 'json',
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{app.state.yacy_base}/yacysearch.json", params=params)
        response.raise_for_status()
        payload = response.json()

    channels = payload.get('channels', [])
    if not channels:
        return []

    items = channels[0].get('items', [])
    results: list[RankedResult] = []
    for i, item in enumerate(items):
        pseudo = max(page_size - i, 1) / page_size
        results.append(
            RankedResult(
                title=str(item.get('title', '')),
                url=str(item.get('link', '')),
                snippet=str(item.get('description', '')),
                source='yacy',
                weighted_score=pseudo * app.state.yacy_weight,
            )
        )
    return results


def _merge_ranked(internal: list[RankedResult], yacy: list[RankedResult], page_size: int) -> list[RankedResult]:
    by_url: dict[str, RankedResult] = {}

    for result in [*internal, *yacy]:
        key = result.url.strip().lower()
        if not key:
            continue
        prev = by_url.get(key)
        if prev is None or result.weighted_score > prev.weighted_score:
            by_url[key] = result

    merged = sorted(by_url.values(), key=lambda r: r.weighted_score, reverse=True)
    return merged[:page_size]
