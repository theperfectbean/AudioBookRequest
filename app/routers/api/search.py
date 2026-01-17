import asyncio
import hashlib
import re
import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional, List
from aiohttp import ClientSession
from fastapi import APIRouter, Depends, HTTPException, Query, Security
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.internal import book_search
from app.internal.auth.authentication import APIKeyAuth, DetailedUser
from app.internal.book_search import (
    audible_region_type,
    audible_regions,
    clear_old_book_caches,
    get_region_from_settings,
    list_audible_books,
    list_popular_books,
    get_book_by_asin,
)
from app.internal.models import Audiobook, AudiobookRequest, AudiobookSearchResult
from app.internal.prowlarr.search_integration import (
    search_prowlarr_available,
    ProwlarrSearchResult,
)
from app.internal.prowlarr.util import verify_match, verify_match_relaxed, normalize_text
from app.internal.metadata.google_books import google_books_provider
from app.internal.env_settings import Settings
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.author_matcher import rank_search_results
from app.internal.models import RankedAudiobookSearchResult
from app.util.cache import SimpleCache

router = APIRouter(prefix="/search", tags=["Search"])

# Cache for virtual book upgrade attempts: (real_asin or None, virtual_asin)
upgrade_attempt_cache: SimpleCache[Optional[str], str] = SimpleCache()

# Cache for ranking results: (ranked_results, cache_key)
ranking_cache: SimpleCache[list[RankedAudiobookSearchResult], str] = SimpleCache()


@asynccontextmanager
async def timing_context(operation: str):
    """Context manager for timing operations"""
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        logger.info(f"⏱️ {operation} took {duration:.2f}s")


def generate_virtual_asin(title: str, author: str) -> str:
    """
    Generate a deterministic, short ASIN for virtual books.
    Same book from different indexers will get the same ASIN.
    """
    # Normalize to avoid duplicates from slight variations
    norm_title = normalize_text(title, primary_only=True)[:50]
    norm_author = normalize_text(author)[:30]
    
    # Create stable hash from normalized metadata
    hash_input = f"{norm_title}:{norm_author}"
    stable_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:11]
    
    return f"VIRTUAL-{stable_hash}"


def generate_ranking_cache_key(books: list[Audiobook], query: str, settings) -> str:
    """Generate stable cache key for ranking results"""
    import json
    from hashlib import sha256

    # Use ASINs (sorted) + query + settings hash
    asin_hash = sha256(
        json.dumps([b.asin for b in books], sort_keys=True).encode()
    ).hexdigest()[:16]
    query_hash = sha256(query.encode()).hexdigest()[:16]
    settings_hash = f"{settings.app.author_match_threshold}_{settings.app.enable_secondary_scoring}"
    return f"{query_hash}:{asin_hash}:{settings_hash}"


def extract_asin_from_prowlarr(p_result: ProwlarrSearchResult) -> Optional[str]:
    """
    Try to extract Audible ASIN from Prowlarr metadata.
    Some indexers include ASIN in GUID, description, or info URL.
    """
    # Pattern for Audible ASIN: B followed by 9 alphanumeric characters
    asin_pattern = r'B[A-Z0-9]{9}'
    
    # Check GUID
    if p_result.guid:
        match = re.search(asin_pattern, p_result.guid)
        if match:
            logger.info(f"🎯 Found ASIN in GUID: {match.group(0)}")
            return match.group(0)
    
    # Check description/comments
    if hasattr(p_result, 'description') and p_result.description:
        match = re.search(asin_pattern, p_result.description)
        if match:
            logger.info(f"🎯 Found ASIN in description: {match.group(0)}")
            return match.group(0)
    
    # Check info URL for Audible link
    if hasattr(p_result, 'info_url') and p_result.info_url:
        # Match: https://www.audible.com/pd/*/B002V00TOO
        url_match = re.search(r'audible\.com/pd/[^/]+/(' + asin_pattern + ')', p_result.info_url)
        if url_match:
            logger.info(f"🎯 Found ASIN in URL: {url_match.group(1)}")
            return url_match.group(1)
    
    return None


