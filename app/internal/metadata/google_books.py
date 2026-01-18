"""
Google Books API provider for metadata enrichment of virtual/fallback audiobooks.
"""
import hashlib
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from aiohttp import ClientSession, ClientError
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select
from sqlalchemy.exc import SQLAlchemyError

from app.internal.env_settings import Settings
from app.internal.models import Audiobook, MetadataCache
from app.util.exceptions import handle_database_error, handle_external_api_error, handle_validation_error
from app.util.log import logger


class GoogleBooksVolumeInfo(BaseModel):
    """Google Books API volume info response model."""
    title: str = ""
    subtitle: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    imageLinks: Optional[Dict[str, str]] = None
    publishedDate: Optional[str] = None
    pageCount: Optional[int] = None
    averageRating: Optional[float] = None
    ratingsCount: Optional[int] = None
    industryIdentifiers: Optional[List[Dict[str, str]]] = None


class GoogleBooksItem(BaseModel):
    """Google Books API item response model."""
    volumeInfo: GoogleBooksVolumeInfo


class GoogleBooksResponse(BaseModel):
    """Google Books API search response model."""
    items: List[GoogleBooksItem] = Field(default_factory=list)
    totalItems: int = 0


class EnrichedMetadata(BaseModel):
    """Enriched metadata from Google Books."""
    cover_image: Optional[str] = None
    description: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    isbn: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    page_count: Optional[int] = None
    published_date: Optional[str] = None
    provider: str = "google_books"


