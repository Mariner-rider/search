# Production Integration Plan (Official Upstream Components)

## Step 1 (implemented)

- Crawler engines:
  - Apache Nutch (`services/nutch`)
  - StormCrawler (`services/stormcrawler`)
- Crawler wrappers + toggle gateway added.
- YaCy integrated with merge API `/yacy/search`.
- SearXNG frontend integrated with backend-only engine.
- LLM answer engine integrated with ai_mode + streaming.
- Extended crawler/indexer integrated (images/videos/news).
- Search orchestration layer integrated:
  - parallel fan-out to Elasticsearch, YaCy, Ads, LLM
  - merge + structured response contract
  - per-upstream timeouts for low latency
- User context system integrated:
  - IP detection from request headers
  - GeoIP lookup (GeoLite2 DB)
  - consent-gated search history cookie
  - personalization score boost
  - GDPR-safe response design

## Next step

- Add signed/encrypted cookies and explicit retention controls.
- Add A/B tests for personalization impact and fairness checks.

- Dev/VPS optimization baseline:
  - resource limits, health checks, restart policy, log rotation
  - tuned ES heap and single-worker API defaults for 4GB–8GB hosts

- Environment presets added:
  - `.env.local` (Scrapy-only, low-memory local mode)
  - `.env.vps` (distributed crawling + YaCy + higher ES heap)

- Elasticsearch low-resource tuning applied:
  - 512MB heap local baseline
  - single shard, zero replicas, compression enabled

- Central logging + monitoring added:
  - stdout + rotating file logs
  - `/health` and `/metrics` endpoints for debugging

- Redis caching + gzip + CDN headers enabled for faster responses and lower payload overhead.
