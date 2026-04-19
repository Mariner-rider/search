from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title='crawler-gateway')


class CrawlRequest(BaseModel):
    engine: str = Field(default='nutch', pattern='^(nutch|stormcrawler)$')
    seed_key: str = Field(default='seed_engine:frontier')
    top_n: int = Field(default=100, ge=1, le=5000)


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/crawler/default')
async def crawler_default() -> dict[str, str]:
    return {'default_engine': os.getenv('DEFAULT_CRAWLER_ENGINE', 'nutch')}


@app.post('/crawl/start')
async def crawl_start(payload: CrawlRequest) -> dict[str, object]:
    engine = payload.engine or os.getenv('DEFAULT_CRAWLER_ENGINE', 'nutch')
    async with httpx.AsyncClient(timeout=30.0) as client:
        if engine == 'stormcrawler':
            resp = await client.post(
                'http://storm-wrapper:8013/storm/start',
                json={'seed_key': payload.seed_key, 'top_n': payload.top_n, 'topology': 'crawler'},
            )
        else:
            resp = await client.post(
                'http://nutch-wrapper:8011/crawl/start',
                json={'seed_key': payload.seed_key, 'top_n': payload.top_n, 'crawl_id': 'gateway-run'},
            )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return {'engine': engine, 'result': resp.json()}