async def upgrade_virtual_book_if_better_match(
    session: Session,
    client_session: ClientSession,
    p_result: ProwlarrSearchResult,
    existing_book: Audiobook,
    region: str,
) -> Optional[Audiobook]:
    """
    Check if an existing virtual book can be upgraded to a real Audible book.
    This handles the case where a virtual book was created but the real book exists.
    """
    # Only check virtual books
    if not existing_book.asin.startswith("VIRTUAL-"):
        return None
    
    logger.info(
        f"🔄 Checking for upgrade of virtual book: '{existing_book.title}' | "
        f"ASIN: {existing_book.asin}"
    )
    
    # Step 1: Try ASIN extraction
    asin = extract_asin_from_prowlarr(p_result)
    if asin:
        real_book = await get_book_by_asin(client_session, asin, region)
        if real_book:
            logger.info(
                f"✅ UPGRADE FOUND: Virtual book upgraded to real ASIN | "
                f"Old: {existing_book.asin} → New: {asin}"
            )
            return real_book
    
    # Step 2: Try enhanced search strategies
    strategies = [
        f"{p_result.title} {p_result.author}",
        normalize_text(p_result.title, primary_only=True),
        f'"{p_result.title}" {p_result.author}',
    ]
    
    for idx, search_query in enumerate(strategies):
        potential_matches = await list_audible_books(
            session=session,
            client_session=client_session,
            query=search_query,
            num_results=10,
            audible_region=region,
        )
        
        for a_result in potential_matches:
            # Try strict matching first
            if verify_match(p_result, a_result):
                logger.info(
                    f"✅ UPGRADE FOUND (Strategy {idx+1}): Virtual book upgraded | "
                    f"Old: {existing_book.asin} → New: {a_result.asin}"
                )
                return a_result
            
            # Try relaxed matching
            if verify_match_relaxed(p_result, a_result):
                logger.warning(
                    f"⚠️ UPGRADE FOUND (Relaxed): Virtual book upgraded | "
                    f"Old: {existing_book.asin} → New: {a_result.asin}"
                )
                return a_result
    
    logger.debug(f"No upgrade found for virtual book: {existing_book.asin}")
    return None


async def check_and_upgrade_virtual_book(
    session: Session,
    client_session: ClientSession,
    p_result: ProwlarrSearchResult,
    region: str,
) -> Optional[Audiobook]:
    """
    Check if we have an existing virtual book for this Prowlarr result,
    and if so, try to upgrade it to a real Audible book.
    
    Uses database-level locking (SELECT FOR UPDATE) to prevent concurrent
    upgrade race conditions. Only one request can hold the lock for a given
    virtual ASIN, making delete+insert atomic.
    """
    # Generate what the virtual ASIN would be
    virtual_asin = generate_virtual_asin(p_result.title, p_result.author)
    
    # Acquire write lock on the virtual book row (if it exists)
    # This prevents other concurrent requests from upgrading simultaneously
    existing = session.exec(
        select(Audiobook)
        .where(Audiobook.asin == virtual_asin)
        .with_for_update()  # Database-level lock: only one transaction can hold it
    ).first()
    
    if existing:
        # Try to upgrade it
        upgraded = await upgrade_virtual_book_if_better_match(
            session, client_session, p_result, existing, region
        )
        
        if upgraded:
            # Replace the virtual book with the real one (now safe - we hold the lock)
            try:
                # Step 1: Migrate any existing requests to the new real book
                existing_requests = session.exec(
                    select(AudiobookRequest).where(AudiobookRequest.asin == existing.asin)
                ).all()
                
                if existing_requests:
                    logger.info(
                        f"Migrating {len(existing_requests)} requests from virtual to real book",
                        virtual_asin=existing.asin,
                        real_asin=upgraded.asin
                    )
                    
                    # Delete old requests (CASCADE will handle this when we delete the book)
                    # But we need to recreate them with new ASIN to preserve user requests
                    for req in existing_requests:
                        new_req = AudiobookRequest(
                            asin=upgraded.asin,  # New real ASIN
                            user_username=req.user_username,
                            updated_at=req.updated_at
                        )
                        session.add(new_req)
                
                # Step 2: Now safe to delete virtual book and add real book
                session.delete(existing)
                # Use merge instead of add to handle existing records gracefully
                session.merge(upgraded)
                session.commit()
                logger.info(
                    f"Upgraded virtual book {existing.asin} → {upgraded.asin}",
                    virtual_asin=existing.asin,
                    real_asin=upgraded.asin
                )
            except IntegrityError as e:
                # Primary key conflict: another request may have already upgraded this book
                session.rollback()
                logger.warning(
                    f"Virtual book upgrade conflict (another request won the race)",
                    virtual_asin=existing.asin,
                    real_asin=upgraded.asin,
                    error=str(e)
                )
                
                # Re-query to get current database state after rollback
                current_state = session.exec(
                    select(Audiobook).where(Audiobook.asin == virtual_asin)
                ).first()
                
                if current_state:
                    # Virtual book still exists (another concurrent upgrade also failed)
                    logger.debug(
                        f"Virtual book still exists after rollback",
                        virtual_asin=virtual_asin
                    )
                    return current_state
                
                # Check if another request successfully upgraded to the real ASIN
                real_book = session.exec(
                    select(Audiobook).where(Audiobook.asin == upgraded.asin)
                ).first()
                
                if real_book:
                    logger.info(
                        f"Found existing real book from concurrent upgrade",
                        real_asin=real_book.asin
                    )
                    return real_book
                
                # Fallback: Neither exists (database corruption or complex race condition)
                logger.error(
                    f"Book disappeared after rollback - possible database issue",
                    virtual_asin=virtual_asin,
                    real_asin=upgraded.asin
                )
                # Re-create virtual book as emergency fallback
                fallback = Audiobook(
                    asin=virtual_asin,
                    title=p_result.title,
                    subtitle=None,
                    authors=[p_result.author],
                    release_date=p_result.publish_date,
                    runtime_length_min=0,
                    cover_image=None,
                )
                session.add(fallback)
                session.commit()
                return fallback
            except Exception as e:
                session.rollback()
                logger.error(
                    f"Failed to upgrade virtual book, rolled back transaction",
                    virtual_asin=existing.asin,
                    real_asin=upgraded.asin,
                    error=str(e),
                    error_type=type(e).__name__
                )
                # Re-query to get valid session-bound object
                current_state = session.exec(
                    select(Audiobook).where(Audiobook.asin == virtual_asin)
                ).first()
                if current_state:
                    return current_state
                
                # Emergency fallback if virtual book also disappeared
                logger.error(
                    f"Virtual book disappeared after generic error - recreating",
                    virtual_asin=virtual_asin
                )
                fallback = Audiobook(
                    asin=virtual_asin,
                    title=p_result.title,
                    subtitle=None,
                    authors=[p_result.author],
                    release_date=p_result.publish_date,
                    runtime_length_min=0,
                    cover_image=None,
                )
                session.add(fallback)
                session.commit()
                return fallback

            return upgraded
        
        # No upgrade found, return existing virtual book
        return existing
    
    return None


