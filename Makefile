.PHONY: infra-up infra-down check format bootstrap-integrations platform-up platform-down build-nutch

infra-up:
	docker compose up -d redis elasticsearch postgres

infra-down:
	docker compose down

bootstrap-integrations:
	./scripts/bootstrap_official_integrations.sh

build-nutch:
	cd services/nutch && ./gradlew clean build

platform-up:
	docker compose -f docker-compose.platform.yml up -d --build

platform-down:
	docker compose -f docker-compose.platform.yml down

check:
	python -m compileall services

format:
	python -m pip install -q ruff
	ruff format services
