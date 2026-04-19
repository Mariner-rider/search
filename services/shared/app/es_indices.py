from __future__ import annotations

from elasticsearch import AsyncElasticsearch

OPTIMIZED_INDEX_SETTINGS = {
    'number_of_shards': 1,
    'number_of_replicas': 0,
    'codec': 'best_compression',
    'refresh_interval': '5s',
}


async def ensure_optimized_indices(es: AsyncElasticsearch, indices: list[str]) -> None:
    template_name = 'bsearch-low-resource-template'
    await es.indices.put_index_template(
        name=template_name,
        index_patterns=['search_documents', 'images', 'videos', 'news'],
        template={
            'settings': OPTIMIZED_INDEX_SETTINGS,
        },
        priority=500,
    )

    for name in indices:
        exists = await es.indices.exists(index=name)
        if not exists:
            await es.indices.create(index=name, settings=OPTIMIZED_INDEX_SETTINGS)
