from __future__ import annotations

import re
from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass(slots=True)
class BusinessRecord:
    business_id: int
    name: str
    description: str
    category: str
    address: str
    latitude: float
    longitude: float
    claimed_by: str
    ads_keywords: str


class BusinessListingEngine:
    def __init__(self, redis: Redis, *, key_prefix: str = 'business') -> None:
        self.redis = redis
        self.key_prefix = key_prefix
        self.seq_key = f'{key_prefix}:id_seq'
        self.geo_key = f'{key_prefix}:geo'

    async def add_business(
        self,
        *,
        name: str,
        description: str,
        category: str,
        address: str,
        latitude: float,
        longitude: float,
        ads_keywords: list[str] | None = None,
    ) -> int:
        business_id = int(await self.redis.incr(self.seq_key))
        record = {
            'id': business_id,
            'name': name,
            'description': description,
            'category': category.lower(),
            'address': address,
            'latitude': latitude,
            'longitude': longitude,
            'claimed_by': '',
            # Prepared for ads monetization targeting.
            'ads_keywords': ','.join(sorted({k.strip().lower() for k in (ads_keywords or []) if k.strip()})),
        }

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(self._business_key(business_id), mapping=record)
            pipe.geoadd(self.geo_key, values=[longitude, latitude, str(business_id)])
            for token in self._index_terms(name, description, category):
                pipe.sadd(self._search_key(token), business_id)
            await pipe.execute()

        return business_id

    async def claim_business(self, *, business_id: int, owner_id: str) -> bool:
        key = self._business_key(business_id)
        exists = await self.redis.exists(key)
        if not exists:
            return False

        # Claim only if currently unclaimed.
        claimed = await self.redis.hsetnx(key, 'claimed_by', owner_id)
        if claimed == 1:
            return True

        current_owner = await self.redis.hget(key, 'claimed_by')
        if current_owner in (None, ''):
            await self.redis.hset(key, mapping={'claimed_by': owner_id})
            return True
        return current_owner == owner_id

    async def search_businesses(
        self,
        *,
        query: str,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float = 10.0,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        terms = self._index_terms(query)
        if not terms:
            return []

        async with self.redis.pipeline(transaction=False) as pipe:
            for term in terms:
                pipe.smembers(self._search_key(term))
            match_sets = await pipe.execute()

        candidate_ids: set[str] = set()
        for ids in match_sets:
            candidate_ids.update(ids)

        if latitude is not None and longitude is not None and candidate_ids:
            nearby = await self.redis.geosearch(
                self.geo_key,
                longitude=longitude,
                latitude=latitude,
                radius=radius_km,
                unit='km',
                count=limit * 5,
            )
            candidate_ids.intersection_update(set(nearby))

        if not candidate_ids:
            return []

        sorted_ids = sorted(int(bid) for bid in candidate_ids)[: limit * 3]
        async with self.redis.pipeline(transaction=False) as pipe:
            for business_id in sorted_ids:
                pipe.hgetall(self._business_key(business_id))
            rows = await pipe.execute()

        results: list[dict[str, str]] = []
        for row in rows:
            if not row:
                continue
            results.append(
                {
                    'id': str(row.get('id', '')),
                    'name': str(row.get('name', '')),
                    'description': str(row.get('description', '')),
                    'category': str(row.get('category', '')),
                    'address': str(row.get('address', '')),
                    'latitude': str(row.get('latitude', '')),
                    'longitude': str(row.get('longitude', '')),
                    'claimed_by': str(row.get('claimed_by', '')),
                    'ads_keywords': str(row.get('ads_keywords', '')),
                }
            )

        return results[:limit]

    def _business_key(self, business_id: int) -> str:
        return f'{self.key_prefix}:item:{business_id}'

    def _search_key(self, token: str) -> str:
        return f'{self.key_prefix}:search:{token}'

    @staticmethod
    def _index_terms(*chunks: str) -> set[str]:
        terms: set[str] = set()
        for chunk in chunks:
            for token in re.findall(r'\w+', chunk.lower()):
                if len(token) >= 2:
                    terms.add(token)
        return terms