async def check_and_upgrade_virtual_book_cached(
    session: Session,
    client_session: ClientSession,
    p_result: ProwlarrSearchResult,
    region: str,
) -> Optional[Audiobook]:
    """
    Cached wrapper for check_and_upgrade_virtual_book to avoid redundant upgrade checks.
    """
    settings = Settings()
    virtual_asin = generate_virtual_asin(p_result.title, p_result.author)

    # Check cache first
    cached_result = upgrade_attempt_cache.get(
        settings.app.upgrade_attempt_cache_ttl, virtual_asin
    )
    if cached_result is not None:
        if cached_result == "":  # Failed upgrade cached
            logger.debug(f"Cached upgrade failure for {virtual_asin}")
            existing = session.exec(
                select(Audiobook).where(Audiobook.asin == virtual_asin)
            ).first()
            return existing
        else:  # Successful upgrade cached
            logger.debug(f"Cached upgrade success for {virtual_asin} → {cached_result}")
            # Return the real book by ASIN
            real_book = await get_book_by_asin(client_session, cached_result, region)
            if real_book:
                return real_book
            # If real book not found (unlikely), fall through to try upgrade again

    # Not cached, perform upgrade check
    try:
        result = await check_and_upgrade_virtual_book(
            session, client_session, p_result, region
        )
    except Exception as e:
        logger.error(
            f"Upgrade check failed with exception",
            virtual_asin=virtual_asin,
            error=str(e),
            error_type=type(e).__name__
        )
        # Don't cache failures - allow retry on next request
        return None

    # Cache result (only if successful)
    if result:
        if result.asin.startswith("VIRTUAL-"):
            upgrade_attempt_cache.set("", virtual_asin)  # Failed upgrade
        else:
            upgrade_attempt_cache.set(result.asin, virtual_asin)  # Successful upgrade

    return result


