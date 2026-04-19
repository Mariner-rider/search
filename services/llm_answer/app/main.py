from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(title='llm-answer-engine')


class ContextDoc(BaseModel):
    title: str
    url: str
    snippet: str


class AnswerRequest(BaseModel):
    query: str
    contexts: list[ContextDoc] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    answer: str
    sources: list[str]


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/answer', response_model=AnswerResponse)
async def answer(payload: AnswerRequest) -> AnswerResponse:
    if os.getenv('AI_PROVIDER', 'local') == 'api':
        return await _answer_via_api(payload)
    return _answer_local(payload)


@app.post('/answer/stream')
async def answer_stream(payload: AnswerRequest) -> StreamingResponse:
    result = await answer(payload)

    async def events() -> AsyncIterator[str]:
        for token in result.answer.split():
            yield f"event: token\ndata: {token}\n\n"
            await asyncio.sleep(0.01)
        yield f"event: sources\ndata: {','.join(result.sources)}\n\n"
        yield 'event: done\ndata: [DONE]\n\n'

    return StreamingResponse(events(), media_type='text/event-stream')


def _answer_local(payload: AnswerRequest) -> AnswerResponse:
    top_contexts = payload.contexts[:5]
    if not top_contexts:
        return AnswerResponse(answer='No supporting results found for this query.', sources=[])

    bullets = '; '.join(f"{doc.title}: {doc.snippet[:140]}" for doc in top_contexts)
    answer_text = f"Based on top search results for '{payload.query}', key points are: {bullets}."
    sources = [doc.url for doc in top_contexts if doc.url]
    return AnswerResponse(answer=answer_text, sources=sources)


async def _answer_via_api(payload: AnswerRequest) -> AnswerResponse:
    api_url = os.getenv('LLM_API_URL', '').rstrip('/')
    api_key = os.getenv('LLM_API_KEY', '')
    model = os.getenv('LLM_MODEL', 'gpt-4o-mini')

    if not api_url:
        raise HTTPException(status_code=500, detail='LLM_API_URL is required when AI_PROVIDER=api')

    prompt_context = '\n'.join(f"- {c.title} ({c.url}): {c.snippet}" for c in payload.contexts[:8])
    user_prompt = (
        f"Query: {payload.query}\n"
        f"Sources:\n{prompt_context}\n\n"
        'Return a concise factual answer and keep references grounded in sources.'
    )

    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': 'You are a search answer assistant. Use only provided sources.'},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': 0.2,
        'stream': False,
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(f'{api_url}/v1/chat/completions', headers=headers, json=body)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f'LLM provider error: {resp.text}')
        data = resp.json()

    answer_text = (
        data.get('choices', [{}])[0]
        .get('message', {})
        .get('content', '')
    )
    sources = [c.url for c in payload.contexts if c.url]
    return AnswerResponse(answer=answer_text, sources=sources)
