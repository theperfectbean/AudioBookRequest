from typing import Annotated
from aiohttp import ClientSession
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Security,
)
from sqlmodel import Session
from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.book_search import (
    audible_region_type,
    audible_regions,
    get_region_from_settings,
)
from app.internal.models import (
    GroupEnum,
)
from app.internal.prowlarr.util import prowlarr_config
from app.internal.ranking.quality import quality_config
from app.internal.db_queries import get_wishlist_results, get_wishlist_counts
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.templates import template_response
from app.routers.api.search import (
    search_books,
    search_suggestions as api_search_suggestions,
)
from app.routers.api.requests import (
    create_request,
    delete_request as api_delete_request,
)

router = APIRouter(prefix="/search")

@router.get("")
async def read_search(
    request: Request,
    client_session: Annotated[ClientSession, Depends(get_connection)],
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    query: Annotated[str | None, Query(alias="q")] = None,
    num_results: int = 20,
    page: int = 0,
    region: audible_region_type | None = None,
    available_only: bool = True,
):
    if region is None:
        region = get_region_from_settings()
    try:
        results = await search_books(
            client_session=client_session,
            session=session,
            user=user,
            query=query,
            num_results=num_results,
            page=page,
            region=region,
            available_only=available_only,
        )
        prowlarr_configured = prowlarr_config.is_valid(session)
        return template_response(
            "search.html",
            request,
            user,
            {
                "search_term": query or "",
                "search_results": results,
                "regions": audible_regions,
                "selected_region": region,
                "page": page,
                "auto_start_download": quality_config.get_auto_download(session)
                and user.is_above(GroupEnum.trusted),
                "prowlarr_configured": prowlarr_configured,
                "available_only": available_only,
            },
        )
    except Exception as e:
        session.rollback()
        logger.exception("Error during search", error=e)
        raise HTTPException(status_code=500, detail="Internal server error") from e

@router.get("/suggestions")
async def search_suggestions(
    request: Request,
    query: Annotated[str, Query(alias="q")],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    region: audible_region_type | None = None,
):
    suggestions = await api_search_suggestions(query, user, region)
    return template_response(
        "search.html",
        request,
        user,
        {"suggestions": suggestions},
        block_name="search_suggestions",
    )

@router.post("/request/{asin}")
async def add_request(
    request: Request,
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    background_task: BackgroundTasks,
    query: Annotated[str | None, Form()],
    page: Annotated[int, Form()],
    region: Annotated[audible_region_type, Form()],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    num_results: Annotated[int, Form()] = 20,
    available_only: Annotated[bool, Form()] = True,
):
    try:
        await create_request(
            asin=asin,
            session=session,
            client_session=client_session,
            background_task=background_task,
            user=user,
            region=region,
        )
    except HTTPException as e:
        logger.warning(
            e.detail,
            username=user.username,
            asin=asin,
        )
    results = []
    if query:
        results = await search_books(
            client_session=client_session,
            session=session,
            user=user,
            query=query,
            num_results=num_results,
            page=page,
            region=region,
            available_only=available_only,
        )
    prowlarr_configured = prowlarr_config.is_valid(session)
    return template_response(
        "search.html",
        request,
        user,
        {
            "search_term": query or "",
            "search_results": results,
            "regions": audible_regions,
            "selected_region": region,
            "page": page,
            "auto_start_download": quality_config.get_auto_download(session)
            and user.is_above(GroupEnum.trusted),
            "prowlarr_configured": prowlarr_configured,
            "available_only": available_only,
        },
        block_name="book_results",
    )

@router.delete("/request/{asin}")
async def delete_request(
    request: Request,
    asin: str,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    downloaded: bool | None = None,
):
    await api_delete_request(asin, session, user)
    results = get_wishlist_results(
        session,
        None if user.is_admin() else user.username,
        "downloaded" if downloaded else "not_downloaded",
    )
    counts = get_wishlist_counts(session, user)
    return template_response(
        "wishlist_page/wishlist.html",
        request,
        user,
        {
            "results": results,
            "page": "downloaded" if downloaded else "wishlist",
            "counts": counts,
            "update_tablist": True,
        },
        block_name="book_wishlist",
    )

