import asyncio
from typing import Awaitable

from app.internal.indexers.abstract import SessionContainer
from app.internal.indexers.indexer_util import get_indexer_contexts
from app.internal.models import Audiobook, ProwlarrSource
from app.util.log import logger


async def edit_source_metadata(
    book: Audiobook,
    sources: list[ProwlarrSource],
    container: SessionContainer,
):
    contexts = await get_indexer_contexts(container)

    coros: list[Awaitable[None]] = [
        context.indexer.setup(book, container, context.valued) for context in contexts
    ]
    exceptions = await asyncio.gather(*coros, return_exceptions=True)
    for exc in exceptions:
        if exc:
            logger.error("Failed to setup indexer", error=str(exc))

    # Helper function to find matching indexer for a source
    async def find_matching_indexer(
        source: ProwlarrSource,
    ) -> tuple[ProwlarrSource, type] | None:
        for context in contexts:
            try:
                if await context.indexer.is_matching_source(source, container):
                    return (source, context)
            except Exception as e:
                logger.error(
                    f"Failed to check if source matches indexer",
                    error=str(e),
                    indexer=type(context.indexer).__name__
                )
        return None

    # Execute all source-indexer matches in parallel
    match_tasks = [find_matching_indexer(source) for source in sources]
    matches = await asyncio.gather(*match_tasks, return_exceptions=True)

    # Build metadata edit tasks from successful matches
    coros = []
    for match in matches:
        if match and not isinstance(match, Exception):
            source, context = match
            coros.append(context.indexer.edit_source_metadata(source, container))

    exceptions = await asyncio.gather(*coros, return_exceptions=True)
    for exc in exceptions:
        if exc:
            logger.error("Failed to edit source metadata", error=str(exc))
