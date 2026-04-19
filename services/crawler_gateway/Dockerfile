FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic httpx

COPY services/crawler_gateway/app /app/services/crawler_gateway/app

CMD ["uvicorn", "services.crawler_gateway.app.main:app", "--host", "0.0.0.0", "--port", "8014"]