class GoogleBooksProvider:
    """Provider for Google Books API metadata enrichment."""

    base_url: str
    cache_expiry_days: int

    def __init__(self):
        self.base_url = "https://www.googleapis.com/books/v1/volumes"
        self.cache_expiry_days = Settings().app.metadata_cache_expiry_days or 30
    
    def _generate_search_key(self, title: str, author: str) -> str:
        """Generate a consistent search key for caching."""
        clean_title = title.lower().strip()[:50]
        clean_author = author.lower().strip()[:30]
        hash_input = f"{clean_title}:{clean_author}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]
    
    def _extract_isbn(self, volume_info: GoogleBooksVolumeInfo) -> Optional[str]:
        """Extract ISBN from industry identifiers."""
        if not volume_info.industryIdentifiers:
            return None
        
        # Prefer ISBN_13, fall back to ISBN_10
        for identifier in volume_info.industryIdentifiers:
            if identifier.get("type") == "ISBN_13":
                return identifier.get("identifier")
        
        for identifier in volume_info.industryIdentifiers:
            if identifier.get("type") == "ISBN_10":
                return identifier.get("identifier")
        
        return None
    
    def _get_best_cover(self, image_links: Optional[Dict[str, str]]) -> Optional[str]:
        """Get the best available cover image."""
        if not image_links:
            return None
        
        # Try different cover sizes in order of preference
        for size in ["extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"]:
            if size in image_links:
                url = image_links[size]
                # Convert http to https if needed
                if url.startswith("http://"):
                    url = url.replace("http://", "https://", 1)
                return url
        
        # Fallback: return any available image if none of the preferred sizes exist
        for url in image_links.values():
            if url:
                if url.startswith("http://"):
                    url = url.replace("http://", "https://", 1)
                return url
        
        return None
    
    async def check_cache(
        self, 
        session: Session, 
        search_key: str
    ) -> Optional[EnrichedMetadata]:
        """Check cache for existing metadata."""
        try:
            result = session.exec(
                select(MetadataCache)
                .where(MetadataCache.search_key == search_key)
                .where(MetadataCache.provider == "google_books")
            ).first()
            
            if not result:
                return None
            
            # Check if cache is expired
            age_days = (datetime.now() - result.created_at).days
            if age_days > self.cache_expiry_days:
                logger.debug(f"Cache expired for {search_key} (age: {age_days} days)")
                session.delete(result)
                session.commit()
                return None
            
            # Parse cached metadata
            metadata_dict = json.loads(result.metadata_json)
            return EnrichedMetadata(**metadata_dict)

        except (json.JSONDecodeError, KeyError) as e:
            handle_external_api_error(e, "MetadataCache", "parse cached data", search_key=search_key)
            return None
        except ValidationError as e:
            handle_validation_error(e, "cached metadata", search_key=search_key)
            return None
        except SQLAlchemyError as e:
            handle_database_error(e, "check cache", search_key=search_key)
            # Re-raise database errors so they can be handled by caller
            raise
    
    async def store_cache(
        self, 
        session: Session, 
        search_key: str, 
        metadata: EnrichedMetadata
    ):
        """Store metadata in cache."""
        try:
            import json
            
            # Remove old cache entry if exists
            existing = session.exec(
                select(MetadataCache)
                .where(MetadataCache.search_key == search_key)
                .where(MetadataCache.provider == "google_books")
            ).first()
            
            if existing:
                existing.metadata_json = json.dumps(metadata.model_dump())
                existing.created_at = datetime.now()
            else:
                cache_entry = MetadataCache(
                    search_key=search_key,
                    provider="google_books",
                    metadata_json=json.dumps(metadata.model_dump())
                )
                session.add(cache_entry)
            
            session.commit()
            logger.debug(f"Stored cache for {search_key}")

        except TypeError as e:
            logger.error(
                f"Error serializing metadata to JSON",
                error=str(e),
                search_key=search_key
            )
            session.rollback()
        except SQLAlchemyError as e:
            handle_database_error(e, "store cache", rollback_session=session, search_key=search_key)
    
    async def search_books(
        self, 
        client_session: ClientSession, 
        title: str, 
        author: str,
        max_results: int = 5
    ) -> Optional[GoogleBooksResponse]:
        """Search Google Books API."""
        # Build search query: "intitle:author_name"
        query = f'intitle:"{title}" inauthor:"{author}"'
        
        params = {
            "q": query,
            "maxResults": max_results,
            "printType": "books",
            "orderBy": "relevance",
        }
        
        try:
            async with client_session.get(self.base_url, params=params) as response:
                if response.status != 200:
                    logger.warning(
                        f"Google Books API returned {response.status}",
                        title=title,
                        author=author
                    )
                    return None
                
                data = await response.json()
                return GoogleBooksResponse(**data)

        except ClientError as e:
            handle_external_api_error(e, "Google Books", "HTTP request", title=title, author=author)
            return None
        except (json.JSONDecodeError, KeyError) as e:
            handle_external_api_error(e, "Google Books", "parse response", title=title, author=author)
            return None
        except ValidationError as e:
            handle_validation_error(e, "Google Books response", title=title, author=author)
            return None
    
    async def search_books_with_fallbacks(
        self,
        client_session: ClientSession,
        title: str,
        author: str,
        max_results: int = 5
    ) -> Optional[GoogleBooksResponse]:
        """Search with multiple query strategies for better matching."""
        
        # Strategy 1: Exact match
        logger.debug(f"Google Books search strategy 1: Exact match")
        result = await self.search_books(client_session, title, author, max_results)
        if result and result.items:
            return result
        
        # Strategy 2: Remove common articles from title
        title_no_article = re.sub(r'^(The|A|An)\s+', '', title, flags=re.IGNORECASE)
        if title_no_article != title:
            logger.debug(f"Google Books search strategy 2: No articles ('{title_no_article}')")
            result = await self.search_books(client_session, title_no_article, author, max_results)
            if result and result.items:
                return result
        
        # Strategy 3: Title only (no author constraint)
        logger.debug(f"Google Books search strategy 3: Title only")
        params = {
            "q": f'intitle:"{title}"',
            "maxResults": max_results,
            "printType": "books",
            "orderBy": "relevance",
        }
        try:
            async with client_session.get(self.base_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    response_obj = GoogleBooksResponse(**data)
                    if response_obj.items:
                        return response_obj
        except (ClientError, json.JSONDecodeError, ValidationError) as e:
            logger.debug(
                f"Strategy 3 failed",
                error=str(e),
                error_type=type(e).__name__
            )
        
        # Strategy 4: Broader search without quotes
        logger.debug(f"Google Books search strategy 4: Broader search")
        params = {
            "q": f'{title} {author}',
            "maxResults": max_results,
            "printType": "books",
            "orderBy": "relevance",
        }
        try:
            async with client_session.get(self.base_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    response_obj = GoogleBooksResponse(**data)
                    if response_obj.items:
                        return response_obj
        except (ClientError, json.JSONDecodeError, ValidationError) as e:
            logger.debug(
                f"Strategy 4 failed",
                error=str(e),
                error_type=type(e).__name__
            )
        
        return None
    
    async def enrich_virtual_book(
        self,
        client_session: ClientSession,
        session: Session,
        book: Audiobook,
    ) -> Audiobook:
        """
        Enrich a virtual book (VIRTUAL-* ASIN) with Google Books metadata.
        
        Returns the enriched book (modifies in place but also returns for chaining).
        """
        # Only enrich virtual books
        if not book.asin.startswith("VIRTUAL-"):
            logger.debug(f"Skipping enrichment for non-virtual book: {book.asin}")
            return book
        
        # Check if we have authors to search with
        if not book.authors:
            logger.debug(f"No authors for virtual book: {book.asin}")
            return book
        
        # Generate search key and check cache
        search_key = self._generate_search_key(book.title, book.authors[0])
        cached = await self.check_cache(session, search_key)
        
        if cached:
            logger.info(f"Using cached metadata for {book.asin}")
            # Apply cached metadata
            if cached.cover_image and not book.cover_image:
                book.cover_image = cached.cover_image
                logger.debug(f"Applied cached cover image for {book.asin}")
            if cached.description:
                # Store description in subtitle field (since Audiobook model doesn't have description)
                if not book.subtitle:
                    book.subtitle = cached.description[:200] + "..." if len(cached.description) > 200 else cached.description
            if cached.authors:
                book.authors = cached.authors
            if cached.categories:
                # Store categories in extra field if needed
                pass
            return book
        
        # Not in cache, search API with fallback strategies
        try:
            logger.info(f"Enriching virtual book: '{book.title}' by {book.authors[0]}")
            response = await self.search_books_with_fallbacks(client_session, book.title, book.authors[0])

            if not response or not response.items:
                logger.warning(f"No Google Books results for '{book.title}' after all fallback strategies")
                # Store empty result in cache to avoid repeated lookups
                empty_metadata = EnrichedMetadata()
                await self.store_cache(session, search_key, empty_metadata)
                return book

            # Use first result
            best_match = response.items[0]
            volume_info = best_match.volumeInfo

            # Extract metadata
            cover_image = self._get_best_cover(volume_info.imageLinks)
            isbn = self._extract_isbn(volume_info)

            # Log what we found
            if volume_info.imageLinks:
                logger.debug(f"Found cover art for '{book.title}': {volume_info.imageLinks}")
            else:
                logger.warning(f"No cover art found for '{book.title}' in Google Books response")

            enriched = EnrichedMetadata(
                cover_image=cover_image,
                description=volume_info.description,
                authors=volume_info.authors,
                categories=volume_info.categories,
                isbn=isbn,
                rating=volume_info.averageRating,
                rating_count=volume_info.ratingsCount,
                page_count=volume_info.pageCount,
                published_date=volume_info.publishedDate,
            )

            # Store in cache
            await self.store_cache(session, search_key, enriched)

            # Apply to book
            if cover_image and not book.cover_image:
                book.cover_image = cover_image
                logger.info(f"Added cover image for {book.asin}")

            if volume_info.description:
                # Store description in subtitle field
                desc = volume_info.description
                if len(desc) > 200:
                    desc = desc[:197] + "..."
                if not book.subtitle:
                    book.subtitle = desc
                    logger.debug(f"Added description for {book.asin}")

            if volume_info.authors:
                book.authors = volume_info.authors
                logger.debug(f"Updated authors for {book.asin}")

        except ClientError as e:
            logger.error(
                f"HTTP error during Google Books enrichment",
                error=str(e),
                error_type=type(e).__name__,
                book_title=book.title,
                book_asin=book.asin
            )
            # Return original book on HTTP error
            return book
        except (ValidationError, KeyError, AttributeError) as e:
            logger.error(
                f"Data validation error during Google Books enrichment",
                error=str(e),
                error_type=type(e).__name__,
                book_title=book.title,
                book_asin=book.asin
            )
            # Return original book on validation error
            return book
        
        logger.info(f"Enriched virtual book: {book.asin}")
        return book
    
    async def clear_cache(
        self,
        session: Session,
        search_key: Optional[str] = None,
        provider: Optional[str] = None
    ) -> int:
        """Clear metadata cache entries. Returns number of entries deleted."""
        query = select(MetadataCache)
        
        if search_key:
            query = query.where(MetadataCache.search_key == search_key)
        if provider:
            query = query.where(MetadataCache.provider == provider)
        
        results = session.exec(query).all()
        count = len(results)
        
        for result in results:
            session.delete(result)
        
        session.commit()
        logger.info(f"Cleared {count} cache entries")
        return count


# Global provider instance
google_books_provider = GoogleBooksProvider()
