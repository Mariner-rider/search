from __future__ import annotations

import hashlib
import html
import json
import re
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from geoip2.database import Reader
from redis.asyncio import Redis

from services.ads.app.engine import AdsEngine
from services.api.app.orchestration import OrchestratorConfig, SearchOrchestrator
from services.shared.app.config import Settings, get_settings
from services.shared.app.es_indices import ensure_optimized_indices
from services.shared.app.logging import get_logger, setup_logging

settings: Settings = get_settings()

app = FastAPI(
    title='api',
    docs_url='/docs' if settings.api_docs_enabled else None,
    redoc_url='/redoc' if settings.api_docs_enabled else None,
    openapi_url='/openapi.json' if settings.api_docs_enabled else None,
)
app.add_middleware(GZipMiddleware, minimum_size=500)
logger = get_logger('search-api')


@app.middleware('http')
async def collect_metrics(request: Request, call_next):
    started = time.perf_counter()
    app.state.metrics['requests_total'] += 1
    client_ip = _detect_ip(request)
    path = request.url.path

    if await _is_lockdown_active() and path not in {'/health', '/metrics'}:
        response = Response(
            content='security lockdown in progress',
            status_code=503,
            media_type='text/plain',
        )
        _logout_response(response)
        response.headers['Retry-After'] = str(settings.security_lockdown_s)
        response.headers['X-Security-Mode'] = 'lockdown'
        _set_security_headers(response)
        return response

    if await _is_suspicious_request(request):
        await _trigger_lockdown(client_ip=client_ip, reason='suspicious-pattern')
        raise HTTPException(status_code=400, detail='malicious payload detected')

    if await _is_rate_limited(client_ip):
        await _trigger_lockdown(client_ip=client_ip, reason='rate-limit')
        raise HTTPException(status_code=429, detail='request rate exceeded')

    try:
        response = await call_next(request)
        if response.status_code >= 500:
            app.state.metrics['errors_total'] += 1
        response.headers['Cache-Control'] = 'public, max-age=60, s-maxage=300, stale-while-revalidate=30'
        response.headers['Vary'] = 'Accept-Encoding'
        _set_security_headers(response)
        return response
    except Exception:
        app.state.metrics['errors_total'] += 1
        logger.exception('Unhandled API error')
        raise
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        app.state.metrics['latency_ms_sum'] += elapsed


@app.on_event('startup')
async def startup() -> None:
    setup_logging(level=settings.log_level, log_file=settings.log_file)

    app.state.metrics = {
        'requests_total': 0,
        'errors_total': 0,
        'latency_ms_sum': 0.0,
        'started_at': datetime.now(UTC).isoformat(),
        'security_events_total': 0,
        'security_lockdowns_total': 0,
    }
    app.state.restart_requested_at = ''

    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.es = AsyncElasticsearch(settings.elasticsearch_url)
    app.state.ads = AdsEngine(app.state.redis)
    app.state.geoip = Reader(settings.geoip_db_path) if Path(settings.geoip_db_path).exists() else None

    await ensure_optimized_indices(app.state.es, ['search_documents', 'images', 'videos', 'news'])

    app.state.orchestrator = SearchOrchestrator(
        es=app.state.es,
        ads=app.state.ads,
        redis=app.state.redis,
        config=OrchestratorConfig(
            search_index_name=settings.search_index_name,
            ads_per_page=settings.ads_per_page,
            yacy_integration_url=settings.yacy_integration_url,
            llm_answer_url=settings.llm_answer_engine_url,
        ),
    )
    logger.info('search-api started')


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.redis.close()
    await app.state.es.close()
    if app.state.geoip is not None:
        app.state.geoip.close()
    logger.info('search-api stopped')


@app.get('/health')
async def health() -> dict[str, str]:
    return {
        'status': 'ok',
        'service': 'search-api',
        'security_lockdown': 'true' if await _is_lockdown_active() else 'false',
        'restart_requested_at': app.state.restart_requested_at,
    }


@app.get('/metrics', response_class=PlainTextResponse)
async def metrics() -> str:
    req = app.state.metrics['requests_total']
    err = app.state.metrics['errors_total']
    lat = app.state.metrics['latency_ms_sum']
    avg = (lat / req) if req else 0.0
    sec_evt = app.state.metrics['security_events_total']
    sec_lock = app.state.metrics['security_lockdowns_total']
    return '\n'.join(
        [
            f'requests_total {req}',
            f'errors_total {err}',
            f'latency_ms_sum {lat:.3f}',
            f'latency_ms_avg {avg:.3f}',
            f'security_events_total {sec_evt}',
            f'security_lockdowns_total {sec_lock}',
        ]
    )


@app.post('/context/consent')
async def set_context_consent(response: Response, enabled: bool = Query(...)) -> dict[str, bool]:
    response.set_cookie(
        key='consent_personalization',
        value='true' if enabled else 'false',
        httponly=True,
        samesite='Lax',
        secure=False,
        max_age=31536000,
    )
    logger.info('context consent updated enabled=%s', enabled)
    return {'consent_personalization': enabled}


