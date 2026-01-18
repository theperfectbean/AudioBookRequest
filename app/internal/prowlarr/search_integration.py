import asyncio
import re
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlencode

import aiohttp
from pydantic import BaseModel
from sqlmodel import Session

from app.internal.prowlarr.prowlarr import _ProwlarrSearchResult  # pyright: ignore[reportPrivateUsage]
from app.internal.prowlarr.util import prowlarr_config
from app.util.cache import SimpleCache
from app.util.log import logger

# Cache for search results with TTL - using SimpleCache for thread safety
search_result_cache: SimpleCache[list['ProwlarrSearchResult'], str] = SimpleCache()

class ProwlarrSearchResult(BaseModel):
    """Enhanced search result with availability information"""
    title: str
    author: str
    narrator: str
    seeders: int
    size: int
    freeleech: bool
    guid: str
    indexer_id: int
    indexer: str
    publish_date: datetime

def _parse_mam_title(title: str) -> tuple[str, str, str]:
    """
    Parse MAM title to extract title, author, and narrator.
    MAM titles often follow formats like:
    "Book Title - Author Name - Narrator Name"
    "Author Name - Book Title"
    "Book Title by Author Name [Tags]"
    """
    # Strip tags like [ENG / MP4] or (2024)
    clean_title = re.sub(r'\[.*?\]', '', title)
    clean_title = re.sub(r'\(.*?\)', '', clean_title).strip()
    
    book_title = clean_title
    author = "Unknown"
    narrator = "Unknown"

    if " - " in clean_title:
        parts = clean_title.split(" - ")
        if len(parts) >= 3:
            book_title = parts[0].strip()
            author = parts[1].strip()
            narrator = parts[2].strip()
        elif len(parts) == 2:
            first_part = parts[0].strip()
            second_part = parts[1].strip()
            # Simple heuristic: if first part has spaces and second doesn't look like it has multiple words
            # it's likely "Author - Title"
            if " " in first_part and len(second_part.split()) <= 1:
                author = first_part
                book_title = second_part
            else:
                book_title = first_part
                author = second_part
    elif " by " in clean_title.lower():
        parts = re.split(r' by ', clean_title, flags=re.IGNORECASE)
        book_title = parts[0].strip()
        author = parts[1].strip()
        
    return book_title, author, narrator

async def search_prowlarr_available(
    session: Session,
    client_session: aiohttp.ClientSession,
    query: str,
    categories: Optional[List[int]] = None,
    indexer_ids: Optional[List[int]] = None,
    limit: int = 100,
) -> List[ProwlarrSearchResult]:
    """
    Search Prowlarr for available audiobooks.
    
    Args:
        session: Database session
        client_session: HTTP client session
        query: Search query (title/author/narrator)
        categories: List of category IDs to search in (default: [3030, 13] for audiobooks)
        indexer_ids: List of indexer IDs to search in (default: all enabled)
        limit: Maximum number of results to return
    
    Returns:
        List of available audiobooks with availability information
    """
    # Check cache first
    cache_key = f"{query}:{categories}:{indexer_ids}:{limit}"
    
    # Check cache with thread-safe SimpleCache
    ttl = prowlarr_config.get_source_ttl(session)
    cached_results = search_result_cache.get(ttl, cache_key)
    if cached_results is not None:
        logger.debug("Using cached Prowlarr search results", query=query)
        return cached_results
    
    # Get Prowlarr configuration
    base_url = prowlarr_config.get_base_url(session)
    api_key = prowlarr_config.get_api_key(session)
    
    if not base_url or not api_key:
        logger.warning("Prowlarr not configured, skipping availability search")
        return []
    
    # Set default category to audiobooks if not specified
    if categories is None:
        categories = [3030, 13]
    
    # Prepare search parameters
    params = {
        "query": query,
        "type": "search",
        "limit": limit,
        "offset": 0,
    }
    
    if categories:
        params["categories"] = categories
    
    if indexer_ids:
        params["indexerIds"] = indexer_ids
    
    # Build URL
    search_url = f"{base_url.rstrip('/')}/api/v1/search?{urlencode(params, doseq=True)}"
    logger.info("Searching Prowlarr for available audiobooks", url=search_url)
    
    try:
        # Execute search
        async with client_session.get(
            search_url,
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json"
            },
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if not response.ok:
                logger.error(
                    "Failed to search Prowlarr",
                    status=response.status,
                    reason=response.reason
                )
                return []
            
            # Parse response
            search_results = _ProwlarrSearchResult.validate_python(await response.json())
            
    except asyncio.TimeoutError:
        logger.error("Prowlarr search timed out", query=query)
        return []
    except Exception as e:
        logger.error("Failed to search Prowlarr", error=str(e))
        return []
    
    # Process results
    available_books: List[ProwlarrSearchResult] = []
    
    for result in search_results:
        try:
            # Skip non-torrent results for now
            if result.protocol != "torrent":
                continue
            
            # Extract author and narrator from title with improved parsing
            title, author, narrator = _parse_mam_title(result.title)
            
            # Check for freeleech
            freeleech = "freeleech" in [flag.lower() for flag in result.indexerFlags]
            
            # Create enhanced result
            enhanced_result = ProwlarrSearchResult(
                title=title,
                author=author,
                narrator=narrator,
                seeders=result.seeders,
                size=result.size,
                freeleech=freeleech,
                guid=result.guid,
                indexer_id=result.indexerId,
                indexer=result.indexer,
                publish_date=datetime.fromisoformat(result.publishDate),
            )
            
            available_books.append(enhanced_result)
            
        except Exception as e:
            logger.warning("Failed to process Prowlarr result", error=str(e), result=result)
            continue
    
    # Cache results using thread-safe SimpleCache
    search_result_cache.set(available_books, cache_key)
    
    logger.info(
        "Prowlarr search completed",
        query=query,
        results_count=len(available_books)
    )
    
    return available_books
