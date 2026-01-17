"""
Comprehensive test suite for Audible book search system.

Tests cover:
1. list_audible_books() function with various search patterns
2. Caching behavior (cache hits, misses, expiry)
3. CacheQuery and CacheResult models
4. Search suggestions with caching
5. Error handling (network errors, timeouts, API failures)
6. Concurrent search requests
7. Cache key generation and stability
8. Different search strategies and pagination
9. Popular books discovery
10. Book storage and retrieval patterns
"""
import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, Mock

import pytest
from aiohttp import ClientError, ClientSession
from sqlmodel import Session

from app.internal.book_search import (
    CacheQuery,
    CacheResult,
    audible_regions,
    clear_old_book_caches,
    get_book_by_asin,
    get_existing_books,
    get_region_from_settings,
    get_search_suggestions,
    list_audible_books,
    list_popular_books,
    search_cache,
    search_suggestions_cache,
    store_new_books,
    REFETCH_TTL,
)
from app.internal.models import Audiobook, AudiobookRequest, User, GroupEnum


def async_context_manager(response):
    """Helper to create an async context manager mock."""
    cm = AsyncMock()
    cm.__aenter__.return_value = response
    cm.__aexit__.return_value = None
    return cm


def mock_get_with_side_effect(*responses):
    """Create a mock get() that returns async context managers in sequence."""
    call_index = [0]
    
    async def side_effect(*args, **kwargs):
        if call_index[0] < len(responses):
            result = responses[call_index[0]]
            call_index[0] += 1
            return result
        else:
            raise ValueError("Ran out of mock responses")
    
    mock = MagicMock()
    mock.side_effect = side_effect
    return mock


