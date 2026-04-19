from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title='integration-orchestrator')


class DependencyMap(BaseModel):
    search_api: str
    images_api: str
    videos_api: str
    news_api: str
    llm_answer_api: str
    crawler_gateway_api: str
    nutch_api: str
    storm_api: str
    processor_api: str
    yacy_node_api: str
    yacy_integration_api: str
    frontend_url: str
    multimodal_vector_api: str
    pipeline: str


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/dependencies', response_model=DependencyMap)
async def dependencies() -> DependencyMap:
    return DependencyMap(
        search_api='http://search-api:8000/search',
        images_api='http://search-api:8000/images',
        videos_api='http://search-api:8000/videos',
        news_api='http://search-api:8000/news',
        llm_answer_api='http://llm-answer:8016',
        crawler_gateway_api='http://crawler-gateway:8014',
        nutch_api='http://nutch-wrapper:8011',
        storm_api='http://storm-wrapper:8013',
        processor_api='http://processor-service:8012',
        yacy_node_api='http://yacy:8090',
        yacy_integration_api='http://yacy-integration:8015',
        frontend_url='http://searxng:8080',
        multimodal_vector_api='http://qdrant:6333',
        pipeline='crawl -> processor(images/videos/news) -> search-api tabs',
    )
