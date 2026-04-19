FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic redis

COPY services/storm_wrapper/app /app/services/storm_wrapper/app

CMD ["uvicorn", "services.storm_wrapper.app.main:app", "--host", "0.0.0.0", "--port", "8013"]