@router.get("", response_model=list[AudiobookSearchResult])
async def search_books(
    client_session: Annotated[ClientSession, Depends(get_connection)],
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(APIKeyAuth())],
    query: Annotated[str | None, Query(alias="q")] = None,
    num_results: int = 20,
    page: int = 0,
    region: audible_region_type | None = None,
    available_only: bool = False,
):
    if region is None:
        region = get_region_from_settings()
    if audible_regions.get(region) is None:
        raise HTTPException(status_code=400, detail="Invalid region")

    results: List[Audiobook] = []

    if query:
        clear_old_book_caches(session)

        if available_only:
            # Availability-first search mode
            # First search Prowlarr for available books
            async with timing_context("Prowlarr search"):
                prowlarr_results = await search_prowlarr_available(
                    session=session,
                    client_session=client_session,
                    query=query,
                    limit=num_results,
                )

            # De-duplicate Prowlarr results by title/author to avoid redundant parallel DB operations
            unique_prowlarr_results: dict[str, ProwlarrSearchResult] = {}
            for res in prowlarr_results:
                key = f"{res.title}:{res.author}".lower()
                if key not in unique_prowlarr_results:
                    unique_prowlarr_results[key] = res

            # For each unique available book, fetch Audible metadata in parallel
            # Use a map to track verified results and fallbacks
            results_map: dict[str, Audiobook] = {}

            # Filter out short/common words from the query for verification
            stop_words = {
                "the",
                "a",
                "an",
                "of",
                "and",
                "or",
                "in",
                "on",
                "at",
                "to",
                "for",
                "with",
                "by",
            }
            query_parts = [
                part
                for part in query.lower().split()
                if part not in stop_words or len(part) > 2
            ]
            if not query_parts:
                query_parts = query.lower().split()

            async def fetch_and_verify(p_result: ProwlarrSearchResult, semaphore: asyncio.Semaphore, original_query: str):
                # Use semaphore to limit concurrent Audible API calls
                async with semaphore:
                    logger.info(
                        f"🔍 FETCH_AND_VERIFY START | "
                        f"Prowlarr: '{p_result.title}' by '{p_result.author}' | "
                        f"GUID: {p_result.guid}"
                    )

                    # Step 0: Check if we have an existing virtual book that can be upgraded
                    existing_upgraded = await check_and_upgrade_virtual_book_cached(
                        session, client_session, p_result, region
                    )
                    if existing_upgraded:
                        logger.info(
                            f"🔄 UPGRADED EXISTING VIRTUAL BOOK | "
                            f"Prowlarr: '{p_result.title}' | "
                            f"New ASIN: {existing_upgraded.asin}"
                        )
                        return (existing_upgraded, p_result)
                    
                    # Step 1: Try to extract ASIN from Prowlarr metadata
                    asin = extract_asin_from_prowlarr(p_result)
                    if asin:
                        book = await get_book_by_asin(client_session, asin, region)
                        if book:
                            logger.info(
                                f"✅ DIRECT ASIN MATCH | "
                                f"Prowlarr: '{p_result.title}' | "
                                f"ASIN: {asin} | "
                                f"Audible: '{book.title}'"
                            )
                            return (book, p_result)
                    
                    # Step 2: Try enhanced search strategies in parallel
                    strategies = [
                        ("title + author", f"{p_result.title} {p_result.author}"),
                        ("primary title only", normalize_text(p_result.title, primary_only=True)),
                        ("quoted title + author", f'"{p_result.title}" {p_result.author}'),
                    ]

                    # Helper function to try a single strategy
                    async def try_strategy(
                        strategy_name: str, search_query: str, strict: bool = True
                    ) -> tuple[Audiobook, ProwlarrSearchResult] | None:
                        try:
                            potential_matches = await list_audible_books(
                                session=session,
                                client_session=client_session,
                                query=search_query,
                                num_results=10,
                                audible_region=region,
                            )

                            verify_fn = verify_match if strict else verify_match_relaxed
                            for a_result in potential_matches:
                                if verify_fn(p_result, a_result, search_query=original_query):
                                    logger.info(
                                        f"✅ {'VERIFIED' if strict else 'RELAXED'} MATCH ({strategy_name}) | "
                                        f"Prowlarr: '{p_result.title}' by '{p_result.author}' | "
                                        f"Audible: '{a_result.title}' by {a_result.authors}"
                                    )
                                    return (a_result, p_result)
                        except Exception as e:
                            logger.warning(f"Strategy {strategy_name} failed: {e}")
                        return None

                    # Create tasks for all strategies (strict + relaxed fallback)
                    tasks = []
                    for strategy_name, search_query in strategies:
                        tasks.append(try_strategy(strategy_name, search_query, strict=True))
                    # Add relaxed strategy as fallback
                    tasks.append(try_strategy("relaxed", strategies[0][1], strict=False))

                    # Return first successful match
                    for coro in asyncio.as_completed(tasks):
                        result = await coro
                        if result:
                            # Cancel remaining tasks
                            for task in tasks:
                                if isinstance(task, asyncio.Task) and not task.done():
                                    task.cancel()
                            return result

                    # Step 3: No match found - create virtual book
                    fallback_asin = generate_virtual_asin(p_result.title, p_result.author)
                    fallback_book = Audiobook(
                        asin=fallback_asin,
                        title=p_result.title,
                        subtitle=None,
                        authors=[p_result.author],
                        release_date=p_result.publish_date,
                        runtime_length_min=0,
                        cover_image=None,
                    )
                    logger.warning(
                        f"📦 VIRTUAL BOOK CREATED | "
                        f"Title: '{p_result.title}' | "
                        f"Author: '{p_result.author}' | "
                        f"ASIN: {fallback_asin} | "
                        f"Reason: No match found after all strategies"
                    )
                    return (fallback_book, p_result)

            # Create semaphore to limit concurrent Audible API calls
            settings = Settings()
            semaphore = asyncio.Semaphore(settings.app.max_concurrent_audible_requests)
            
            # Create tasks for unique results and run them in parallel with rate limiting
            async def fetch_with_timeout(res, semaphore):
                try:
                    return await asyncio.wait_for(
                        fetch_and_verify(res, semaphore, query),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout for {res.title}, creating virtual book")
                    fallback_asin = generate_virtual_asin(res.title, res.author)
                    fallback_book = Audiobook(
                        asin=fallback_asin,
                        title=res.title,
                        subtitle=None,
                        authors=[res.author],
                        release_date=res.publish_date,
                        runtime_length_min=0,
                        cover_image=None,
                    )
                    logger.warning(
                        f"📦 VIRTUAL BOOK CREATED (Timeout) | "
                        f"Title: '{res.title}' | "
                        f"Author: '{res.author}' | "
                        f"ASIN: {fallback_asin}"
                    )
                    return (fallback_book, res)
            
            tasks = [fetch_with_timeout(res, semaphore) for res in unique_prowlarr_results.values()]
            async with timing_context("Parallel fetch & verify"):
                parallel_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Track seen virtual ASINs to prevent duplicate creation
            seen_virtual_asins: set[str] = set()
            
            for item in parallel_results:
                if isinstance(item, Exception):
                    # Log the actual exception for debugging
                    logger.error(
                        f"Failed to fetch/verify Prowlarr result",
                        error=str(item),
                        error_type=type(item).__name__
                    )
                    continue
                
                if not item:
                    continue

                book, prowlarr_result = item
                
                # Deduplicate virtual ASINs to prevent parallel creation conflicts
                if book.asin.startswith("VIRTUAL-"):
                    if book.asin in seen_virtual_asins:
                        logger.debug(
                            f"Skipping duplicate virtual ASIN: {book.asin} for '{book.title}'"
                        )
                        continue
                    seen_virtual_asins.add(book.asin)
                
                if book.asin not in results_map:
                    book.prowlarr_count = prowlarr_result.seeders
                    book.freeleech = prowlarr_result.freeleech
                    book.last_prowlarr_query = prowlarr_result.publish_date
                    results_map[book.asin] = book
                else:
                    existing = results_map[book.asin]
                    if prowlarr_result.seeders > (existing.prowlarr_count or 0):
                        existing.prowlarr_count = prowlarr_result.seeders
                        if prowlarr_result.freeleech:
                            existing.freeleech = True

            results = list(results_map.values())
            
            # Enrich virtual books with Google Books metadata
            settings = Settings()
            if settings.app.enable_metadata_enrichment:
                virtual_books = [b for b in results if b.asin.startswith("VIRTUAL-")]
                virtual_book_count = len(virtual_books)
                logger.info(f"📚 Starting metadata enrichment for {virtual_book_count} virtual books")

                if virtual_book_count > 0:
                    async with timing_context("Metadata enrichment"):
                        # Parallel enrichment using asyncio.gather
                        enrichment_tasks = [
                            google_books_provider.enrich_virtual_book(
                                client_session=client_session,
                                session=session,
                                book=book,
                            )
                            for book in virtual_books
                        ]

                        enriched_books = await asyncio.gather(*enrichment_tasks, return_exceptions=True)

                    # Build results map with enriched books and persist to database
                    results_map = {}
                    successful = 0
                    failed = 0

                    try:
                        for book in results:
                            if book.asin.startswith("VIRTUAL-"):
                                # Find corresponding enriched result
                                idx = virtual_books.index(book)
                                enriched = enriched_books[idx]
                                if isinstance(enriched, Exception):
                                    logger.error(
                                        f"Failed to enrich {book.asin}",
                                        error=str(enriched),
                                        title=book.title,
                                        authors=book.authors
                                    )
                                    results_map[book.asin] = book
                                    failed += 1
                                else:
                                    # Check if book already exists in database before merging
                                    existing = session.exec(
                                        select(Audiobook).where(Audiobook.asin == enriched.asin)
                                    ).first()
                                    
                                    if existing:
                                        # Book already exists, update it instead of merge to avoid constraint violations
                                        for attr in ['title', 'subtitle', 'authors', 'narrators', 'cover_image', 'release_date', 'runtime_length_min']:
                                            setattr(existing, attr, getattr(enriched, attr))
                                        results_map[book.asin] = existing
                                    else:
                                        # New book, safe to merge
                                        session.merge(enriched)
                                        results_map[book.asin] = enriched
                                    successful += 1
                            else:
                                results_map[book.asin] = book

                        results = list(results_map.values())

                        # Commit enrichment changes to database
                        try:
                            session.commit()
                            logger.info(
                                f"✅ Enrichment complete: {successful} successful, {failed} failed, changes persisted to database"
                            )
                        except IntegrityError:
                            # Some books already exist, that's ok - just use the in-memory versions
                            session.rollback()
                            session.expire_all()  # Clear session state after rollback
                            logger.info(
                                f"✅ Enrichment complete (some books already in DB): {successful} successful, {failed} failed"
                            )
                    except IntegrityError as e:
                        session.rollback()
                        session.expire_all()  # Clear session state after rollback
                        logger.warning(
                            f"Duplicate ASIN detected during enrichment, rolled back",
                            error=str(e),
                            successful=successful,
                            failed=failed
                        )
                        # Continue with enriched books in memory (not persisted)
                        logger.info("Continuing with non-persisted enrichment in search results")
                    except Exception as e:
                        session.rollback()
                        session.expire_all()  # Clear session state after rollback
                        logger.error(
                            f"Failed to persist enriched metadata, rolled back",
                            error=str(e),
                            error_type=type(e).__name__,
                            successful=successful,
                            failed=failed
                        )
                        # Continue with enriched books in memory (not persisted)
                        logger.warning("Continuing with non-persisted enrichment in search results")
        else:
            # Standard Audible-first search
            logger.info(f"🔍 STANDARD AUDIBLE SEARCH | Query: '{query}' | Mode: Audible-first (NO Prowlarr verification)")
            results = await list_audible_books(
                session=session,
                client_session=client_session,
                query=query,
                num_results=num_results,
                page=page,
                audible_region=region,
            )
            logger.info(f"📊 AUDIBLE SEARCH RESULTS | Found {len(results)} books from Audible API")
            for idx, book in enumerate(results[:5]):
                logger.info(f"  [{idx+1}] '{book.title}' by {book.authors} | ASIN: {book.asin}")
    else:
        results = await list_popular_books(
            session=session,
            client_session=client_session,
            num_results=num_results,
            page=page,
            audible_region=region,
        )

    # Apply author relevance ranking for available_only searches
    settings = Settings()
    if (available_only and query and results and
        settings.app.enable_author_relevance_ranking):

        logger.info(f"🎯 Applying author relevance ranking for {len(results)} results")

        # Check cache first
        cache_key = generate_ranking_cache_key(results, query, settings)
        cached_ranking = ranking_cache.get(settings.app.ranking_cache_ttl, cache_key)

        if cached_ranking:
            logger.info(f"✅ Using cached ranking for {len(results)} results")
            return cached_ranking

        # Rank results by author relevance
        async with timing_context("Author ranking"):
            ranked_results = rank_search_results(
                books=results,
                search_query=query,
                author_threshold=settings.app.author_match_threshold,
                enable_secondary_scoring=settings.app.enable_secondary_scoring
            )

        # Log ranking results for debugging
        logger.info(f"📊 RANKING RESULTS for '{query}'")
        for idx, result in enumerate(ranked_results[:10]):
            logger.info(
                f"  [{idx+1}] Score: {result['score']:.1f} | "
                f"Type: {result['match_type']} | "
                f"Title: {result['book'].title} | "
                f"Authors: {result['book'].authors} | "
                f"Explanation: {result['explanation']}"
            )

        # Convert to RankedAudiobookSearchResult objects
        response = [
            RankedAudiobookSearchResult(
                book=r['book'],
                requests=r['book'].requests,
                username=user.username,
                relevance_score=r['score'],
                author_score=r['author_score'],
                secondary_score=r['secondary_score'],
                match_type=r['match_type'],
                match_explanation=r['explanation'],
                is_best_match=r['is_best_match']
            )
            for r in ranked_results
        ]

        # Cache the result
        ranking_cache.set(response, cache_key)

        return response
    
    # Log search results for debugging (non-ranked)
    logger.info(f"========== SEARCH RESULTS for '{query}' ==========")
    logger.info(f"Total results: {len(results)}")
    for idx, result in enumerate(results[:10]):  # Log first 10
        logger.info(f"  [{idx+1}] {result.title} by {result.authors}")
    logger.info("=" * 50)

    # Return standard results (non-ranked)
    return [
        AudiobookSearchResult(
            book=book,
            requests=book.requests,
            username=user.username,
        )
        for book in results
    ]


@router.get("/suggestions", response_model=list[str])
async def search_suggestions(
    query: Annotated[str, Query(alias="q")],
    _: Annotated[DetailedUser, Security(APIKeyAuth())],
    region: audible_region_type | None = None,
):
    if region is None:
        region = get_region_from_settings()
    async with ClientSession() as client_session:
        return await book_search.get_search_suggestions(client_session, query, region)


@router.post("/clear-metadata-cache")
async def clear_metadata_cache(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(APIKeyAuth())],
    search_key: Optional[str] = Query(None, description="Specific search key to clear (optional)"),
    provider: Optional[str] = Query(None, description="Provider to clear (default: google_books)"),
):
    """
    Clear metadata cache entries. Useful for retrying failed enrichments or debugging.

    - If no parameters provided, clears all metadata cache
    - If search_key provided, clears cache for that specific key
    - If provider provided, clears cache for that provider
    """
    try:
        count = await google_books_provider.clear_cache(
            session=session,
            search_key=search_key,
            provider=provider or "google_books"
        )
        return {
            "success": True,
            "message": f"Cleared {count} cache entries",
            "count": count
        }
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {e}")


