# Nutch integration config

When `services/nutch` is available, configure Nutch to:

1. Read seed URLs from Redis seed engine (`seed_engine:frontier`) via wrapper service.
2. Run crawl job from generated `runtime/local/urls/seed.txt`.
3. Export crawl results into JSON-like dump folders under `/workspace/data/nutch-json`.

The wrapper endpoint `/crawl/start` handles seed extraction + job trigger.
