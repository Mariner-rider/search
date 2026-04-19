from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from elasticsearch import AsyncElasticsearch
from redis.asyncio import Redis

from services.ads.app.engine import AdsEngine


@dataclass(slots=True)
class OrchestratorConfig:
    search_index_name: str
    ads_per_page: int
    yacy_integration_url: str
    llm_answer_url: str
    internal_weight: float = 1.0
    yacy_weight: float = 0.35
    upstream_timeout_s: float = 1.8
    search_cache_ttl_s: int = 45
    ads_cache_ttl_s: int = 90


class SearchOrchestrator:
    def __init__(self, *, es: AsyncElasticsearch, ads: AdsEngine, redis: Redis, config: OrchestratorConfig) -> None:
        self.es = es
        self.ads = ads
        self.redis = redis
        self.config = config

    async def execute(self, *, query: str, page: int, page_size: int, ai_mode: bool) -> dict[str, Any]:
        started = time.perf_counter()

        internal_task = asyncio.create_task(self._internal_results(query=query, page=page, page_size=page_size))
        yacy_task = asyncio.create_task(self._yacy_results(query=query, page_size=page_size))
        ads_task = asyncio.create_task(self._ads(query=query))

        internal, yacy, ads = await asyncio.gather(internal_task, yacy_task, ads_task)
        merged_results = self._merge_ranked(internal=internal, yacy=yacy, page_size=page_size)

        ai_overview: dict[str, Any] = {'answer': '', 'sources': []}
        if ai_mode:
            ai_overview = await self._ai_overview(query=query, results=merged_results)

        took_ms = int((time.perf_counter() - started) * 1000)
        return {
            'results': merged_results,
            'ads': ads,
            'ai_overview': ai_overview,
            'orchestration': {
                'latency_ms': took_ms,
                'components': ['elasticsearch', 'yacy', 'ads', 'llm' if ai_mode else 'llm-disabled'],
            },
        }

    async def _internal_results(self, *, query: str, page: int, page_size: int) -> list[dict[str, Any]]:
        cache_key = f'cache:search:internal:{query}:{page}:{page_size}'
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)

        response = await asyncio.wait_for(
            self.es.search(
                index=self.config.search_index_name,
                query={'multi_match': {'query': query, 'fields': ['title^2', 'content', 'snippet']}},
                from_=(page - 1) * page_size,
                size=page_size,
                track_total_hits=True,
            ),
            timeout=self.config.upstream_timeout_s,
        )
        hits = response.get('hits', {}).get('hits', [])

        results = [
            {
                'title': h.get('_source', {}).get('title', ''),
                'url': h.get('_source', {}).get('url', ''),
                'snippet': h.get('_source', {}).get('snippet', ''),
                'source': 'internal',
                'timestamp': str(h.get('_source', {}).get('timestamp', '')),
                'score': float(h.get('_score', 0.0)) * self.config.internal_weight,
            }
            for h in hits
        ]
        await self.redis.set(cache_key, json.dumps(results), ex=self.config.search_cache_ttl_s)
        return results

    async def _yacy_results(self, *, query: str, page_size: int) -> list[dict[str, Any]]:
        cache_key = f'cache:search:yacy:{query}:{page_size}'
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)

        try:
            async with httpx.AsyncClient(timeout=self.config.upstream_timeout_s) as client:
                resp = await client.get(
                    f'{self.config.yacy_integration_url}/yacy/search',
                    params={'q': query, 'page_size': page_size},
                )
                if resp.status_code >= 400:
                    return []
                payload = resp.json()
        except Exception:
            return []

        items = payload.get('results', [])
        results = [
            {
                'title': i.get('title', ''),
                'url': i.get('url', ''),
                'snippet': i.get('snippet', ''),
                'source': i.get('source', 'yacy'),
                'timestamp': '',
                'score': float(i.get('weighted_score', 0.0)) * self.config.yacy_weight,
            }
            for i in items
        ]
        await self.redis.set(cache_key, json.dumps(results), ex=self.config.search_cache_ttl_s)
        return results

    async def _ads(self, *, query: str) -> list[dict[str, str]]:
        cache_key = f'cache:ads:{query}:{self.config.ads_per_page}'
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)
        try:
            results = await asyncio.wait_for(self.ads.get_top_ads(query, limit=self.config.ads_per_page), timeout=0.8)
            await self.redis.set(cache_key, json.dumps(results), ex=self.config.ads_cache_ttl_s)
            return results
        except Exception:
            return []

    async def _ai_overview(self, *, query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        contexts = [{'title': r['title'], 'url': r['url'], 'snippet': r['snippet']} for r in results[:8]]
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                resp = await client.post(f'{self.config.llm_answer_url}/answer', json={'query': query, 'contexts': contexts})
                if resp.status_code >= 400:
                    return {'answer': '', 'sources': []}
                payload = resp.json()
                return {'answer': payload.get('answer', ''), 'sources': payload.get('sources', [])}
        except Exception:
            return {'answer': '', 'sources': []}

    @staticmethod
    def _merge_ranked(*, internal: list[dict[str, Any]], yacy: list[dict[str, Any]], page_size: int) -> list[dict[str, Any]]:
        by_url: dict[str, dict[str, Any]] = {}
        for result in [*internal, *yacy]:
            key = str(result.get('url', '')).strip().lower()
            if not key:
                continue
            prev = by_url.get(key)
            if prev is None or float(result.get('score', 0.0)) > float(prev.get('score', 0.0)):
                by_url[key] = result

        merged = sorted(by_url.values(), key=lambda item: float(item.get('score', 0.0)), reverse=True)
        return merged[:page_size]