class TestCacheModels:
    """Test CacheQuery and CacheResult model behavior."""

    def test_cache_query_immutable(self):
        """CacheQuery should be frozen (immutable)."""
        query = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        with pytest.raises(Exception):  # pydantic raises ValidationError
            query.query = "modified"

    def test_cache_query_hashable(self):
        """CacheQuery should be hashable for use as dict key."""
        query1 = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        query2 = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        query3 = CacheQuery(query="test", num_results=20, page=1, audible_region="us")
        
        cache = {query1: "value1"}
        assert cache[query2] == "value1"  # Same query should map to same key
        assert query3 not in cache  # Different page should be different key

    def test_cache_query_equality(self):
        """CacheQuery instances with same values should be equal."""
        query1 = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        query2 = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        assert query1 == query2

    def test_cache_query_different_regions(self):
        """CacheQuery should treat different regions as different keys."""
        query_us = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        query_uk = CacheQuery(query="test", num_results=20, page=0, audible_region="uk")
        assert query_us != query_uk

    def test_cache_result_immutable(self):
        """CacheResult should be frozen (immutable)."""
        result = CacheResult(value=[], timestamp=time.time())
        with pytest.raises(Exception):
            result.timestamp = time.time() + 100

    def test_cache_result_with_books(self):
        """CacheResult should store list of Audiobook objects."""
        book = Audiobook(
            asin="B002V00TOO",
            title="The Art of Computer Programming",
            authors=["Donald E. Knuth"],
            narrators=["Paul Boehmer"],
            cover_image="https://example.com/cover.jpg",
            release_date=datetime(1968, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=1000,
        )
        result = CacheResult(value=[book], timestamp=time.time())
        assert len(result.value) == 1
        assert result.value[0].asin == "B002V00TOO"


class TestCacheKeyGeneration:
    """Test cache key generation and stability."""

    def test_cache_key_stable_across_calls(self):
        """Same search parameters should generate identical cache keys."""
        key1 = CacheQuery(
            query="Brandon Sanderson",
            num_results=20,
            page=0,
            audible_region="us"
        )
        key2 = CacheQuery(
            query="Brandon Sanderson",
            num_results=20,
            page=0,
            audible_region="us"
        )
        assert key1 == key2
        assert hash(key1) == hash(key2)

    def test_cache_key_differs_by_query(self):
        """Different query text should produce different cache keys."""
        key1 = CacheQuery(query="Sanderson", num_results=20, page=0, audible_region="us")
        key2 = CacheQuery(query="mistborn", num_results=20, page=0, audible_region="us")
        assert key1 != key2

    def test_cache_key_differs_by_page(self):
        """Different page numbers should produce different cache keys."""
        key1 = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        key2 = CacheQuery(query="test", num_results=20, page=1, audible_region="us")
        assert key1 != key2

    def test_cache_key_differs_by_num_results(self):
        """Different result counts should produce different cache keys."""
        key1 = CacheQuery(query="test", num_results=20, page=0, audible_region="us")
        key2 = CacheQuery(query="test", num_results=50, page=0, audible_region="us")
        assert key1 != key2


class TestGetExistingBooks:
    """Test get_existing_books function."""

    def test_get_existing_books_empty_set(self, db_session):
        """Should return empty dict when no ASINs provided."""
        result = get_existing_books(db_session, set())
        assert result == {}

    def test_get_existing_books_not_found(self, db_session):
        """Should return empty dict when books not in database."""
        result = get_existing_books(db_session, {"B002V00TOO", "B007IRREX2"})
        assert result == {}

    def test_get_existing_books_single_book(self, db_session, sample_audible_books):
        """Should retrieve single book from database."""
        book = sample_audible_books[0]
        db_session.add(book)
        db_session.commit()
        
        result = get_existing_books(db_session, {book.asin})
        assert len(result) == 1
        assert result[book.asin].asin == book.asin

    def test_get_existing_books_multiple_books(self, db_session, sample_audible_books):
        """Should retrieve multiple books from database."""
        for book in sample_audible_books[:2]:
            db_session.add(book)
        db_session.commit()
        
        asins = {sample_audible_books[0].asin, sample_audible_books[1].asin}
        result = get_existing_books(db_session, asins)
        assert len(result) == 2

    def test_get_existing_books_filters_expired(self, db_session):
        """Should exclude books older than REFETCH_TTL."""
        old_book = Audiobook(
            asin="B_OLD",
            title="Old Book",
            authors=["Old Author"],
            narrators=["Old Narrator"],
            cover_image=None,
            release_date=datetime(2000, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=100,
            updated_at=datetime.fromtimestamp(
                time.time() - REFETCH_TTL - 1000  # Older than TTL
            ),
        )
        db_session.add(old_book)
        db_session.commit()
        
        result = get_existing_books(db_session, {"B_OLD"})
        assert len(result) == 0  # Expired book excluded


class TestStoreNewBooks:
    """Test store_new_books function."""

    def test_store_new_books_empty_list(self, db_session):
        """Should handle empty book list gracefully."""
        store_new_books(db_session, [])
        assert len(db_session.query(Audiobook).all()) == 0

    def test_store_new_books_single_book(self, db_session):
        """Should store single new book."""
        book = Audiobook(
            asin="B_NEW_1",
            title="New Book",
            authors=["New Author"],
            narrators=["New Narrator"],
            cover_image="https://example.com/cover.jpg",
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
        )
        store_new_books(db_session, [book])
        
        result = db_session.query(Audiobook).filter_by(asin="B_NEW_1").first()
        assert result is not None
        assert result.title == "New Book"

    def test_store_new_books_multiple_books(self, db_session):
        """Should store multiple books."""
        books = [
            Audiobook(
                asin=f"B_NEW_{i}",
                title=f"Book {i}",
                authors=[f"Author {i}"],
                narrators=[f"Narrator {i}"],
                cover_image=None,
                release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=500,
            )
            for i in range(5)
        ]
        store_new_books(db_session, books)
        
        stored_count = len(db_session.query(Audiobook).all())
        assert stored_count == 5

    def test_store_new_books_updates_existing(self, db_session):
        """Should update existing book metadata."""
        # Store initial version
        book_v1 = Audiobook(
            asin="B_UPDATE",
            title="Original Title",
            authors=["Original Author"],
            narrators=["Original Narrator"],
            cover_image="https://example.com/old.jpg",
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
        )
        db_session.add(book_v1)
        db_session.commit()
        
        # Store updated version
        book_v2 = Audiobook(
            asin="B_UPDATE",
            title="Updated Title",
            authors=["Updated Author"],
            narrators=["Updated Narrator"],
            cover_image="https://example.com/new.jpg",
            release_date=datetime(2021, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=600,
        )
        store_new_books(db_session, [book_v2])
        
        # Verify update
        result = db_session.query(Audiobook).filter_by(asin="B_UPDATE").first()
        assert result.title == "Updated Title"
        assert result.authors == ["Updated Author"]
        assert result.runtime_length_min == 600

    def test_store_new_books_handles_duplicates(self, db_session):
        """Should handle duplicate books gracefully."""
        book = Audiobook(
            asin="B_DUP",
            title="Book",
            authors=["Author"],
            narrators=["Narrator"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
        )
        # Add twice to same session
        store_new_books(db_session, [book])
        db_session.commit()
        
        # Try to store again (should update or skip)
        store_new_books(db_session, [book])
        
        # Should still only have one book
        result_count = len(db_session.query(Audiobook).filter_by(asin="B_DUP").all())
        assert result_count == 1


@pytest.mark.asyncio
class TestGetBookByAsin:
    """Test get_book_by_asin function with mocked external APIs."""

    async def test_get_book_by_asin_from_audimeta(self, mock_client_session):
        """Should fetch book from Audimeta API."""
        mock_response = AsyncMock()
        mock_response.ok = True
        mock_response.json = AsyncMock(
            return_value={
                "asin": "B002V00TOO",
                "title": "The Art of Computer Programming",
                "authors": [{"name": "Donald E. Knuth"}],
                "narrators": [{"name": "Paul Boehmer"}],
                "imageUrl": "https://example.com/cover.jpg",
                "releaseDate": "1968-01-01",
                "lengthMinutes": 1000,
            }
        )
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(mock_response))
        
        result = await get_book_by_asin(mock_client_session, "B002V00TOO", "us")
        
        assert result is not None
        assert result.asin == "B002V00TOO"
        assert result.title == "The Art of Computer Programming"

    async def test_get_book_by_asin_audimeta_fails_tries_audnexus(self, mock_client_session):
        """Should fallback to Audnexus if Audimeta fails."""
        # Audimeta fails
        audimeta_response = AsyncMock()
        audimeta_response.ok = False
        audimeta_response.status = 404
        
        # Audnexus succeeds
        audnexus_response = AsyncMock()
        audnexus_response.ok = True
        audnexus_response.json = AsyncMock(
            return_value={
                "asin": "B002V00TOO",
                "title": "The Art of Computer Programming",
                "authors": [{"name": "Donald E. Knuth"}],
                "narrators": [{"name": "Paul Boehmer"}],
                "image": "https://example.com/cover.jpg",
                "releaseDate": "1968-01-01",
                "runtimeLengthMin": 1000,
            }
        )
        
        mock_client_session.get = AsyncMock(
            side_effect=[
                async_context_manager(audimeta_response),
                async_context_manager(audnexus_response),
            ]
        )
        
        result = await get_book_by_asin(mock_client_session, "B002V00TOO", "us")
        
        assert result is not None
        assert result.asin == "B002V00TOO"
        # Called twice: once for audimeta, once for audnexus
        assert mock_client_session.get.call_count == 2

    async def test_get_book_by_asin_both_apis_fail(self, mock_client_session):
        """Should return None if both APIs fail."""
        failed_response = AsyncMock()
        failed_response.ok = False
        failed_response.status = 500
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(failed_response))
        
        result = await get_book_by_asin(mock_client_session, "B_NONEXISTENT", "us")
        
        assert result is None

    async def test_get_book_by_asin_network_error(self, mock_client_session):
        """Should handle network errors gracefully."""
        mock_client_session.get = AsyncMock(side_effect=ClientError("Connection refused"))
        
        result = await get_book_by_asin(mock_client_session, "B002V00TOO", "us")
        
        assert result is None

    async def test_get_book_by_asin_invalid_response(self, mock_client_session):
        """Should handle invalid API response gracefully."""
        mock_response = AsyncMock()
        mock_response.ok = True
        mock_response.json = AsyncMock(return_value={"invalid": "response"})
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(mock_response))
        
        result = await get_book_by_asin(mock_client_session, "B002V00TOO", "us")
        
        assert result is None


@pytest.mark.asyncio
class TestListAudibleBooks:
    """Test list_audible_books function."""

    async def test_list_audible_books_basic_search(self, db_session, mock_client_session):
        """Should search Audible API and return books."""
        # Mock Audible search API response
        search_response = AsyncMock()
        search_response.ok = True
        search_response.json = AsyncMock(
            return_value={
                "products": [
                    {"asin": "B002V00TOO"},
                    {"asin": "B007IRREX2"},
                ]
            }
        )
        
        # Mock book fetches
        book1_response = AsyncMock()
        book1_response.ok = True
        book1_response.json = AsyncMock(
            return_value={
                "asin": "B002V00TOO",
                "title": "Book 1",
                "authors": [{"name": "Author 1"}],
                "narrators": [],
                "imageUrl": None,
                "releaseDate": "2020-01-01",
                "lengthMinutes": None,
            }
        )
        
        book2_response = AsyncMock()
        book2_response.ok = True
        book2_response.json = AsyncMock(
            return_value={
                "asin": "B007IRREX2",
                "title": "Book 2",
                "authors": [{"name": "Author 2"}],
                "narrators": [],
                "imageUrl": None,
                "releaseDate": "2020-01-01",
                "lengthMinutes": None,
            }
        )
        
        mock_client_session.get = AsyncMock(
            side_effect=[
                async_context_manager(search_response),
                async_context_manager(book1_response),
                async_context_manager(book2_response),
            ]
        )
        
        # Clear any existing cache
        search_cache.clear()
        
        result = await list_audible_books(
            db_session,
            mock_client_session,
            "test query",
            num_results=20,
            page=0,
            audible_region="us"
        )
        
        assert len(result) == 2
        assert result[0].asin == "B002V00TOO"
        assert result[1].asin == "B007IRREX2"

    async def test_list_audible_books_cache_hit(self, db_session, mock_client_session, sample_audible_books):
        """Should return cached results without API call."""
        # Pre-populate cache
        search_cache.clear()
        cache_key = CacheQuery(
            query="cached query",
            num_results=20,
            page=0,
            audible_region="us"
        )
        # Store fresh books in database
        for book in sample_audible_books[:2]:
            db_session.merge(book)
        db_session.commit()
        
        search_cache[cache_key] = CacheResult(
            value=sample_audible_books[:2],
            timestamp=time.time()
        )
        
        # Mock client should not be called
        mock_client_session.get = AsyncMock()
        
        result = await list_audible_books(
            db_session,
            mock_client_session,
            "cached query",
            num_results=20,
            page=0,
            audible_region="us"
        )
        
        assert len(result) == 2
        mock_client_session.get.assert_not_called()

    async def test_list_audible_books_cache_miss(self, db_session, mock_client_session):
        """Should fetch from API when cache miss occurs."""
        search_cache.clear()
        
        search_response = AsyncMock()
        search_response.ok = True
        search_response.json = AsyncMock(return_value={"products": []})
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(search_response))
        
        result = await list_audible_books(
            db_session,
            mock_client_session,
            "new query",
            audible_region="us"
        )
        
        assert result == []
        mock_client_session.get.assert_called()

    async def test_list_audible_books_expired_cache(self, db_session, mock_client_session, sample_audible_books):
        """Should refetch when cache has expired."""
        search_cache.clear()
        cache_key = CacheQuery(
            query="expired query",
            num_results=20,
            page=0,
            audible_region="us"
        )
        # Create expired cache entry (older than REFETCH_TTL)
        search_cache[cache_key] = CacheResult(
            value=sample_audible_books[:1],
            timestamp=time.time() - REFETCH_TTL - 100
        )
        
        # Mock new search response
        search_response = AsyncMock()
        search_response.ok = True
        search_response.json = AsyncMock(return_value={"products": []})
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(search_response))
        
        result = await list_audible_books(
            db_session,
            mock_client_session,
            "expired query",
            audible_region="us"
        )
        
        # Should have called API since cache expired
        mock_client_session.get.assert_called()

    async def test_list_audible_books_pagination(self, db_session, mock_client_session):
        """Should handle pagination correctly."""
        search_cache.clear()
        
        search_response = AsyncMock()
        search_response.ok = True
        search_response.json = AsyncMock(
            return_value={"products": [{"asin": f"B_PAGE_{i}"} for i in range(5)]}
        )
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(search_response))
        
        # Test page 0
        await list_audible_books(
            db_session,
            mock_client_session,
            "test",
            page=0,
            audible_region="us"
        )
        
        # Verify API was called with page=0
        calls = mock_client_session.get.call_args_list
        assert "page=0" in calls[0][0][0]

    async def test_list_audible_books_api_error(self, db_session, mock_client_session):
        """Should return empty list on API error."""
        search_cache.clear()
        
        mock_client_session.get = AsyncMock(side_effect=ClientError("API error"))
        
        result = await list_audible_books(
            db_session,
            mock_client_session,
            "test",
            audible_region="us"
        )
        
        assert result == []

    async def test_list_audible_books_uses_defaults(self, db_session, mock_client_session):
        """Should use default values for optional parameters."""
        search_cache.clear()
        
        search_response = AsyncMock()
        search_response.ok = True
        search_response.json = AsyncMock(return_value={"products": []})
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(search_response))
        
        # Call without region (should use default)
        with patch("app.internal.book_search.get_region_from_settings", return_value="us"):
            result = await list_audible_books(
                db_session,
                mock_client_session,
                "test"
            )
        
        assert result == []


