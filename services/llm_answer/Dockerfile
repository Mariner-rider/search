FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic httpx

COPY services/llm_answer/app /app/services/llm_answer/app

CMD ["uvicorn", "services.llm_answer.app.main:app", "--host", "0.0.0.0", "--port", "8016"]
