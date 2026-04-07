from fastapi import FastAPI
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from services.ads.app.engine import AdsEngine
from services.shared.app.config import get_settings

app = FastAPI(title='ads')


class CreateAdRequest(BaseModel):
    keyword: str
    bid: float = Field(gt=0)
    budget: float = Field(ge=0)
    title: str
    url: str


@app.on_event('startup')
async def startup() -> None:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.ads = AdsEngine(app.state.redis)


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.redis.close()


@app.post('/ads')
async def create_ad(payload: CreateAdRequest) -> dict[str, int]:
    ad_id = await app.state.ads.store_ad(**payload.model_dump())
    return {'ad_id': ad_id}


@app.get('/ads/auction')
async def auction(query: str, limit: int = 3) -> list[dict[str, str]]:
    return await app.state.ads.get_top_ads(query, limit=limit)