@pytest.mark.asyncio
class TestListPopularBooks:
    """Test list_popular_books function."""

    async def test_list_popular_books_search(self, db_session, mock_client_session):
        """Should fetch popular science/tech books."""
        search_cache.clear()
        
        search_response = AsyncMock()
        search_response.ok = True
        search_response.json = AsyncMock(
            return_value={"products": [{"asin": "B_POPULAR_1"}]}
        )
        
        book_response = AsyncMock()
        book_response.ok = True
        book_response.json = AsyncMock(
            return_value={
                "asin": "B_POPULAR_1",
                "title": "Popular Book",
                "authors": [{"name": "Popular Author"}],
                "narrators": [],
                "imageUrl": None,
                "releaseDate": "2020-01-01",
                "lengthMinutes": None,
            }
        )
        
        mock_client_session.get = AsyncMock(
            side_effect=[
                async_context_manager(search_response),
                async_context_manager(book_response),
            ]
        )
        
        result = await list_popular_books(
            db_session,
            mock_client_session,
            num_results=20,
            page=0,
            audible_region="us"
        )
        
        assert len(result) == 1
        assert result[0].asin == "B_POPULAR_1"

    async def test_list_popular_books_cache_hit(self, db_session, mock_client_session, sample_audible_books):
        """Should return cached popular books."""
        search_cache.clear()
        cache_key = CacheQuery(
            query="__popular_scitech__",
            num_results=20,
            page=0,
            audible_region="us"
        )
        # Store fresh books in database
        for book in sample_audible_books[:1]:
            db_session.merge(book)
        db_session.commit()
        
        search_cache[cache_key] = CacheResult(
            value=sample_audible_books[:1],
            timestamp=time.time()
        )
        
        mock_client_session.get = AsyncMock()
        
        result = await list_popular_books(
            db_session,
            mock_client_session,
            num_results=20,
            page=0,
            audible_region="us"
        )
        
        assert len(result) == 1
        mock_client_session.get.assert_not_called()


