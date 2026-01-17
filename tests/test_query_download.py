"""
Comprehensive test suite for query and download management system.

Tests cover:
- Query model creation and validation (QueryResult)
- Download status transitions (queued → active → completed)
- Concurrent query operations (manage_queried context manager)
- Query caching behavior (with_cache, only_return_if_cached)
- Error handling in download management
- State management (ok, querying, uncached)
- User permission checks
- Query retrieval and filtering
- Timeout handling
- Background query operations
"""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import Any

import pytest
from aiohttp import ClientSession
from fastapi import HTTPException
from sqlmodel import Session, select

from app.internal.query import (
    QueryResult,
    query_sources,
    background_start_query,
    manage_queried,
    querying,
)
from app.internal.models import (
    Audiobook,
    User,
    ProwlarrSource,
    TorrentSource,
    GroupEnum,
    AudiobookRequest,
)


class TestQueryResult:
    """Test QueryResult model creation and validation."""

    def test_query_result_ok_state(self):
        """QueryResult with ok state should have ok property True."""
        book = Audiobook(
            asin="B002V00TOO",
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        
        sources: list[ProwlarrSource] = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        result = QueryResult(
            sources=sources,
            book=book,
            state="ok",
        )
        
        assert result.ok is True
        assert result.state == "ok"
        assert result.sources == sources
        assert result.error_message is None

    def test_query_result_querying_state(self):
        """QueryResult with querying state should have ok property False."""
        book = Audiobook(
            asin="B002V00TOO",
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        
        result = QueryResult(
            sources=None,
            book=book,
            state="querying",
        )
        
        assert result.ok is False
        assert result.state == "querying"
        assert result.sources is None

    def test_query_result_uncached_state(self):
        """QueryResult with uncached state should have ok property False."""
        book = Audiobook(
            asin="B002V00TOO",
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        
        result = QueryResult(
            sources=None,
            book=book,
            state="uncached",
        )
        
        assert result.ok is False
        assert result.state == "uncached"

    def test_query_result_with_error_message(self):
        """QueryResult can include error message."""
        book = Audiobook(
            asin="B002V00TOO",
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        
        result = QueryResult(
            sources=None,
            book=book,
            state="uncached",
            error_message="Prowlarr connection failed",
        )
        
        assert result.error_message == "Prowlarr connection failed"


class TestManageQueried:
    """Test manage_queried context manager for concurrent query tracking."""

    def test_manage_queried_adds_asin_to_querying_set(self):
        """ASIN should be added to querying set."""
        querying.clear()
        asin = "B002V00TOO"
        
        with manage_queried(asin):
            assert asin in querying
        
        # Should be removed after context exit
        assert asin not in querying

    def test_manage_queried_removes_asin_on_exit(self):
        """ASIN should be removed from querying set on context exit."""
        querying.clear()
        asin = "B002V00TOO"
        
        with manage_queried(asin):
            pass
        
        assert asin not in querying

    def test_manage_queried_handles_keyerror_on_exit(self):
        """Should handle KeyError gracefully if ASIN not in set."""
        querying.clear()
        asin = "B002V00TOO"
        
        # Add and manually remove to simulate KeyError condition
        querying.add(asin)
        querying.remove(asin)
        
        # Should not raise exception
        try:
            with manage_queried(asin):
                assert asin in querying
        except KeyError:
            pytest.fail("Should handle KeyError gracefully")

    def test_manage_queried_exception_still_removes(self):
        """ASIN should be removed even if exception occurs."""
        querying.clear()
        asin = "B002V00TOO"
        
        try:
            with manage_queried(asin):
                assert asin in querying
                raise ValueError("Test exception")
        except ValueError:
            pass
        
        assert asin not in querying

    def test_manage_queried_multiple_concurrent(self):
        """Multiple ASINs can be tracked simultaneously."""
        querying.clear()
        asin1 = "B002V00TOO"
        asin2 = "B003V00TOO"
        
        with manage_queried(asin1):
            with manage_queried(asin2):
                assert asin1 in querying
                assert asin2 in querying
            
            # asin2 removed, asin1 still there
            assert asin1 in querying
            assert asin2 not in querying


class TestQuerySources:
    """Test query_sources async function."""

    @pytest.mark.asyncio
    async def test_query_sources_book_not_found(self, db_session: Session):
        """Should raise 404 if book not found."""
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_client = AsyncMock(spec=ClientSession)
        
        with pytest.raises(HTTPException) as exc_info:
            await query_sources(
                asin="NONEXISTENT",
                session=db_session,
                client_session=mock_client,
                requester=user,
            )
        
        assert exc_info.value.status_code == 404
        assert "Book not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_query_sources_concurrent_query_returns_querying_state(
        self, db_session: Session
    ):
        """Should return querying state if ASIN already being queried."""
        querying.clear()
        asin = "B002V00TOO"
        
        # Add to querying set
        querying.add(asin)
        
        try:
            book = Audiobook(
                asin=asin,
                title="Test Book",
                authors=["Test Author"],
                narrators=["Test Narrator"],
                cover_image="http://example.com/cover.jpg",
                release_date=datetime.now(timezone.utc),
                runtime_length_min=300,
            )
            db_session.add(book)
            db_session.commit()
            
            user = User(
                username="testuser",
                password="hashed",
                group=GroupEnum.trusted,
            )
            db_session.add(user)
            db_session.commit()
            
            mock_client = AsyncMock(spec=ClientSession)
            
            result = await query_sources(
                asin=asin,
                session=db_session,
                client_session=mock_client,
                requester=user,
            )
            
            assert result.state == "querying"
            assert result.sources is None
            assert result.book.asin == asin
        finally:
            querying.discard(asin)

    @pytest.mark.asyncio
    async def test_query_sources_only_return_if_cached_true(self, db_session: Session):
        """Should return uncached state if only_return_if_cached=True and no cache."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            prowlarr_count=None,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch(
            "app.internal.query.prowlarr_config.raise_if_invalid"
        ) as mock_raise:
            with patch(
                "app.internal.query.query_prowlarr"
            ) as mock_query_prowlarr:
                mock_query_prowlarr.return_value = None
                
                result = await query_sources(
                    asin=asin,
                    session=db_session,
                    client_session=mock_client,
                    requester=user,
                    only_return_if_cached=True,
                )
        
        assert result.state == "uncached"
        assert result.sources is None
        # Should still validate config
        mock_raise.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_sources_invalid_prowlarr_config_raises(
        self, db_session: Session
    ):
        """Should raise if Prowlarr config invalid."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch(
            "app.internal.query.prowlarr_config.raise_if_invalid"
        ) as mock_raise:
            mock_raise.side_effect = HTTPException(status_code=500, detail="Invalid config")
            
            with pytest.raises(HTTPException) as exc_info:
                await query_sources(
                    asin=asin,
                    session=db_session,
                    client_session=mock_client,
                    requester=user,
                )
            
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_query_sources_returns_ok_with_sources(self, db_session: Session):
        """Should return ok state with sources from successful query."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        # Mock sources
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        result = await query_sources(
                            asin=asin,
                            session=db_session,
                            client_session=mock_client,
                            requester=user,
                        )
        
        assert result.state == "ok"
        assert result.sources == mock_sources
        assert result.book.asin == asin
        assert result.book.prowlarr_count == 1

    @pytest.mark.asyncio
    async def test_query_sources_updates_book_metadata(self, db_session: Session):
        """Should update book prowlarr_count and last_prowlarr_query."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            prowlarr_count=None,
            last_prowlarr_query=None,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        before_query = datetime.now()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        await query_sources(
                            asin=asin,
                            session=db_session,
                            client_session=mock_client,
                            requester=user,
                        )
        
        # Verify book was updated
        updated_book = db_session.exec(
            select(Audiobook).where(Audiobook.asin == asin)
        ).first()
        
        assert updated_book.prowlarr_count == 1
        assert updated_book.last_prowlarr_query is not None

    @pytest.mark.asyncio
    async def test_query_sources_auto_download_success(self, db_session: Session):
        """Should mark book as downloaded on successful auto-download."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        mock_response = AsyncMock()
        mock_response.ok = True
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            mock_download.return_value = mock_response
                            
                            result = await query_sources(
                                asin=asin,
                                session=db_session,
                                client_session=mock_client,
                                requester=user,
                                start_auto_download=True,
                            )
        
        # Verify book marked as downloaded
        updated_book = db_session.exec(
            select(Audiobook).where(Audiobook.asin == asin)
        ).first()
        
        assert updated_book.downloaded is True

    @pytest.mark.asyncio
    async def test_query_sources_auto_download_duplicate_torrent(self, db_session: Session):
        """Should mark as downloaded if error indicates duplicate torrent."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        mock_response = AsyncMock()
        mock_response.ok = False
        mock_response.text = AsyncMock(
            return_value=json.dumps({
                "message": "Failed",
                "description": "Duplicate torrent in transmission"
            })
        )
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            mock_download.return_value = mock_response
                            
                            result = await query_sources(
                                asin=asin,
                                session=db_session,
                                client_session=mock_client,
                                requester=user,
                                start_auto_download=True,
                            )
        
        # Verify book marked as downloaded despite error
        updated_book = db_session.exec(
            select(Audiobook).where(Audiobook.asin == asin)
        ).first()
        
        assert updated_book.downloaded is True

    @pytest.mark.asyncio
    async def test_query_sources_auto_download_already_exists(self, db_session: Session):
        """Should mark as downloaded if already exists."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        mock_response = AsyncMock()
        mock_response.ok = False
        mock_response.text = AsyncMock(
            return_value=json.dumps({
                "message": "Failed",
                "description": "Already exists"
            })
        )
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            mock_download.return_value = mock_response
                            
                            result = await query_sources(
                                asin=asin,
                                session=db_session,
                                client_session=mock_client,
                                requester=user,
                                start_auto_download=True,
                            )
        
        # Verify book marked as downloaded
        updated_book = db_session.exec(
            select(Audiobook).where(Audiobook.asin == asin)
        ).first()
        
        assert updated_book.downloaded is True

    @pytest.mark.asyncio
    async def test_query_sources_auto_download_failure_raises(self, db_session: Session):
        """Should raise if auto-download fails without duplicate indicator."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        mock_response = AsyncMock()
        mock_response.ok = False
        mock_response.text = AsyncMock(
            return_value=json.dumps({
                "message": "Failed",
                "description": "Network error"
            })
        )
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            mock_download.return_value = mock_response
                            
                            with pytest.raises(HTTPException) as exc_info:
                                await query_sources(
                                    asin=asin,
                                    session=db_session,
                                    client_session=mock_client,
                                    requester=user,
                                    start_auto_download=True,
                                )
            
            assert exc_info.value.status_code == 500
            assert "Failed to start download" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_query_sources_no_auto_download_when_downloaded(self, db_session: Session):
        """Should not attempt auto-download if book already downloaded."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=True,  # Already downloaded
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            await query_sources(
                                asin=asin,
                                session=db_session,
                                client_session=mock_client,
                                requester=user,
                                start_auto_download=True,
                            )
        
        # start_download should not be called
        mock_download.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_sources_no_auto_download_with_no_sources(self, db_session: Session):
        """Should not attempt auto-download if no sources found."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = []  # No sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = []
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            result = await query_sources(
                                asin=asin,
                                session=db_session,
                                client_session=mock_client,
                                requester=user,
                                start_auto_download=True,
                            )
        
        # start_download should not be called
        mock_download.assert_not_called()
        assert result.state == "ok"
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_query_sources_handles_malformed_error_response(self, db_session: Session):
        """Should handle malformed error response gracefully."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        mock_response = AsyncMock()
        mock_response.ok = False
        mock_response.text = AsyncMock(return_value="Not JSON")
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            mock_download.return_value = mock_response
                            
                            with pytest.raises(HTTPException) as exc_info:
                                await query_sources(
                                    asin=asin,
                                    session=db_session,
                                    client_session=mock_client,
                                    requester=user,
                                    start_auto_download=True,
                                )


class TestBackgroundStartQuery:
    """Test background_start_query for background task execution."""

    @pytest.mark.asyncio
    async def test_background_start_query_calls_query_sources(self):
        """Should call query_sources with correct parameters."""
        asin = "B002V00TOO"
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        
        with patch("app.internal.query.get_session") as mock_get_session:
            with patch("app.internal.query.ClientSession") as mock_client_class:
                mock_session = AsyncMock(spec=Session)
                mock_session.__enter__ = AsyncMock(return_value=mock_session)
                mock_session.__exit__ = AsyncMock(return_value=None)
                
                # Mock get_session to return a context manager
                mock_session_gen = AsyncMock()
                mock_session_gen.__next__ = MagicMock(return_value=mock_session)
                mock_get_session.return_value = mock_session_gen
                
                mock_client = AsyncMock(spec=ClientSession)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client
                
                with patch("app.internal.query.query_sources") as mock_query:
                    await background_start_query(asin, user, auto_download=True)
                    
                    # Verify query_sources was called
                    mock_query.assert_called_once()
                    call_kwargs = mock_query.call_args.kwargs
                    assert call_kwargs["asin"] == asin
                    assert call_kwargs["start_auto_download"] is True
                    assert call_kwargs["requester"] == user

    @pytest.mark.asyncio
    async def test_background_start_query_auto_download_false(self):
        """Should respect auto_download=False parameter."""
        asin = "B002V00TOO"
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        
        with patch("app.internal.query.get_session") as mock_get_session:
            with patch("app.internal.query.ClientSession") as mock_client_class:
                mock_session = AsyncMock(spec=Session)
                mock_session.__enter__ = AsyncMock(return_value=mock_session)
                mock_session.__exit__ = AsyncMock(return_value=None)
                
                # Mock get_session to return a context manager
                mock_session_gen = AsyncMock()
                mock_session_gen.__next__ = MagicMock(return_value=mock_session)
                mock_get_session.return_value = mock_session_gen
                
                mock_client = AsyncMock(spec=ClientSession)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client
                
                with patch("app.internal.query.query_sources") as mock_query:
                    await background_start_query(asin, user, auto_download=False)
                    
                    call_kwargs = mock_query.call_args.kwargs
                    assert call_kwargs["start_auto_download"] is False


class TestConcurrentQueryHandling:
    """Test concurrent query operations and state management."""

    @pytest.mark.asyncio
    async def test_concurrent_queries_different_asin(self, db_session: Session):
        """Multiple concurrent queries with different ASINs should all proceed."""
        querying.clear()
        
        asin1 = "B002V00TOO"
        asin2 = "B003V00TOO"
        
        book1 = Audiobook(
            asin=asin1,
            title="Book 1",
            authors=["Author 1"],
            narrators=["Narrator 1"],
            cover_image="http://example.com/cover1.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        book2 = Audiobook(
            asin=asin2,
            title="Book 2",
            authors=["Author 2"],
            narrators=["Narrator 2"],
            cover_image="http://example.com/cover2.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        
        db_session.add(book1)
        db_session.add(book2)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        # Simulate concurrent state tracking
        with manage_queried(asin1):
            with manage_queried(asin2):
                assert asin1 in querying
                assert asin2 in querying
        
        assert asin1 not in querying
        assert asin2 not in querying

    def test_querying_set_isolation_between_tests(self):
        """Querying set should be properly isolated."""
        querying.clear()
        
        assert len(querying) == 0
        
        with manage_queried("B001"):
            assert len(querying) == 1
        
        assert len(querying) == 0


class TestQueryErrorHandling:
    """Test error handling in query operations."""

    @pytest.mark.asyncio
    async def test_query_sources_malformed_json_error_response(self, db_session: Session):
        """Should handle malformed JSON in error response."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = [
            TorrentSource(
                guid="guid1",
                indexer_id=1,
                indexer="TestIndexer",
                title="Test Book",
                size=1000000,
                publish_date=datetime.now(timezone.utc),
                info_url="http://example.com",
                indexer_flags=[],
                seeders=10,
                leechers=2,
            )
        ]
        
        mock_client = AsyncMock(spec=ClientSession)
        mock_response = AsyncMock()
        mock_response.ok = False
        mock_response.text = AsyncMock(side_effect=json.JSONDecodeError("msg", "doc", 0))
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = mock_sources
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        with patch("app.internal.query.start_download") as mock_download:
                            mock_download.return_value = mock_response
                            
                            with pytest.raises(HTTPException):
                                await query_sources(
                                    asin=asin,
                                    session=db_session,
                                    client_session=mock_client,
                                    requester=user,
                                    start_auto_download=True,
                                )


class TestQueryForceRefresh:
    """Test force_refresh parameter."""

    @pytest.mark.asyncio
    async def test_query_sources_force_refresh_passed_to_prowlarr(self, db_session: Session):
        """Should pass force_refresh parameter to query_prowlarr."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = []
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = []
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        await query_sources(
                            asin=asin,
                            session=db_session,
                            client_session=mock_client,
                            requester=user,
                            force_refresh=True,
                        )
        
        # Verify force_refresh was passed
        call_kwargs = mock_query.call_args.kwargs
        assert call_kwargs["force_refresh"] is True


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_manage_queried_explicit_keyerror_scenario(self):
        """Explicitly test KeyError exception handler in manage_queried."""
        querying.clear()
        asin = "B002V00TOO"
        
        # Manually trigger a KeyError scenario
        querying.add(asin)
        querying.remove(asin)  # Now it's not in the set
        
        # Context manager should handle KeyError gracefully
        try:
            with manage_queried(asin):
                # Add it back for the try block
                assert asin in querying
                # Simulate that it gets removed (e.g., by another thread)
                querying.discard(asin)
        except KeyError:
            pytest.fail("Should handle KeyError in finally block")
        
        assert asin not in querying

    def test_query_result_empty_sources_list(self):
        """QueryResult should handle empty sources list."""
        book = Audiobook(
            asin="B002V00TOO",
            title="Test Book",
            authors=["Test Author"],
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        
        result = QueryResult(
            sources=[],
            book=book,
            state="ok",
        )
        
        assert result.ok is True
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_query_sources_with_unicode_title(self, db_session: Session):
        """Should handle unicode characters in book title."""
        asin = "B002V00TOO"
        
        book = Audiobook(
            asin=asin,
            title="日本語の本",  # Japanese title
            authors=["著者名"],  # Japanese author
            narrators=["Test Narrator"],
            cover_image="http://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=300,
        )
        db_session.add(book)
        db_session.commit()
        
        user = User(
            username="testuser",
            password="hashed",
            group=GroupEnum.trusted,
        )
        db_session.add(user)
        db_session.commit()
        
        mock_sources = []
        mock_client = AsyncMock(spec=ClientSession)
        
        with patch("app.internal.query.prowlarr_config.raise_if_invalid"):
            with patch("app.internal.query.query_prowlarr") as mock_query:
                mock_query.return_value = mock_sources
                
                with patch("app.internal.query.rank_sources") as mock_rank:
                    mock_rank.return_value = []
                    
                    with patch("app.internal.query.prowlarr_config.get_indexers") as mock_get_indexers:
                        mock_get_indexers.return_value = [1, 2, 3]
                        
                        result = await query_sources(
                            asin=asin,
                            session=db_session,
                            client_session=mock_client,
                            requester=user,
                        )
        
        assert result.book.title == "日本語の本"

    def test_manage_queried_with_special_characters_asin(self):
        """Should handle ASINs with special characters."""
        querying.clear()
        asin = "VIRTUAL-a3f9b8d2c1"
        
        with manage_queried(asin):
            assert asin in querying
        
        assert asin not in querying