@router.get("/cache-stats")
async def get_cache_stats(
    _: Annotated[DetailedUser, Security(APIKeyAuth())],
):
    """
    Get cache statistics for performance monitoring.

    Returns information about:
    - Fuzzy match cache (for text matching optimization)
    - Ranking cache (for author relevance ranking)
    - Upgrade attempt cache (for virtual book upgrade tracking)
    """
    from app.internal.prowlarr.util import fuzzy_match_cache

    settings = Settings()

    # Helper to safely get cache size
    def get_cache_size(cache):
        try:
            if hasattr(cache, "_cache"):
                return len(cache._cache)
            return 0
        except Exception:
            return 0

    return {
        "fuzzy_match_cache": {
            "size": get_cache_size(fuzzy_match_cache),
            "ttl_seconds": settings.app.fuzzy_match_cache_ttl,
            "description": "Caches fuzzy string matching scores to avoid redundant calculations"
        },
        "ranking_cache": {
            "size": get_cache_size(ranking_cache),
            "ttl_seconds": settings.app.ranking_cache_ttl,
            "description": "Caches author relevance ranking results for repeated queries"
        },
        "upgrade_attempt_cache": {
            "size": get_cache_size(upgrade_attempt_cache),
            "ttl_seconds": settings.app.upgrade_attempt_cache_ttl,
            "description": "Caches virtual book upgrade attempts to avoid redundant Audible searches"
        },
        "performance_settings": {
            "max_concurrent_audible_requests": settings.app.max_concurrent_audible_requests,
            "description": "Maximum number of concurrent Audible API requests allowed"
        }
    }
