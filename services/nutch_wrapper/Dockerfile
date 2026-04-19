FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic redis

COPY services/nutch_wrapper/app /app/services/nutch_wrapper/app

CMD ["uvicorn", "services.nutch_wrapper.app.main:app", "--host", "0.0.0.0", "--port", "8011"]
