FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic elasticsearch

COPY services/processor/app /app/services/processor/app

CMD ["uvicorn", "services.processor.app.main:app", "--host", "0.0.0.0", "--port", "8012"]
