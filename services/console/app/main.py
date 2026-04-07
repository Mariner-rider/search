from __future__ import annotations

import secrets
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from services.console.app.storage import init_db
from services.shared.app.config import get_settings

app = FastAPI(title='console')


class RegisterSiteRequest(BaseModel):
    user_id: str
    domain: str


class RegisterSiteResponse(BaseModel):
    site_id: int
    domain: str
    verification_file_path: str
    meta_tag: str


class VerifySiteResponse(BaseModel):
    verified: bool
    method: str | None = None


class SubmitSitemapRequest(BaseModel):
    sitemap_url: str


class InspectURLRequest(BaseModel):
    url: str


class InspectionResponse(BaseModel):
    indexed_status: bool
    last_crawled: str
    issues: list[str]


class AnalyticsEventRequest(BaseModel):
    url: str
    event_type: str = Field(pattern='^(click|impression)$')


class AnalyticsSummary(BaseModel):
    clicks: int
    impressions: int
    ctr: float


@app.on_event('startup')
async def startup() -> None:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.db = await asyncpg.create_pool(settings.postgres_url, min_size=1, max_size=5)
    await init_db(app.state.db)


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.redis.close()
    await app.state.db.close()


@app.post('/console/sites/register', response_model=RegisterSiteResponse)
async def register_site(payload: RegisterSiteRequest) -> RegisterSiteResponse:
    token = secrets.token_hex(8)
    normalized_domain = _normalize_domain(payload.domain)

    async with app.state.db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO console_sites(user_id, domain, verification_token)
            VALUES($1, $2, $3)
            ON CONFLICT (domain) DO UPDATE SET user_id = EXCLUDED.user_id
            RETURNING id, domain, verification_token
            """,
            payload.user_id,
            normalized_domain,
            token,
        )

    file_name = f'bs_verify_{row["verification_token"]}.html'
    return RegisterSiteResponse(
        site_id=int(row['id']),
        domain=str(row['domain']),
        verification_file_path=f'https://{row["domain"]}/{file_name}',
        meta_tag=f'<meta name="bsearch-verification" content="{row["verification_token"]}" />',
    )


@app.post('/console/sites/{site_id}/verify', response_model=VerifySiteResponse)
async def verify_site(site_id: int) -> VerifySiteResponse:
    async with app.state.db.acquire() as conn:
        site = await conn.fetchrow('SELECT id, domain, verification_token FROM console_sites WHERE id = $1', site_id)
    if not site:
        raise HTTPException(status_code=404, detail='site not found')

    domain = str(site['domain'])
    token = str(site['verification_token'])
    file_url = f'https://{domain}/bs_verify_{token}.html'

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        file_ok = await _check_html_file(client, file_url, token)
        meta_ok = await _check_meta_tag(client, f'https://{domain}', token)

    if not file_ok and not meta_ok:
        return VerifySiteResponse(verified=False, method=None)

    method = 'html_file' if file_ok else 'meta_tag'
    async with app.state.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE console_sites
            SET verification_method = $2, verified_at = NOW()
            WHERE id = $1
            """,
            site_id,
            method,
        )

    return VerifySiteResponse(verified=True, method=method)


@app.post('/console/sites/{site_id}/sitemap')
async def submit_sitemap(site_id: int, payload: SubmitSitemapRequest) -> dict[str, int]:
    _ = await _require_verified_site(site_id)
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        response = await client.get(payload.sitemap_url)
        response.raise_for_status()

    urls = _extract_urls_from_sitemap(response.text)

    inserted = 0
    async with app.state.redis.pipeline(transaction=False) as pipe:
        for url in urls:
            canonical = _canonicalize_url(url)
            meta_key = f'seed_engine:meta:{canonical}'
            pipe.sadd('seed_engine:seen', canonical)
            pipe.hsetnx(meta_key, 'pagerank_score', 0.0)
            pipe.hsetnx(meta_key, 'last_crawled', 0.0)
            pipe.hsetnx(meta_key, 'domain_authority', 0.0)
            pipe.zadd('seed_engine:frontier', {canonical: 0.0}, nx=True)
            inserted += 1
        await pipe.execute()

    return {'queued_urls': inserted}


