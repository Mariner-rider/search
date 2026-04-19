from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.shared.app.es_indices import ensure_optimized_indices

app = FastAPI(title='processor-service')

YOUTUBE_PATTERNS = [
    re.compile(r'https?://(?:www\.)?youtube\.com/watch\?v=([\w-]+)'),
    re.compile(r'https?://youtu\.be/([\w-]+)'),
]
VIMEO_PATTERN = re.compile(r'https?://(?:www\.)?vimeo\.com/(\d+)')
IMG_PATTERN = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
HREF_PATTERN = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)

NEWS_DOMAINS = {
    'reuters.com', 'apnews.com', 'bbc.com', 'cnn.com', 'nytimes.com',
    'washingtonpost.com', 'theguardian.com', 'bloomberg.com',
}


class ProcessRequest(BaseModel):
    path: str = Field(description='Folder containing Nutch JSON outputs')


@app.on_event('startup')
async def startup() -> None:
    es_url = 'http://elasticsearch:9200'
    app.state.es = AsyncElasticsearch(es_url)
    app.state.index = 'search_documents'
    await ensure_optimized_indices(app.state.es, ['search_documents', 'images', 'videos', 'news'])


@app.on_event('shutdown')
async def shutdown() -> None:
    await app.state.es.close()


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/ingest/nutch-json')
async def ingest_nutch_json(payload: ProcessRequest) -> dict[str, int]:
    base = Path(payload.path)
    if not base.exists():
        raise HTTPException(status_code=404, detail='Path not found')

    indexed = 0
    images_indexed = 0
    videos_indexed = 0
    news_indexed = 0

    for file in base.rglob('*.json'):
        for line in file.read_text(errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            url = str(raw.get('url', ''))
            title = str(raw.get('title', ''))
            content = str(raw.get('content', ''))
            snippet = content[:500]
            timestamp = str(raw.get('fetchTime', ''))

            source = {
                'url': url,
                'title': title,
                'snippet': snippet,
                'content': content,
                'timestamp': timestamp,
            }
            await app.state.es.index(index=app.state.index, document=source)
            indexed += 1

            image_docs = _extract_images(url=url, title=title, content=content, timestamp=timestamp)
            for doc in image_docs:
                await app.state.es.index(index='images', document=doc)
                images_indexed += 1

            video_docs = _extract_videos(url=url, title=title, content=content, timestamp=timestamp)
            for doc in video_docs:
                await app.state.es.index(index='videos', document=doc)
                videos_indexed += 1

            if _is_news_domain(url):
                freshness_boost = _freshness_boost(timestamp)
                news_doc = {
                    'url': url,
                    'title': title,
                    'snippet': snippet,
                    'content': content,
                    'timestamp': timestamp,
                    'domain': urlparse(url).netloc.lower(),
                    'freshness_boost': freshness_boost,
                }
                await app.state.es.index(index='news', document=news_doc)
                news_indexed += 1

    return {
        'indexed': indexed,
        'images_indexed': images_indexed,
        'videos_indexed': videos_indexed,
        'news_indexed': news_indexed,
    }


def _extract_images(*, url: str, title: str, content: str, timestamp: str) -> list[dict[str, str]]:
    images = []
    for src in IMG_PATTERN.findall(content):
        images.append(
            {
                'page_url': url,
                'image_url': src,
                'title': title,
                'timestamp': timestamp,
            }
        )
    return images


def _extract_videos(*, url: str, title: str, content: str, timestamp: str) -> list[dict[str, str]]:
    links = set(HREF_PATTERN.findall(content))
    videos: list[dict[str, str]] = []

    for link in links:
        provider, video_id = _video_provider(link)
        if not provider:
            continue
        videos.append(
            {
                'page_url': url,
                'video_url': link,
                'provider': provider,
                'video_id': video_id,
                'title': title,
                'timestamp': timestamp,
            }
        )
    return videos


def _video_provider(link: str) -> tuple[str, str]:
    for pattern in YOUTUBE_PATTERNS:
        match = pattern.search(link)
        if match:
            return 'youtube', match.group(1)

    vimeo = VIMEO_PATTERN.search(link)
    if vimeo:
        return 'vimeo', vimeo.group(1)

    return '', ''


def _is_news_domain(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(domain == d or domain.endswith(f'.{d}') for d in NEWS_DOMAINS)


def _freshness_boost(timestamp: str) -> float:
    try:
        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        now = datetime.now(UTC)
        hours = max((now - dt).total_seconds() / 3600, 0.0)
        return max(0.1, 1.0 / (1.0 + hours / 24.0))
    except ValueError:
        return 0.1