@app.get('/search')
async def search(
    request: Request,
    response: Response,
    query: str,
    page: int = 1,
    page_size: int = 10,
    ai_mode: bool = False,
    compact: bool = True,
) -> dict:
    query = _sanitize_input(query)
    consent = request.cookies.get('consent_personalization', 'false') == 'true'
    user_context = _user_context(request=request, consent=consent)
    history_terms = _history_terms(request.cookies.get('search_history', '')) if consent else set()

    orchestrated = await app.state.orchestrator.execute(query=query, page=page, page_size=page_size, ai_mode=ai_mode)
    merged_results = []
    for item in orchestrated['results']:
        base_score = float(item.get('score', 0.0))
        boost = _personalization_boost(
            title=str(item.get('title', '')),
            snippet=str(item.get('snippet', '')),
            url=str(item.get('url', '')),
            history_terms=history_terms,
            country=user_context['geo'].get('country', ''),
        ) if consent else 0.0

        result = {
            'title': _sanitize_output(str(item.get('title', ''))),
            'url': _sanitize_output(str(item.get('url', ''))),
            'snippet': _sanitize_output(str(item.get('snippet', ''))),
            'source': _sanitize_output(str(item.get('source', 'internal'))),
            'timestamp': item.get('timestamp', ''),
            'score': {
                'orchestrated_score': base_score,
                'personalization_boost': boost,
                'final_score': base_score + boost,
            },
        }
        if compact and not result['timestamp']:
            result.pop('timestamp')
        merged_results.append(result)

    merged_results.sort(key=lambda x: x['score']['final_score'], reverse=True)

    if consent:
        _write_history_cookie(response=response, current=query, previous_cookie=request.cookies.get('search_history', ''))

    logger.info('search query=%s results=%d ai_mode=%s', query, len(merged_results), ai_mode)

    payload = {
        'query': query,
        'ai_mode': ai_mode,
        'ai_overview': orchestrated['ai_overview'],
        'results': merged_results,
        'ads': orchestrated['ads'],
        'orchestration': orchestrated['orchestration'],
        'meta': {
            'total_results': str(len(merged_results)),
            'time_taken': f"{orchestrated['orchestration']['latency_ms']}ms",
            'personalization_enabled': consent,
            'gdpr_safe': True,
        },
    }
    if not compact:
        payload['knowledge_panel'] = {'title': '', 'description': '', 'images': [], 'related_topics': []}
        payload['user_context'] = user_context
    return payload


@app.get('/images')
async def images(query: str = Query(..., min_length=1), size: int = Query(20, ge=1, le=100)) -> dict:
    query = _sanitize_input(query)
    response = await app.state.es.search(
        index='images',
        size=size,
        query={'multi_match': {'query': query, 'fields': ['title^2', 'image_url', 'page_url']}},
        track_total_hits=True,
    )
    hits = response.get('hits', {}).get('hits', [])
    return {'query': query, 'results': [{'title': _sanitize_output(str(h.get('_source', {}).get('title', ''))), 'image_url': _sanitize_output(str(h.get('_source', {}).get('image_url', ''))), 'page_url': _sanitize_output(str(h.get('_source', {}).get('page_url', '')))} for h in hits]}


@app.get('/videos')
async def videos(query: str = Query(..., min_length=1), size: int = Query(20, ge=1, le=100)) -> dict:
    query = _sanitize_input(query)
    response = await app.state.es.search(
        index='videos',
        size=size,
        query={'multi_match': {'query': query, 'fields': ['title^2', 'provider', 'video_url', 'page_url']}},
        track_total_hits=True,
    )
    hits = response.get('hits', {}).get('hits', [])
    return {'query': query, 'results': [{'title': _sanitize_output(str(h.get('_source', {}).get('title', ''))), 'video_url': _sanitize_output(str(h.get('_source', {}).get('video_url', ''))), 'provider': _sanitize_output(str(h.get('_source', {}).get('provider', ''))), 'video_id': _sanitize_output(str(h.get('_source', {}).get('video_id', ''))), 'page_url': _sanitize_output(str(h.get('_source', {}).get('page_url', '')))} for h in hits]}


@app.get('/news')
async def news(query: str = Query(..., min_length=1), size: int = Query(20, ge=1, le=100)) -> dict:
    query = _sanitize_input(query)
    response = await app.state.es.search(
        index='news',
        size=size,
        query={
            'function_score': {
                'query': {'multi_match': {'query': query, 'fields': ['title^2', 'snippet', 'content', 'domain']}},
                'field_value_factor': {'field': 'freshness_boost', 'factor': 2.0, 'missing': 0.1},
                'boost_mode': 'sum',
            }
        },
        sort=[{'_score': 'desc'}],
        track_total_hits=True,
    )
    hits = response.get('hits', {}).get('hits', [])
    return {'query': query, 'results': [{'title': _sanitize_output(str(h.get('_source', {}).get('title', ''))), 'url': _sanitize_output(str(h.get('_source', {}).get('url', ''))), 'snippet': _sanitize_output(str(h.get('_source', {}).get('snippet', ''))), 'domain': _sanitize_output(str(h.get('_source', {}).get('domain', ''))), 'freshness_boost': h.get('_source', {}).get('freshness_boost', 0.1)} for h in hits]}


