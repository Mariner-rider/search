from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title='nutch-wrapper')

NUTCH_DIR = Path(os.getenv('NUTCH_DIR', '/workspace/services/nutch'))
NUTCH_RUNTIME = NUTCH_DIR / 'runtime' / 'local'
SEED_FILE = NUTCH_RUNTIME / 'urls' / 'seed.txt'


class CrawlStartRequest(BaseModel):
    seed_key: str = Field(default='seed_engine:frontier')
    top_n: int = Field(default=100, ge=1, le=5000)
    crawl_id: str = Field(default='default')


class CrawlStartResponse(BaseModel):
    started: bool
    crawl_id: str
    command: str


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/crawl/start', response_model=CrawlStartResponse)
async def crawl_start(payload: CrawlStartRequest) -> CrawlStartResponse:
    if not NUTCH_DIR.exists():
        raise HTTPException(status_code=503, detail=f'Nutch not found at {NUTCH_DIR}')

    crawl_script = NUTCH_RUNTIME / 'bin' / 'crawl'
    if not crawl_script.exists():
        raise HTTPException(status_code=503, detail=f'Nutch crawl script not found: {crawl_script}')

    SEED_FILE.parent.mkdir(parents=True, exist_ok=True)

    redis_host = os.getenv('REDIS_HOST', 'redis')
    redis_port = os.getenv('REDIS_PORT', '6379')
    redis_db = os.getenv('REDIS_DB', '0')

    fetch_cmd = (
        "python - <<'PY'\n"
        'import os\n'
        'import redis\n'
        'from pathlib import Path\n'
        f"r=redis.Redis(host='{redis_host}', port={int(redis_port)}, db={int(redis_db)}, decode_responses=True)\n"
        f"urls=r.zrange('{payload.seed_key}',0,{payload.top_n-1})\n"
        f"Path('{SEED_FILE}').write_text('\\n'.join(urls)+'\\n' if urls else '')\n"
        "print(f'Wrote {len(urls)} seeds')\n"
        'PY'
    )

    crawl_depth = int(os.getenv('NUTCH_CRAWL_DEPTH', '2'))
    threads = int(os.getenv('NUTCH_FETCH_THREADS', '10'))
    output = os.getenv('NUTCH_JSON_OUTPUT', '/workspace/data/nutch-json')

    crawl_cmd = f"{shlex.quote(str(crawl_script))} {shlex.quote(str(SEED_FILE))} -depth {crawl_depth} -threads {threads} -topN {payload.top_n}"
    export_cmd = (
        f"{shlex.quote(str(NUTCH_RUNTIME / 'bin' / 'nutch'))}"
        f" readseg -dump {shlex.quote(output)}/$(date +%s)"
    )

    full_cmd = f"{fetch_cmd} && {crawl_cmd} && {export_cmd}"

    process = await asyncio.create_subprocess_shell(full_cmd)
    await process.communicate()
    if process.returncode != 0:
        raise HTTPException(status_code=500, detail='Nutch crawl failed')

    return CrawlStartResponse(started=True, crawl_id=payload.crawl_id, command=full_cmd)
