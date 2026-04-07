from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from services.business.app.engine import BusinessListingEngine
from services.shared.app.config import get_settings

app = FastAPI(title='business')


class AddBusinessRequest(BaseModel):
    name: str
    description: str
    category: str
    address: str
    latitude: float
    longitude: float
    ads_keywords: list[str] = Field(default_factory=list)


class ClaimBusinessRequest(BaseModel):
    owner_id: str


@app.on_event('startup')
async def startup() -> None:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.businesses = BusinessListingEngine(app.state.redis)


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.redis.close()


@app.post('/businesses')
async def add_business(payload: AddBusinessRequest) -> dict[str, int]:
    business_id = await app.state.businesses.add_business(**payload.model_dump())
    return {'business_id': business_id}


@app.post('/businesses/{business_id}/claim')
async def claim_business(business_id: int, payload: ClaimBusinessRequest) -> dict[str, bool]:
    claimed = await app.state.businesses.claim_business(business_id=business_id, owner_id=payload.owner_id)
    if not claimed:
        raise HTTPException(status_code=404, detail='Business not found or already claimed')
    return {'claimed': True}


@app.get('/businesses/search')
async def search_businesses(
    query: str = Query(..., min_length=1),
    latitude: float | None = None,
    longitude: float | None = None,
    radius_km: float = Query(10.0, ge=0.1, le=200.0),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict[str, str]]:
    return await app.state.businesses.search_businesses(
        query=query,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        limit=limit,
    )