@pytest.mark.asyncio
class TestSearchSuggestions:
    """Test get_search_suggestions function."""

    async def test_get_search_suggestions_from_api(self, mock_client_session):
        """Should fetch suggestions from Audible API."""
        search_suggestions_cache.clear()
        
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(
            return_value={
                "model": {
                    "items": [
                        {
                            "model": {
                                "product_metadata": {
                                    "title": {"value": "Brandon Sanderson"}
                                }
                            }
                        },
                        {
                            "model": {
                                "title_group": {
                                    "title": {"value": "Mistborn"}
                                }
                            }
                        },
                    ]
                }
            }
        )
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(mock_response))
        
        result = await get_search_suggestions(mock_client_session, "bran", audible_region="us")
        
        assert len(result) == 2
        assert "Brandon Sanderson" in result
        assert "Mistborn" in result

    async def test_get_search_suggestions_cache_hit(self, mock_client_session):
        """Should return cached suggestions."""
        search_suggestions_cache.clear()
        search_suggestions_cache["test"] = CacheResult(
            value=["Suggestion 1", "Suggestion 2"],
            timestamp=time.time()
        )
        
        mock_client_session.get = AsyncMock()
        
        result = await get_search_suggestions(mock_client_session, "test", audible_region="us")
        
        assert len(result) == 2
        mock_client_session.get.assert_not_called()

    async def test_get_search_suggestions_cache_miss(self, mock_client_session):
        """Should fetch suggestions when cache expires."""
        search_suggestions_cache.clear()
        search_suggestions_cache["expired"] = CacheResult(
            value=["Old Suggestion"],
            timestamp=time.time() - REFETCH_TTL - 100
        )
        
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={"model": {"items": []}})
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(mock_response))
        
        result = await get_search_suggestions(mock_client_session, "expired", audible_region="us")
        
        # Should call API since cache expired
        mock_client_session.get.assert_called()

    async def test_get_search_suggestions_api_error(self, mock_client_session):
        """Should return empty list on API error."""
        search_suggestions_cache.clear()
        
        mock_client_session.get = AsyncMock(side_effect=ClientError("API error"))
        
        result = await get_search_suggestions(mock_client_session, "test", audible_region="us")
        
        assert result == []

    async def test_get_search_suggestions_empty_response(self, mock_client_session):
        """Should handle empty suggestions response."""
        search_suggestions_cache.clear()
        
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(
            return_value={"model": {"items": []}}
        )
        
        mock_client_session.get = AsyncMock(return_value=async_context_manager(mock_response))
        
        result = await get_search_suggestions(mock_client_session, "nothing", audible_region="us")
        
        assert result == []