@app.post('/console/sites/{site_id}/inspect', response_model=InspectionResponse)
async def inspect_url(site_id: int, payload: InspectURLRequest) -> InspectionResponse:
    _ = await _require_verified_site(site_id)
    url = _canonicalize_url(payload.url)

    meta_key = f'seed_engine:meta:{url}'
    values = await app.state.redis.hmget(meta_key, 'last_crawled')
    last_crawled = float(values[0]) if values and values[0] else 0.0
    indexed_status = last_crawled > 0.0

    issues: list[str] = []
    if not indexed_status:
        issues.append('URL not crawled/indexed yet')

    async with app.state.db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO console_inspections(site_id, url, indexed_status, last_crawled, issues)
            VALUES($1, $2, $3, $4, $5)
            ON CONFLICT (site_id, url)
            DO UPDATE SET indexed_status=EXCLUDED.indexed_status,
                          last_crawled=EXCLUDED.last_crawled,
                          issues=EXCLUDED.issues,
                          inspected_at=NOW()
            """,
            site_id,
            url,
            indexed_status,
            last_crawled,
            '; '.join(issues),
        )

    return InspectionResponse(
        indexed_status=indexed_status,
        last_crawled=str(last_crawled),
        issues=issues,
    )


@app.post('/console/sites/{site_id}/analytics/event')
async def track_event(site_id: int, payload: AnalyticsEventRequest) -> dict[str, str]:
    _ = await _require_verified_site(site_id)
    async with app.state.db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO console_analytics_events(site_id, url, event_type)
            VALUES($1, $2, $3)
            """,
            site_id,
            _canonicalize_url(payload.url),
            payload.event_type,
        )
    return {'status': 'ok'}


@app.get('/console/sites/{site_id}/analytics', response_model=AnalyticsSummary)
async def analytics(site_id: int) -> AnalyticsSummary:
    _ = await _require_verified_site(site_id)

    async with app.state.db.acquire() as conn:
        clicks = await conn.fetchval(
            "SELECT COUNT(*) FROM console_analytics_events WHERE site_id = $1 AND event_type = 'click'",
            site_id,
        )
        impressions = await conn.fetchval(
            "SELECT COUNT(*) FROM console_analytics_events WHERE site_id = $1 AND event_type = 'impression'",
            site_id,
        )

    clicks_int = int(clicks or 0)
    impressions_int = int(impressions or 0)
    ctr = (clicks_int / impressions_int) if impressions_int > 0 else 0.0
    return AnalyticsSummary(clicks=clicks_int, impressions=impressions_int, ctr=ctr)


async def _require_verified_site(site_id: int) -> dict[str, object]:
    async with app.state.db.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT id, domain, verified_at FROM console_sites WHERE id = $1',
            site_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail='site not found')
    if row['verified_at'] is None:
        raise HTTPException(status_code=400, detail='site not verified')
    return dict(row)


async def _check_html_file(client: httpx.AsyncClient, file_url: str, token: str) -> bool:
    try:
        response = await client.get(file_url)
        if response.status_code != 200:
            return False
        return token in response.text
    except httpx.HTTPError:
        return False


async def _check_meta_tag(client: httpx.AsyncClient, base_url: str, token: str) -> bool:
    try:
        response = await client.get(base_url)
        if response.status_code != 200:
            return False
        expected = f'<meta name="bsearch-verification" content="{token}"'
        return expected in response.text
    except httpx.HTTPError:
        return False


def _extract_urls_from_sitemap(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    namespace = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    urls = [node.text for node in root.findall('.//sm:url/sm:loc', namespace) if node.text]
    if urls:
        return urls
    return [node.text for node in root.findall('.//loc') if node.text]


def _normalize_domain(domain: str) -> str:
    parsed = urlparse(domain if domain.startswith('http') else f'https://{domain}')
    return parsed.netloc.lower()


def _canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or 'https'
    path = parsed.path or '/'
    return f'{scheme}://{parsed.netloc.lower()}{path}'
