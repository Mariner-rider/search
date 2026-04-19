from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = Field(default='redis://localhost:6379/0', alias='REDIS_URL')
    elasticsearch_url: str = Field(default='http://localhost:9200', alias='ELASTICSEARCH_URL')
    postgres_url: str = Field(default='postgresql://postgres:postgres@localhost:5432/search', alias='POSTGRES_URL')

    ads_per_page: int = Field(default=3, alias='ADS_PER_PAGE')
    search_index_name: str = Field(default='search_documents', alias='SEARCH_INDEX_NAME')

    llm_answer_engine_url: str = Field(default='http://llm-answer:8016', alias='LLM_ANSWER_ENGINE_URL')
    yacy_integration_url: str = Field(default='http://yacy-integration:8015', alias='YACY_INTEGRATION_URL')
    geoip_db_path: str = Field(default='/workspace/data/GeoLite2-City.mmdb', alias='GEOIP_DB_PATH')
    log_level: str = Field(default='INFO', alias='LOG_LEVEL')
    log_file: str = Field(default='logs/search-api.log', alias='LOG_FILE')
    api_docs_enabled: bool = Field(default=False, alias='API_DOCS_ENABLED')
    security_rate_limit: int = Field(default=120, alias='SECURITY_RATE_LIMIT')
    security_rate_window_s: int = Field(default=60, alias='SECURITY_RATE_WINDOW_S')
    security_lockdown_s: int = Field(default=180, alias='SECURITY_LOCKDOWN_S')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