@app.get('/search/stream')
async def search_stream(request: Request, query: str, page: int = 1, page_size: int = 10, ai_mode: bool = True) -> StreamingResponse:
    query = _sanitize_input(query)
    payload = await search(request=request, response=Response(), query=query, page=page, page_size=page_size, ai_mode=ai_mode)

    async def events():
        yield f"event: response\ndata: {json.dumps(payload)}\n\n"
        yield 'event: done\ndata: [DONE]\n\n'

    return StreamingResponse(events(), media_type='text/event-stream')


def _user_context(*, request: Request, consent: bool) -> dict[str, object]:
    ip = _detect_ip(request)
    masked_ip = hashlib.sha256(ip.encode()).hexdigest()[:12] if ip else ''
    geo = _lookup_geo(ip)
    return {
        'consent_personalization': consent,
        'client': {'ip_hash': masked_ip, 'user_agent': request.headers.get('user-agent', '')},
        'geo': geo,
    }


def _sanitize_input(text: str) -> str:
    normalized = unicodedata.normalize('NFKC', text)
    normalized = re.sub(r'[\x00-\x1f\x7f]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    lowered = normalized.lower()
    blocked = ('<script', 'javascript:', 'onerror=', 'onload=', '</', '<iframe', '<img')
    if any(token in lowered for token in blocked):
        raise HTTPException(status_code=400, detail='potential script injection blocked')
    return normalized[:512]


def _sanitize_output(text: str) -> str:
    return html.escape(text, quote=True)


def _set_security_headers(response: Response) -> None:
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-site'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


def _logout_response(response: Response) -> None:
    response.delete_cookie('consent_personalization')
    response.delete_cookie('search_history')


async def _is_rate_limited(client_ip: str) -> bool:
    if not client_ip:
        return False
    key = f'sec:rate:{client_ip}'
    count = await app.state.redis.incr(key)
    if count == 1:
        await app.state.redis.expire(key, settings.security_rate_window_s)
    return count > settings.security_rate_limit


async def _is_lockdown_active() -> bool:
    return bool(await app.state.redis.get('sec:lockdown:active'))


async def _trigger_lockdown(*, client_ip: str, reason: str) -> None:
    app.state.metrics['security_events_total'] += 1
    app.state.metrics['security_lockdowns_total'] += 1
    app.state.restart_requested_at = datetime.now(UTC).isoformat()
    await app.state.redis.set('sec:lockdown:active', reason, ex=settings.security_lockdown_s)
    logger.critical('SECURITY lockdown triggered ip=%s reason=%s restart_requested_at=%s', client_ip, reason, app.state.restart_requested_at)


async def _is_suspicious_request(request: Request) -> bool:
    combined = f'{request.url.path} {request.url.query} {request.headers.get("user-agent", "")}'.lower()
    patterns = ('<script', 'union select', "' or 1=1", '../', 'javascript:', '${jndi')
    return any(p in combined for p in patterns)


def _detect_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '').strip()
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = request.headers.get('x-real-ip', '').strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return request.client.host
    return ''


def _lookup_geo(ip: str) -> dict[str, str]:
    if not ip or app.state.geoip is None:
        return {'country': '', 'city': '', 'timezone': ''}

    try:
        rec = app.state.geoip.city(ip)
        return {'country': rec.country.iso_code or '', 'city': rec.city.name or '', 'timezone': rec.location.time_zone or ''}
    except Exception:
        return {'country': '', 'city': '', 'timezone': ''}


def _history_terms(raw_cookie: str) -> set[str]:
    if not raw_cookie:
        return set()
    try:
        history = json.loads(raw_cookie)
    except json.JSONDecodeError:
        return set()
    terms: set[str] = set()
    for item in history:
        for token in str(item.get('q', '')).lower().split():
            terms.add(token)
    return terms


def _write_history_cookie(*, response: Response, current: str, previous_cookie: str) -> None:
    history = []
    if previous_cookie:
        try:
            history = json.loads(previous_cookie)
        except json.JSONDecodeError:
            history = []
    history.append({'q': current, 'ts': datetime.now(UTC).isoformat()})
    history = history[-20:]
    response.set_cookie(key='search_history', value=json.dumps(history), httponly=True, samesite='Lax', secure=False, max_age=2592000)


def _personalization_boost(*, title: str, snippet: str, url: str, history_terms: set[str], country: str) -> float:
    text = f"{title} {snippet}".lower()
    overlap = len([term for term in history_terms if term and term in text])
    history_boost = min(overlap * 0.05, 0.5)
    geo_boost = 0.1 if country and url.lower().endswith(f'.{country.lower()}') else 0.0
    return history_boost + geo_boost
