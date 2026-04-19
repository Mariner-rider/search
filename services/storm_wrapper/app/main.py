from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title='storm-wrapper')

STORMCRAWLER_DIR = Path(os.getenv('STORMCRAWLER_DIR', '/workspace/services/stormcrawler'))
STATUS_FILE = Path(os.getenv('STORM_STATUS_FILE', '/workspace/data/stormcrawler/status.json'))


class StormStartRequest(BaseModel):
    topology: str = Field(default='crawler')
    seed_key: str = Field(default='seed_engine:frontier')
    top_n: int = Field(default=100, ge=1, le=5000)


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/storm/start')
async def storm_start(payload: StormStartRequest) -> dict[str, str]:
    if not STORMCRAWLER_DIR.exists():
        raise HTTPException(status_code=503, detail=f'StormCrawler not found at {STORMCRAWLER_DIR}')

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    start_time = datetime.now(UTC).isoformat()

    # Generates URL seed list from Redis for StormCrawler spout ingestion.
    seed_file = STORMCRAWLER_DIR / 'seeds.txt'
    redis_host = os.getenv('REDIS_HOST', 'redis')
    redis_port = os.getenv('REDIS_PORT', '6379')
    redis_db = os.getenv('REDIS_DB', '0')

    cmd = (
        "python - <<'PY'\n"
        'import redis\n'
        'from pathlib import Path\n'
        f"r=redis.Redis(host='{redis_host}', port={int(redis_port)}, db={int(redis_db)}, decode_responses=True)\n"
        f"urls=r.zrange('{payload.seed_key}',0,{payload.top_n-1})\n"
        f"Path('{seed_file}').write_text('\\n'.join(urls)+'\\n' if urls else '')\n"
        "print(f'Wrote {len(urls)} seeds for StormCrawler')\n"
        'PY'
    )

    process = await asyncio.create_subprocess_shell(cmd)
    await process.communicate()
    if process.returncode != 0:
        raise HTTPException(status_code=500, detail='Failed to create StormCrawler seed file')

    # Expected Storm command once environment is provisioned.
    topology_cmd = f'cd {STORMCRAWLER_DIR} && storm jar external/target/*jar crawl-crawler {payload.topology}'
    STATUS_FILE.write_text(
        '{'
        f'"started_at":"{start_time}",'
        '"state":"submitted",'
        f'"topology":"{payload.topology}",'
        f'"command":"{topology_cmd}"'
        '}'
    )

    return {'status': 'submitted', 'topology': payload.topology, 'command': topology_cmd}


@app.get('/storm/status')
async def storm_status() -> dict[str, str]:
    if not STATUS_FILE.exists():
        return {'state': 'not_started'}
    return {'state': 'submitted', 'details': STATUS_FILE.read_text()}