class TestClearOldBookCaches:
    """Test clear_old_book_caches function."""

    def test_clear_old_book_caches_removes_expired_unused_books(self, db_session):
        """Should remove books older than TTL that aren't requested."""
        # Create old unrequested book
        old_book = Audiobook(
            asin="B_OLD_UNUSED",
            title="Old Book",
            authors=["Old Author"],
            narrators=["Old Narrator"],
            cover_image=None,
            release_date=datetime(2000, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=100,
            updated_at=datetime.fromtimestamp(time.time() - REFETCH_TTL - 1000),
        )
        db_session.add(old_book)
        db_session.commit()
        
        clear_old_book_caches(db_session)
        
        result = db_session.query(Audiobook).filter_by(asin="B_OLD_UNUSED").first()
        assert result is None

    def test_clear_old_book_caches_keeps_requested_books(self, db_session):
        """Should keep old books that have requests."""
        # Create user with password
        user = User(
            username="testuser",
            password="test_password",
            group=GroupEnum.untrusted
        )
        old_book = Audiobook(
            asin="B_OLD_REQUESTED",
            title="Old Book",
            authors=["Old Author"],
            narrators=["Old Narrator"],
            cover_image=None,
            release_date=datetime(2000, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=100,
            updated_at=datetime.fromtimestamp(time.time() - REFETCH_TTL - 1000),
        )
        db_session.add(user)
        db_session.add(old_book)
        db_session.commit()
        
        request = AudiobookRequest(asin="B_OLD_REQUESTED", user_username="testuser")
        db_session.add(request)
        db_session.commit()
        
        clear_old_book_caches(db_session)
        
        result = db_session.query(Audiobook).filter_by(asin="B_OLD_REQUESTED").first()
        assert result is not None  # Should be kept

    def test_clear_old_book_caches_keeps_downloaded_books(self, db_session):
        """Should keep old books that are marked as downloaded."""
        old_book = Audiobook(
            asin="B_OLD_DOWNLOADED",
            title="Old Downloaded Book",
            authors=["Old Author"],
            narrators=["Old Narrator"],
            cover_image=None,
            release_date=datetime(2000, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=100,
            downloaded=True,
            updated_at=datetime.fromtimestamp(time.time() - REFETCH_TTL - 1000),
        )
        db_session.add(old_book)
        db_session.commit()
        
        clear_old_book_caches(db_session)
        
        result = db_session.query(Audiobook).filter_by(asin="B_OLD_DOWNLOADED").first()
        assert result is not None  # Should be kept

    def test_clear_old_book_caches_keeps_recent_books(self, db_session):
        """Should not remove recent books."""
        recent_book = Audiobook(
            asin="B_RECENT",
            title="Recent Book",
            authors=["Recent Author"],
            narrators=["Recent Narrator"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=100,
            updated_at=datetime.now(),
        )
        db_session.add(recent_book)
        db_session.commit()
        
        clear_old_book_caches(db_session)
        
        result = db_session.query(Audiobook).filter_by(asin="B_RECENT").first()
        assert result is not None  # Should be kept


class TestGetRegionFromSettings:
    """Test region configuration."""

    def test_get_region_from_settings_default(self):
        """Should return default region when configured."""
        with patch("app.internal.book_search.Settings") as mock_settings:
            mock_settings.return_value.app.default_region = "us"
            result = get_region_from_settings()
            assert result == "us"

    def test_get_region_from_settings_valid_region(self):
        """Should return configured region if valid."""
        with patch("app.internal.book_search.Settings") as mock_settings:
            mock_settings.return_value.app.default_region = "uk"
            result = get_region_from_settings()
            assert result == "uk"

    def test_get_region_from_settings_invalid_defaults_to_us(self):
        """Should default to 'us' for invalid region."""
        with patch("app.internal.book_search.Settings") as mock_settings:
            mock_settings.return_value.app.default_region = "invalid"
            result = get_region_from_settings()
            assert result == "us"


@pytest.mark.asyncio
class TestConcurrentSearches:
    """Test concurrent search request handling."""

    async def test_concurrent_identical_searches(self, db_session, mock_client_session):
        """Should handle concurrent identical searches efficiently."""
        search_cache.clear()
        
        search_response = AsyncMock()
        search_response.ok = True
        search_response.json = AsyncMock(
            return_value={"products": [{"asin": "B_CONCURRENT"}]}
        )
        
        book_response = AsyncMock()
        book_response.ok = True
        book_response.json = AsyncMock(
            return_value={
                "asin": "B_CONCURRENT",
                "title": "Concurrent Book",
                "authors": [{"name": "Author"}],
                "narrators": [],
                "imageUrl": None,
                "releaseDate": "2020-01-01",
                "lengthMinutes": None,
            }
        )
        
        # Both searches need search+book responses
        mock_client_session.get = AsyncMock(
            side_effect=[
                async_context_manager(search_response),
                async_context_manager(book_response),
                async_context_manager(search_response),
                async_context_manager(book_response),
            ]
        )
        
        # Run two searches concurrently
        results = await asyncio.gather(
            list_audible_books(
                db_session,
                mock_client_session,
                "concurrent",
                audible_region="us"
            ),
            list_audible_books(
                db_session,
                mock_client_session,
                "concurrent",
                audible_region="us"
            ),
        )
        
        # Both should return results
        assert len(results) == 2
        assert len(results[0]) > 0
        assert len(results[1]) > 0

    async def test_concurrent_different_searches(self, db_session, mock_client_session):
        """Should handle concurrent different searches."""
        search_cache.clear()
        
        # Create responses for two different searches (search1, search2)
        search1_response = AsyncMock()
        search1_response.ok = True
        search1_response.json = AsyncMock(return_value={"products": [{"asin": "B_SEARCH_1"}]})
        
        book1_response = AsyncMock()
        book1_response.ok = True
        book1_response.json = AsyncMock(
            return_value={
                "asin": "B_SEARCH_1",
                "title": "Book 1",
                "authors": [{"name": "Author"}],
                "narrators": [],
                "imageUrl": None,
                "releaseDate": "2020-01-01",
                "lengthMinutes": None,
            }
        )
        
        search2_response = AsyncMock()
        search2_response.ok = True
        search2_response.json = AsyncMock(return_value={"products": [{"asin": "B_SEARCH_2"}]})
        
        book2_response = AsyncMock()
        book2_response.ok = True
        book2_response.json = AsyncMock(
            return_value={
                "asin": "B_SEARCH_2",
                "title": "Book 2",
                "authors": [{"name": "Author"}],
                "narrators": [],
                "imageUrl": None,
                "releaseDate": "2020-01-01",
                "lengthMinutes": None,
            }
        )
        
        mock_client_session.get = AsyncMock(
            side_effect=[
                async_context_manager(search1_response),
                async_context_manager(book1_response),
                async_context_manager(search2_response),
                async_context_manager(book2_response),
            ]
        )
        
        # Run two different searches concurrently
        results = await asyncio.gather(
            list_audible_books(
                db_session,
                mock_client_session,
                "search1",
                audible_region="us"
            ),
            list_audible_books(
                db_session,
                mock_client_session,
                "search2",
                audible_region="us"
            ),
        )
        
        # Both should return results
        assert len(results) == 2
        assert len(results[0]) > 0
        assert len(results[1]) > 0


class TestEdgeCases:
    """Test edge cases and special characters."""

    def test_cache_key_special_characters(self):
        """Should handle special characters in query."""
        key1 = CacheQuery(
            query="test & special: chars!",
            num_results=20,
            page=0,
            audible_region="us"
        )
        key2 = CacheQuery(
            query="test & special: chars!",
            num_results=20,
            page=0,
            audible_region="us"
        )
        assert key1 == key2

    def test_cache_key_unicode(self):
        """Should handle unicode characters in query."""
        key = CacheQuery(
            query="日本語の本",
            num_results=20,
            page=0,
            audible_region="jp"
        )
        assert key.query == "日本語の本"

    def test_store_new_books_unicode_metadata(self, db_session):
        """Should store books with unicode metadata."""
        book = Audiobook(
            asin="B_UNICODE",
            title="日本語の本",
            authors=["著者名"],
            narrators=["ナレーター"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
        )
        store_new_books(db_session, [book])
        
        result = db_session.query(Audiobook).filter_by(asin="B_UNICODE").first()
        assert result is not None
        assert result.title == "日本語の本"

    def test_audible_regions_coverage(self):
        """Should support all major Audible regions."""
        expected_regions = {"us", "ca", "uk", "au", "fr", "de", "jp", "it", "in", "es", "br"}
        assert set(audible_regions.keys()) == expected_regions

    def test_cache_result_with_large_book_list(self):
        """Should handle cache results with many books."""
        books = [
            Audiobook(
                asin=f"B_{i:04d}",
                title=f"Book {i}",
                authors=[f"Author {i}"],
                narrators=[f"Narrator {i}"],
                cover_image=None,
                release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=500,
            )
            for i in range(1000)
        ]
        result = CacheResult(value=books, timestamp=time.time())
        assert len(result.value) == 1000
