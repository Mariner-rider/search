from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = Field(default='redis://localhost:6379/0', alias='REDIS_URL')
    elasticsearch_url: str = Field(default='http://localhost:9200', alias='ELASTICSEARCH_URL')
    postgres_url: str = Field(default='postgresql://postgres:postgres@localhost:5432/search', alias='POSTGRES_URL')

    ads_per_page: int = Field(default=3, alias='ADS_PER_PAGE')
    search_index_name: str = Field(default='search_documents', alias='SEARCH_INDEX_NAME')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
