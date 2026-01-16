from typing import Annotated, Literal

from aiohttp import ClientSession
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Security,
    Response,
)
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select, delete

from app.internal.auth.authentication import APIKeyAuth, DetailedUser
from app.internal.book_search import (
    get_book_by_asin,
    audible_region_type,
    get_region_from_settings,
    audible_regions,
)
from app.internal.models import (
    Audiobook,
    AudiobookRequest,
    AudiobookWishlistResult,
    EventEnum,
    GroupEnum,
    User,
)
from app.internal.notifications import (
    send_all_notifications,
)
from app.internal.prowlarr.prowlarr import start_download
from app.internal.prowlarr.util import ProwlarrMisconfigured, prowlarr_config
from app.internal.query import query_sources, QueryResult, background_start_query
from app.internal.ranking.quality import quality_config
from app.internal.db_queries import get_wishlist_results
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.toast import ToastException

router = APIRouter(prefix="/requests", tags=["Requests"])


class DownloadSourceBody(BaseModel):
    guid: str
    indexer_id: int


@router.post("/{asin}", status_code=201)
async def create_request(
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(APIKeyAuth())],
    background_task: BackgroundTasks,
    asin: str,
    region: audible_region_type | None = None,
):
    if region is None:
        region = get_region_from_settings()
    if audible_regions.get(region) is None:
        raise HTTPException(status_code=400, detail="Invalid region")

    book = await get_book_by_asin(client_session, asin, region)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if not session.exec(
        select(AudiobookRequest).where(
            AudiobookRequest.asin == asin,
            AudiobookRequest.user_username == user.username,
        )
    ).first():
        try:
            book_request = AudiobookRequest(asin=asin, user_username=user.username)
            session.add(book_request)
            session.commit()
            logger.info(
                "Added new audiobook request",
                username=user.username,
                asin=asin,
            )
        except HTTPException:
            session.rollback()
            raise
        except IntegrityError:
            session.rollback()
            logger.info("Duplicate request detected", username=user.username, asin=asin)
            raise HTTPException(status_code=409, detail="Book already requested")
        except Exception as e:
            session.rollback()
            logger.exception("Failed to add audiobook request", username=user.username, asin=asin, error=e)
            raise HTTPException(status_code=500, detail="Failed to add request")
    else:
        raise HTTPException(status_code=409, detail="Book already requested")

    background_task.add_task(
        send_all_notifications,
        event_type=EventEnum.on_new_request,
        requester=User.model_validate(user),
        book_asin=asin,
    )

    if quality_config.get_auto_download(session) and user.is_above(GroupEnum.trusted):
        # start querying and downloading if auto download is enabled
        background_task.add_task(
            background_start_query,
            asin=asin,
            requester=User.model_validate(user),
            auto_download=True,
        )

    return Response(status_code=201)


@router.get("", response_model=list[AudiobookWishlistResult])
async def list_requests(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(APIKeyAuth())],
    filter: Literal["all", "downloaded", "not_downloaded"] = "all",
):
    username = None if user.is_admin() else user.username
    results = get_wishlist_results(session, username, filter)
    return results


@router.delete("/{asin}")
async def delete_request(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(APIKeyAuth())],
):
    try:
        if user.is_admin():
            session.execute(
                delete(AudiobookRequest).where(col(AudiobookRequest.asin) == asin)
            )
        else:
            session.execute(
                delete(AudiobookRequest).where(
                    (col(AudiobookRequest.asin) == asin)
                    & (col(AudiobookRequest.user_username) == user.username)
                )
            )
        session.commit()
        return Response(status_code=204)
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.exception("Failed to delete audiobook request", asin=asin, username=user.username, error=e)
        raise HTTPException(status_code=500, detail="Failed to delete request")


@router.patch("/{asin}/downloaded")
async def mark_downloaded(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    _: Annotated[DetailedUser, Security(APIKeyAuth(GroupEnum.admin))],
):
    book = session.exec(select(Audiobook).where(Audiobook.asin == asin)).first()
    if book:
        try:
            book.downloaded = True
            session.add(book)
            session.commit()

            background_task.add_task(
                send_all_notifications,
                event_type=EventEnum.on_successful_download,
                requester=None,
                book_asin=asin,
            )
            return Response(status_code=204)
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.exception("Failed to mark book as downloaded", asin=asin, error=e)
            raise HTTPException(status_code=500, detail="Failed to mark book as downloaded")
    raise HTTPException(status_code=404, detail="Book not found")


@router.post(
    "/{asin}/refresh",
    description="Refresh the sources from prowlarr for a book",
)
async def refresh_source(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(APIKeyAuth())],
    force_refresh: bool = False,
):
    # causes the sources to be placed into cache once they're done
    await query_sources(
        asin=asin,
        session=session,
        client_session=client_session,
        force_refresh=force_refresh,
        requester=User.model_validate(user),
    )
    return Response(status_code=202)


@router.get("/{asin}/sources", response_model=QueryResult)
async def list_sources(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(APIKeyAuth(GroupEnum.admin))],
    only_cached: bool = False,
):
    try:
        prowlarr_config.raise_if_invalid(session)
    except ProwlarrMisconfigured:
        raise HTTPException(status_code=400, detail="Prowlarr misconfigured")

    result = await query_sources(
        asin,
        session=session,
        client_session=client_session,
        requester=admin_user,
        only_return_if_cached=only_cached,
    )
    return result


@router.post("/{asin}/download")
async def download_book(
    asin: str,
    body: DownloadSourceBody,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(APIKeyAuth(GroupEnum.admin))],
):
    try:
        resp = await start_download(
            session=session,
            client_session=client_session,
            guid=body.guid,
            indexer_id=body.indexer_id,
            requester=admin_user,
            book_asin=asin,
        )
    except ProwlarrMisconfigured as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not resp.ok:
        raise HTTPException(status_code=500, detail="Failed to start download")

    book = session.exec(select(Audiobook).where(Audiobook.asin == asin)).first()
    if book:
        try:
            book.downloaded = True
            session.add(book)
            session.commit()
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.exception("Failed to mark book as downloaded", asin=asin, error=e)
            raise HTTPException(status_code=500, detail="Failed to mark book as downloaded")

    return Response(status_code=204)


@router.post("/{asin}/auto-download")
async def start_auto_download_endpoint(
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(APIKeyAuth(GroupEnum.trusted))],
):
    try:
        await query_sources(
            asin=asin,
            start_auto_download=True,
            session=session,
            client_session=client_session,
            requester=user,
        )
    except HTTPException as e:
        raise ToastException(e.detail) from None

    return Response(status_code=204)
