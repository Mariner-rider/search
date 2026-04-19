FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic httpx elasticsearch

COPY services/yacy_integration/app /app/services/yacy_integration/app

CMD ["uvicorn", "services.yacy_integration.app.main:app", "--host", "0.0.0.0", "--port", "8015"]
