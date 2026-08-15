from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.logging_config import configure_logging
from app.models import ApplicantProfile, AuditEvent, Decision, Listing, SearchConfig, SourceConfig
from app.schemas import ApplicantProfileData, Criteria
from app.security import LOGIN_CSRF_COOKIE, SESSION_COOKIE, AuthManager
from app.seed import seed_defaults
from app.services.audit import add_audit
from app.services.pipeline import Pipeline
from app.services.scheduler import SourceScheduler

settings = get_settings()
configure_logging(settings.log_level)
auth = AuthManager(settings)
pipeline = Pipeline()
scheduler = SourceScheduler(pipeline)
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    with SessionLocal() as db:
        seed_defaults(db)
    if settings.scheduler_enabled:
        scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="Woningzoeker", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def page_context(request: Request, **values: object) -> dict[str, object]:
    session = auth.load_session(request)
    return {
        "request": request,
        "session": session,
        "csrf_token": session.csrf_token if session else "",
        "dry_run": settings.dry_run,
        **values,
    }


@app.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "time": datetime.now(UTC).isoformat(), "dry_run": settings.dry_run}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if auth.load_session(request):
        return RedirectResponse("/", status_code=303)
    csrf = auth.create_login_csrf()
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "login_csrf": csrf, "error": None},
    )
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        csrf,
        max_age=600,
        httponly=True,
        secure=settings.dashboard_cookie_secure,
        samesite="lax",
    )
    return response


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
) -> Response:
    if not auth.verify_login_csrf(request, csrf_token) or not auth.verify_password(password):
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"request": request, "login_csrf": csrf_token, "error": "Ongeldige login."},
            status_code=401,
        )
        return response
    token, _ = auth.create_session()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        secure=settings.dashboard_cookie_secure,
        samesite="lax",
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE)
    return response


@app.post("/logout")
def logout(request: Request, csrf_token: Annotated[str, Form()]) -> Response:
    auth.verify_csrf(request, csrf_token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    auth.require_session(request)
    listings = db.scalars(
        select(Listing).options(selectinload(Listing.source)).order_by(desc(Listing.first_seen_at)).limit(100)
    ).all()
    sources = db.scalars(select(SourceConfig).order_by(SourceConfig.display_name)).all()
    events = db.scalars(select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(50)).all()
    counts = dict(db.execute(select(Listing.decision, func.count()).group_by(Listing.decision)).all())
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(hours=2)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            request,
            listings=listings,
            sources=sources,
            events=events,
            counts=counts,
            stale_cutoff=stale_cutoff,
            telegram_configured=bool(settings.telegram_bot_token and settings.telegram_chat_id),
        ),
    )


@app.get("/listings/{listing_id}", response_class=HTMLResponse)
def listing_detail(listing_id: int, request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    auth.require_session(request)
    listing = db.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .options(
            selectinload(Listing.source),
            selectinload(Listing.canonical_property).selectinload(type(Listing).source),
        )
    )
    # The nested loader above is intentionally avoided for cross-source records; query explicitly.
    if not listing:
        raise HTTPException(status_code=404, detail="listing not found")
    related = db.scalars(
        select(Listing)
        .where(Listing.canonical_property_id == listing.canonical_property_id)
        .options(selectinload(Listing.source))
        .order_by(Listing.first_seen_at)
    ).all()
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.listing_id == listing_id).order_by(desc(AuditEvent.created_at))
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context=page_context(request, listing=listing, related=related, events=events),
    )


@app.post("/listings/{listing_id}/ignore")
def ignore_listing(
    listing_id: int,
    request: Request,
    csrf_token: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="listing not found")
    listing.decision = Decision.IGNORE.value
    listing.reasoning_summary = "Handmatig genegeerd door de beheerder."
    add_audit(
        db,
        "LISTING_IGNORED_MANUALLY",
        "Advertentie handmatig genegeerd",
        listing_id=listing.id,
        source_id=listing.source_id,
    )
    db.commit()
    return RedirectResponse(f"/listings/{listing_id}", status_code=303)


@app.post("/admin/run-all")
def run_all(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: Annotated[str, Form()],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    background_tasks.add_task(pipeline.run_all)
    return RedirectResponse("/", status_code=303)


@app.post("/sources/{source_id}/toggle")
def toggle_source(
    source_id: int,
    request: Request,
    csrf_token: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    source = db.get(SourceConfig, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="source not found")
    source.enabled = not source.enabled
    add_audit(
        db,
        "SOURCE_TOGGLED",
        f"{source.display_name} {'ingeschakeld' if source.enabled else 'gepauzeerd'}",
        source_id=source.id,
    )
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    auth.require_session(request)
    criteria_record = db.get(SearchConfig, 1)
    profile_record = db.get(ApplicantProfile, 1)
    if not criteria_record or not profile_record:
        raise HTTPException(status_code=500, detail="defaults missing")
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=page_context(
            request,
            criteria=Criteria.model_validate(criteria_record.config),
            profile=ApplicantProfileData.model_validate(profile_record.profile),
            telegram_configured=bool(settings.telegram_bot_token and settings.telegram_chat_id),
        ),
    )


@app.post("/settings/criteria")
def update_criteria(
    request: Request,
    csrf_token: Annotated[str, Form()],
    accepted_cities: Annotated[str, Form()],
    min_area_m2: Annotated[float, Form()],
    target_total_monthly: Annotated[float, Form()],
    soft_price_margin: Annotated[float, Form()],
    allow_shared_rooms: Annotated[bool, Form()] = False,
    db: Session = Depends(get_db),
) -> Response:
    auth.verify_csrf(request, csrf_token)
    record = db.get(SearchConfig, 1)
    assert record is not None
    current = Criteria.model_validate(record.config)
    updated = current.model_copy(
        update={
            "accepted_cities": [item.strip() for item in accepted_cities.split(",") if item.strip()],
            "min_area_m2": min_area_m2,
            "target_total_monthly": target_total_monthly,
            "soft_price_margin": soft_price_margin,
            "allow_shared_rooms": allow_shared_rooms,
        }
    )
    record.config = updated.model_dump(mode="json")
    add_audit(db, "CRITERIA_UPDATED", "Zoekcriteria bijgewerkt")
    db.commit()
    return RedirectResponse("/settings", status_code=303)
