from __future__ import annotations

import re
from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass(slots=True)
class AdCandidate:
    ad_id: int
    keyword: str
    bid: float
    budget: float
    title: str
    url: str


class AdsEngine:
    def __init__(self, redis: Redis, *, key_prefix: str = 'ads') -> None:
        self.redis = redis
        self.key_prefix = key_prefix
        self.seq_key = f'{key_prefix}:seq'

    async def store_ad(self, *, keyword: str, bid: float, budget: float, title: str, url: str) -> int:
        ad_id = int(await self.redis.incr(self.seq_key))
        normalized_keyword = keyword.strip().lower()
        ad_key = f'{self.key_prefix}:item:{ad_id}'
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(ad_key, mapping={
                'id': ad_id,
                'keyword': normalized_keyword,
                'bid': bid,
                'budget': budget,
                'title': title,
                'url': url,
            })
            pipe.sadd(f'{self.key_prefix}:keyword:{normalized_keyword}', ad_id)
            await pipe.execute()
        return ad_id

    async def get_top_ads(self, query: str, *, limit: int = 3) -> list[dict[str, str]]:
        terms = {t.lower() for t in re.findall(r'\w+', query)}
        if not terms:
            return []

        async with self.redis.pipeline(transaction=False) as pipe:
            for term in terms:
                pipe.smembers(f'{self.key_prefix}:keyword:{term}')
            id_sets = await pipe.execute()

        ad_ids: set[str] = set()
        for ids in id_sets:
            ad_ids.update(ids)

        candidates = await self._load_candidates(ad_ids)
        ranked: list[tuple[float, AdCandidate]] = []
        for ad in candidates:
            if ad.budget <= 0:
                continue
            relevance_score = self._relevance_score(query, ad.keyword)
            ad_rank = ad.bid * relevance_score
            if ad_rank > 0:
                ranked.append((ad_rank, ad))

        ranked.sort(key=lambda x: x[0], reverse=True)
        return [
            {'title': ad.title, 'url': ad.url, 'bid': f'{ad.bid:.2f}'}
            for _, ad in ranked[:limit]
        ]

    async def _load_candidates(self, ad_ids: set[str]) -> list[AdCandidate]:
        if not ad_ids:
            return []
        sorted_ids = sorted(int(aid) for aid in ad_ids)
        async with self.redis.pipeline(transaction=False) as pipe:
            for ad_id in sorted_ids:
                pipe.hgetall(f'{self.key_prefix}:item:{ad_id}')
            rows = await pipe.execute()

        return [
            AdCandidate(
                ad_id=int(row['id']),
                keyword=str(row['keyword']),
                bid=float(row['bid']),
                budget=float(row['budget']),
                title=str(row['title']),
                url=str(row['url']),
            )
            for row in rows if row
        ]

    @staticmethod
    def _relevance_score(query: str, keyword: str) -> float:
        query_terms = set(re.findall(r'\w+', query.lower()))
        key_terms = set(re.findall(r'\w+', keyword.lower()))
        if not key_terms:
            return 0.0
        return len(query_terms & key_terms) / len(key_terms)
