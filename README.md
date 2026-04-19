# AI Search Platform

Production-oriented, modular search stack built around FastAPI microservices, Elasticsearch, Redis, optional crawler integrations (YaCy, Nutch, StormCrawler, Scrapy), and a SearXNG frontend.

This README is the **full project guide**: architecture, services, setup, operations, development workflow, and how to safely modify components.

---

## Table of contents

1. [What this project includes](#what-this-project-includes)
2. [Repository map](#repository-map)
3. [Architecture and request flow](#architecture-and-request-flow)
4. [Service catalog](#service-catalog)
5. [Key runtime features](#key-runtime-features)
6. [Prerequisites](#prerequisites)
7. [Environment configuration](#environment-configuration)
8. [Step-by-step setup (local)](#step-by-step-setup-local)
9. [Step-by-step setup (VPS / full platform)](#step-by-step-setup-vps--full-platform)
10. [How to run without Docker (developer mode)](#how-to-run-without-docker-developer-mode)
11. [Operational runbook](#operational-runbook)
12. [API reference (major endpoints)](#api-reference-major-endpoints)
13. [Security model](#security-model)
14. [Performance and caching model](#performance-and-caching-model)
15. [How to modify this project safely](#how-to-modify-this-project-safely)
16. [Troubleshooting](#troubleshooting)

---

## What this project includes

- Multi-service search backend with orchestration and fan-out logic.
- Frontend integration via SearXNG with custom templates/settings.
- Two Docker Compose topologies:
  - `docker-compose.yml` for local/default runtime.
  - `docker-compose.platform.yml` for broader VPS/platform topology.
- Baseline observability (`/health`, `/metrics`) in primary services.
- Security controls:
  - Query sanitization + output escaping.
  - Security headers.
  - Adaptive lock-down behavior with forced cookie invalidation.
- Performance controls:
  - Redis caching for key search paths.
  - GZip compression.
  - CDN-friendly cache headers.

---

## Repository map

### Top-level files

- `README.md` — this guide.
- `INTEGRATION_PLAN.md` — implementation checklist/summary.
- `Makefile` — convenience commands.
- `.env.example`, `.env.local`, `.env.vps` — env presets.
- `docker-compose.yml` — default stack.
- `docker-compose.platform.yml` — expanded platform stack.
- `pyproject.toml` — Python dependencies/project metadata.

### Directories

- `configs/elasticsearch/elasticsearch.yml` — Elasticsearch runtime config.
- `configs/nutch/README.md` — Nutch notes.
- `frontend/searxng/` — SearXNG settings + template overrides.
- `scripts/bootstrap_official_integrations.sh` — clone/update external upstream repos.
- `services/` — all backend microservices.

---

## Architecture and request flow

### Core query flow

1. Client calls `search-api` (`/search`).
2. API orchestrator fans out concurrently to:
   - Elasticsearch internal index
   - YaCy integration (if available)
   - Ads engine
   - LLM answer service (when `ai_mode=true`)
3. Results are merged/ranked.
4. API returns merged payload + ads + orchestration metadata.

### Data / processing flow

- Crawlers or wrappers feed content into processing/indexing paths.
- `processor-service` handles document processing/index write path.
- `dataset` service exposes training-friendly records from indexed content.

---

## Service catalog

### Primary APIs

- `services/api/app/main.py` (`search-api`, port `8000`)
  - Search endpoints (`/search`, `/search/stream`, `/images`, `/videos`, `/news`)
  - Consent endpoint (`/context/consent`)
  - Health/metrics (`/health`, `/metrics`)
  - Security middleware, personalization, user-context handling

- `services/dataset/app/main.py`
  - `/dataset` endpoint
  - Redis cache + GZip + cache/security headers
  - `GET /security/status` for lockdown visibility

### Integration and support services

- `services/api/app/orchestration.py` — query fan-out/merge logic.
- `services/ads/app/engine.py` — ads selection.
- `services/llm_answer/app/main.py` — AI answer generation endpoint.
- `services/yacy_integration/app/main.py` — YaCy bridge.
- `services/crawler_gateway/app/main.py` — crawler routing gateway.
- `services/nutch_wrapper/app/main.py` — Nutch control wrapper.
- `services/storm_wrapper/app/main.py` — StormCrawler control wrapper.
- `services/processor/app/main.py` — ingest/processing service.
- `services/orchestrator/app/main.py` — integration orchestrator service.
- `services/shared/app/config.py` — environment-backed settings model.
- `services/shared/app/es_indices.py` — index optimization/bootstrap.
- `services/shared/app/logging.py` — logging bootstrap utilities.

### Frontend

- `frontend/searxng/settings.yml`
- `frontend/searxng/templates/simple/results.html`

---

## Key runtime features

### 1) GDPR-safe personalization

- Personalization is consent-gated via cookie:
  - `POST /context/consent?enabled=true|false`
- Search history cookie stores recent query terms (bounded).
- User context exposes hashed IP token instead of raw IP.

### 2) Security controls

- Canonicalization + suspicious payload checks on query input.
- Escaping on outbound text fields in search responses.
- Security headers (`CSP`, frame/content/referrer protections).
- Adaptive controls:
  - Per-IP rate limiter.
  - Suspicious payload detector.
  - Temporary lockdown (`503`) with cookie invalidation.
- Docs/OpenAPI hiding toggle via env:
  - `API_DOCS_ENABLED=false` by default.

### 3) Performance controls

- Redis caching for:
  - Internal search results.
  - YaCy bridge responses.
  - Ads blocks.
  - Dataset results.
- GZip compression in API and dataset services.
- CDN-friendly headers (`Cache-Control`, `Vary`).
- Compact mode on `/search` to reduce response payload size.

### 4) Observability

- `GET /health`
- `GET /metrics`
- Security counters included in metrics output.

---

## Prerequisites

### Mandatory

- Docker Engine + Docker Compose plugin (recommended path).
- Git.

### Optional (for local non-Docker development)

- Python 3.11+
- `pip`

---

## Environment configuration

Three templates are provided:

- `.env.example` — baseline keys/defaults.
- `.env.local` — low-resource local settings.
- `.env.vps` — fuller distributed settings.

### Important variables

#### Connectivity

- `REDIS_URL`
- `ELASTICSEARCH_URL`
- `POSTGRES_URL`

#### Search behavior

- `ADS_PER_PAGE`
- `SEARCH_INDEX_NAME`
- `LLM_ANSWER_ENGINE_URL`
- `YACY_INTEGRATION_URL`

#### Runtime/security

- `LOG_LEVEL`
- `LOG_FILE`
- `API_DOCS_ENABLED`
- `SECURITY_RATE_LIMIT`
- `SECURITY_RATE_WINDOW_S`
- `SECURITY_LOCKDOWN_S`

#### Feature toggles (compose-driven)

- `ENABLE_SCRAPY`
- `ENABLE_NUTCH`
- `ENABLE_STORMCRAWLER`
- `ENABLE_YACY`
- `DISTRIBUTED_CRAWLING`

---

## Step-by-step setup (local)

### Step 1 — Clone

```bash
git clone <your-repo-url> search
cd search
```

### Step 2 — Choose env preset

```bash
cp .env.local .env
```

(Or use `.env.example` and customize manually.)

### Step 3 — Start core stack

```bash
docker compose --env-file .env.local up -d
```

### Step 4 — Verify containers

```bash
docker compose ps
```

### Step 5 — Verify endpoints

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/metrics
curl -s 'http://localhost:8000/search?query=hello'
```

### Step 6 — Open frontend

- SearXNG UI: `http://localhost:8080`

### Step 7 — Stop stack

```bash
docker compose --env-file .env.local down
```

---

## Step-by-step setup (VPS / full platform)

### Step 1 — Prepare host

- 4–8GB RAM recommended minimum for expanded profile.
- Install Docker + Compose plugin.

### Step 2 — Clone repo and set env

```bash
git clone <your-repo-url> search
cd search
cp .env.vps .env
```

### Step 3 — Start platform topology

```bash
docker compose --env-file .env.vps -f docker-compose.platform.yml up -d --build
```

### Step 4 — Check service health

```bash
docker compose -f docker-compose.platform.yml ps
```

### Step 5 — Validate key APIs

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8016/healthz
```

### Step 6 — Shutdown

```bash
docker compose --env-file .env.vps -f docker-compose.platform.yml down
```

---

## How to run without Docker (developer mode)

> This mode is best for editing Python services quickly; still requires running Redis/Elasticsearch/Postgres somehow.

### Step 1 — Install editable package

```bash
python -m pip install -e .
```

### Step 2 — Export env vars (example)

```bash
export REDIS_URL=redis://localhost:6379/0
export ELASTICSEARCH_URL=http://localhost:9200
export POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/search
export SEARCH_INDEX_NAME=search_documents
export LLM_ANSWER_ENGINE_URL=http://localhost:8016
export YACY_INTEGRATION_URL=http://localhost:8015
```

### Step 3 — Run API

```bash
uvicorn services.api.app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 4 — Run dataset service

```bash
uvicorn services.dataset.app.main:app --host 0.0.0.0 --port 8017 --reload
```

---

## Operational runbook

### Makefile shortcuts

```bash
make infra-up
make infra-down
make platform-up
make platform-down
make check
make bootstrap-integrations
```

### Bootstrap official upstream repos

```bash
./scripts/bootstrap_official_integrations.sh
```

This clones/updates:

- Apache Nutch
- StormCrawler
- YaCy
- SearXNG
- Haystack
- Qdrant

---

## API reference (major endpoints)

### Search API (port 8000)

- `POST /context/consent?enabled=true|false`
- `GET /search?query=...&page=1&page_size=10&ai_mode=false&compact=true`
- `GET /search/stream?query=...&ai_mode=true`
- `GET /images?query=...`
- `GET /videos?query=...`
- `GET /news?query=...`
- `GET /health`
- `GET /metrics`

### Dataset API

- `GET /dataset?query=...&size=25`
- `GET /security/status`

### Quick examples

```bash
# Enable personalization consent
curl -i -X POST 'http://localhost:8000/context/consent?enabled=true'

# Search
curl -s 'http://localhost:8000/search?query=distributed+systems&ai_mode=true'

# Dataset lookup
curl -s 'http://localhost:8017/dataset?query=nlp&size=10'
```

---

## Security model

- Preventive controls:
  - Query sanitization/canonicalization.
  - Output escaping for key string fields.
  - Defense headers in middleware.
- Adaptive controls:
  - Per-IP rate checks in Redis.
  - Suspicious pattern matching.
  - Lockdown mode with `Retry-After` and cookie invalidation.
- Visibility:
  - Lockdown state in health/security status endpoints.
  - Security counters in metrics.

> Note: no software-only solution can make APIs “invisible” to all browser tooling. This project mitigates exposure by disabling docs/openapi by default and enforcing runtime request controls.

---

## Performance and caching model

- Compression:
  - `GZipMiddleware` in search and dataset APIs.
- Caching:
  - Search fan-out responses and ads cached in Redis with short TTLs.
  - Dataset results cached in Redis.
- Headers:
  - `Cache-Control` + `Vary: Accept-Encoding` for CDN/proxy friendliness.
- Payload minimization:
  - `/search` compact mode defaults to `true`.

---

## How to modify this project safely

### 1) Change one service at a time

- Prefer isolated edits under a single service directory (`services/<name>/app`).
- Keep shared logic in `services/shared` to avoid duplication.

### 2) Add config via `Settings`

- Add env var in `services/shared/app/config.py`.
- Add corresponding entries to:
  - `.env.example`
  - `.env.local`
  - `.env.vps`
  - `docker-compose*.yml` service environment blocks (if needed)

### 3) Update docs with every behavior change

- Update this README.
- Update `INTEGRATION_PLAN.md` when major milestones shift.

### 4) Validate before commit

```bash
python -m compileall services
```

Optional if Docker available:

```bash
docker compose -f docker-compose.yml config
docker compose -f docker-compose.platform.yml config
```

### 5) Common modification playbooks

#### A) Add a new API endpoint

1. Implement route in relevant service (`services/api/app/main.py` etc.).
2. Add middleware/security handling if endpoint accepts user input.
3. Add tests/checks (or at minimum compile check).
4. Document endpoint in README API section.

#### B) Add a new microservice

1. Create `services/<service_name>/app/main.py`.
2. Add `Dockerfile` under `services/<service_name>/`.
3. Register in both compose files (if applicable).
4. Wire dependencies/healthchecks.
5. Document in Service Catalog.

#### C) Tune performance

1. Adjust TTLs or cache keys in service logic.
2. Monitor `/metrics` changes.
3. Validate payload size/latency with representative queries.

#### D) Tune security thresholds

1. Adjust `SECURITY_RATE_LIMIT`, `SECURITY_RATE_WINDOW_S`, `SECURITY_LOCKDOWN_S`.
2. Restart service.
3. Verify `/health`, `/metrics`, and `/security/status` outputs.

---

## Troubleshooting

### Docker compose command not found

Use Docker Compose plugin syntax (`docker compose ...`) instead of legacy `docker-compose ...`.

### Search API failing startup

- Check Redis + Elasticsearch health.
- Confirm env vars are set correctly.
- Inspect logs:

```bash
docker compose logs -f search-api
```

### Elasticsearch memory pressure

- Lower heap in env (`ES_JAVA_OPTS`) for constrained hosts.
- Keep shard/replica counts low for local mode.

### Lockdown unexpectedly active

- Inspect API metrics and logs for security triggers.
- Wait for lockdown TTL to expire or clear Redis key manually.

### GeoIP warnings

- Ensure `GEOIP_DB_PATH` points to a valid mmdb file.
- If absent, API still runs with empty geo fields.

---

## Final notes

- This project intentionally balances modularity, constrained-resource operation, and progressive hardening.
- Start with `.env.local` + `docker-compose.yml` for development.
- Move to `.env.vps` + `docker-compose.platform.yml` for fuller deployments.
