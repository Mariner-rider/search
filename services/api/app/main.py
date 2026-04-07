from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI
from redis.asyncio import Redis

from services.ads.app.engine import AdsEngine
from services.shared.app.config import get_settings

app = FastAPI(title='api')


@app.on_event('startup')
async def startup() -> None:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.es = AsyncElasticsearch(settings.elasticsearch_url)
    app.state.ads = AdsEngine(app.state.redis)


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.redis.close()
    await app.state.es.close()


@app.get('/search')
async def search(query: str, page: int = 1, page_size: int = 10) -> dict:
    response = await app.state.es.search(
        index=get_settings().search_index_name,
        query={'multi_match': {'query': query, 'fields': ['title^2', 'content', 'snippet']}},
        from_=(page - 1) * page_size,
        size=page_size,
        track_total_hits=True,
    )
    hits = response.get('hits', {}).get('hits', [])
    ads = await app.state.ads.get_top_ads(query, limit=get_settings().ads_per_page)

    return {
        'query': query,
        'ai_overview': {'summary': '', 'sources': []},
        'results': [
            {
                'title': h.get('_source', {}).get('title', ''),
                'url': h.get('_source', {}).get('url', ''),
                'snippet': h.get('_source', {}).get('snippet', ''),
                'source': '',
                'timestamp': str(h.get('_source', {}).get('timestamp', '')),
                'score': {},
            }
            for h in hits
        ],
        'knowledge_panel': {'title': '', 'description': '', 'images': [], 'related_topics': []},
        'ads': ads,
        'meta': {
            'total_results': str(response.get('hits', {}).get('total', {}).get('value', 0)),
            'time_taken': f"{response.get('took', 0)}ms",
        },
    }
