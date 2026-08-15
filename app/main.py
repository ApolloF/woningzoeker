"""ASGI entrypoint and production route overrides."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.assistance_models import AssistanceRequest, AssistanceState
from app.db import get_db
from app.main_base import app, auth, page_context, pipeline, settings, templates
from app.models import (
    ApplicantProfile,
    AuditEvent,
    Listing,
    SearchConfig,
    SourceConfig,
    SourceMode,
    Submission,
)
from app.schemas import ApplicantProfileData, Criteria, PrivateContactData, SourceCredentialData
from app.services.audit import add_audit

reaction_service = pipeline.reaction_service
ACCOUNT_SOURCES = {"huurwoningen", "pararius", "woldring", "campus_groningen"}
SESSION_SOURCES = {"huurwoningen", "pararius"}

# Replace legacy implementations that cannot expose reaction/assistance state.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if not (
        (getattr(route, "path", None) == "/" and "GET" in getattr(route, "methods", set()))
        or (
            getattr(route, "path", None) == "/listings/{listing_id}"
            and "GET" in getattr(route, "methods", set())
        )
        or (getattr(route, "path", None) == "/settings" and "GET" in getattr(route, "methods", set()))
    )
]


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    auth.require_session(request)
    listings = db.scalars(
        select(Listing)
        .options(selectinload(Listing.source))
        .order_by(desc(Listing.first_seen_at))
        .limit(100)
    ).all()
    sources = db.scalars(select(SourceConfig).order_by(SourceConfig.display_name)).all()
    events = db.scalars(select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(50)).all()
    counts = dict(db.execute(select(Listing.decision, func.count()).group_by(Listing.decision)).all())
    assistance_count = db.scalar(
        select(func.count())
        .select_from(AssistanceRequest)
        .where(AssistanceRequest.state == AssistanceState.OPEN.value)
    )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            request,
            listings=listings,
            sources=sources,
            events=events,
            counts=counts,
            assistance_count=assistance_count or 0,
            readiness=reaction_service.readiness(),
            telegram_configured=bool(settings.telegram_bot_token and settings.telegram_chat_id),
        ),
    )


@app.get("/listings/{listing_id}", response_class=HTMLResponse)
def listing_detail(
    listing_id: int, request: Request, db: Annotated[Session, Depends(get_db)]
) -> Response:
    auth.require_session(request)
    listing = db.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.source), selectinload(Listing.canonical_property))
    )
    if not listing:
        raise HTTPException(status_code=404, detail="listing not found")
    related = db.scalars(
        select(Listing)
        .where(Listing.canonical_property_id == listing.canonical_property_id)
        .options(selectinload(Listing.source))
        .order_by(Listing.first_seen_at)
    ).all()
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.listing_id == listing_id)
        .order_by(desc(AuditEvent.created_at))
    ).all()
    submission = db.scalar(
        select(Submission).where(
            Submission.canonical_property_id == listing.canonical_property_id
        )
    )
    assistance = None
    if submission:
        assistance = db.scalar(
            select(AssistanceRequest).where(
                AssistanceRequest.submission_id == submission.id,
                AssistanceRequest.state == AssistanceState.OPEN.value,
            )
        )
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context=page_context(
            request,
            listing=listing,
            related=related,
            events=events,
            submission=submission,
            assistance=assistance,
            auto_react_enabled=settings.auto_react_enabled,
        ),
    )


@app.post("/listings/{listing_id}/prepare-reaction")
def prepare_reaction(
    listing_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    if db.get(Listing, listing_id) is None:
        raise HTTPException(status_code=404, detail="listing not found")
    background_tasks.add_task(reaction_service.dispatch, listing_id, force=True)
    return RedirectResponse(f"/listings/{listing_id}", status_code=303)


@app.get("/assistance", response_class=HTMLResponse)
def assistance_page(request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    auth.require_session(request)
    rows = db.execute(
        select(AssistanceRequest, Listing, SourceConfig, Submission)
        .join(Listing, Listing.id == AssistanceRequest.listing_id)
        .join(SourceConfig, SourceConfig.id == AssistanceRequest.source_id)
        .join(Submission, Submission.id == AssistanceRequest.submission_id)
        .where(AssistanceRequest.state == AssistanceState.OPEN.value)
        .order_by(AssistanceRequest.created_at)
    ).all()
    items = [
        {"assistance": item, "listing": listing, "source": source, "submission": submission}
        for item, listing, source, submission in rows
    ]
    return templates.TemplateResponse(
        request=request,
        name="assistance.html",
        context=page_context(request, items=items),
    )


@app.post("/assistance/{assistance_id}/confirm")
def confirm_assistance(
    assistance_id: int,
    request: Request,
    csrf_token: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> Response:
    auth.verify_csrf(request, csrf_token)
    outcome = reaction_service.assistance.confirm_manual_submission(assistance_id, note)
    if not outcome.ok:
        raise HTTPException(status_code=409, detail=outcome.code)
    return RedirectResponse("/assistance", status_code=303)


@app.post("/assistance/{assistance_id}/skip")
def skip_assistance(
    assistance_id: int,
    request: Request,
    csrf_token: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> Response:
    auth.verify_csrf(request, csrf_token)
    outcome = reaction_service.assistance.skip(assistance_id, note)
    if not outcome.ok:
        raise HTTPException(status_code=409, detail=outcome.code)
    return RedirectResponse("/assistance", status_code=303)


@app.post("/assistance/{assistance_id}/retry")
def retry_assistance(
    assistance_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    assistance = db.get(AssistanceRequest, assistance_id)
    if assistance is None or assistance.state != AssistanceState.OPEN.value:
        raise HTTPException(status_code=404, detail="open assistance request not found")
    background_tasks.add_task(reaction_service.dispatch, assistance.listing_id, force=True)
    return RedirectResponse("/assistance", status_code=303)


@app.get("/submissions/{submission_id}/artifact/{kind}")
def submission_artifact(
    submission_id: int,
    kind: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    auth.require_session(request)
    if kind not in {"before", "after"}:
        raise HTTPException(status_code=404, detail="artifact not found")
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    raw_path = submission.before_screenshot if kind == "before" else submission.after_screenshot
    if not raw_path:
        raise HTTPException(status_code=404, detail="artifact not found")
    artifact_root = Path(settings.reaction_artifact_dir).resolve()
    artifact_path = Path(raw_path).resolve()
    if artifact_root not in artifact_path.parents or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(artifact_path, media_type="image/png", filename=f"{kind}.png")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    auth.require_session(request)
    criteria_record = db.get(SearchConfig, 1)
    profile_record = db.get(ApplicantProfile, 1)
    if not criteria_record or not profile_record:
        raise HTTPException(status_code=500, detail="defaults missing")
    models = settings.llm_models() if settings.llm_provider != "disabled" else ()
    sources = db.scalars(select(SourceConfig).order_by(SourceConfig.display_name)).all()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=page_context(
            request,
            criteria=Criteria.model_validate(criteria_record.config),
            profile=ApplicantProfileData.model_validate(profile_record.profile),
            telegram_configured=bool(settings.telegram_bot_token and settings.telegram_chat_id),
            llm_provider=settings.llm_provider,
            llm_models=models,
            auto_react_enabled=settings.auto_react_enabled,
            scheduler_enabled=settings.scheduler_enabled,
            contact_configured=reaction_service.contact_is_configured(),
            credential_statuses=reaction_service.credential_statuses(),
            account_sources=ACCOUNT_SOURCES,
            session_sources=SESSION_SOURCES,
            sources=sources,
            source_modes=[mode.value for mode in SourceMode],
            readiness=reaction_service.readiness(),
        ),
    )


@app.get("/api/automation/readiness")
def automation_readiness(request: Request) -> dict[str, object]:
    auth.require_session(request)
    return reaction_service.readiness()


@app.post("/settings/profile")
def update_applicant_profile(
    request: Request,
    csrf_token: Annotated[str, Form()],
    applicants: Annotated[str, Form()],
    current_city: Annotated[str, Form()],
    current_situation: Annotated[str, Form()],
    applicant_details: Annotated[str, Form()],
    financial_wording: Annotated[str, Form()],
    guarantor_wording: Annotated[str, Form()],
    lifestyle: Annotated[str, Form()],
    desired_tenure: Annotated[str, Form()],
    standard_message: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> Response:
    auth.verify_csrf(request, csrf_token)
    record = db.get(ApplicantProfile, 1)
    if record is None:
        raise HTTPException(status_code=500, detail="applicant profile missing")
    profile = ApplicantProfileData(
        applicants=[item.strip() for item in applicants.splitlines() if item.strip()],
        current_city=current_city.strip(),
        current_situation=current_situation.strip(),
        applicant_details=[item.strip() for item in applicant_details.splitlines() if item.strip()],
        financial_wording=financial_wording.strip(),
        guarantor_wording=guarantor_wording.strip(),
        lifestyle=[item.strip() for item in lifestyle.splitlines() if item.strip()],
        desired_tenure=desired_tenure.strip(),
        standard_message=standard_message.strip(),
    )
    record.profile = profile.model_dump(mode="json")
    add_audit(db, "APPLICANT_PROFILE_UPDATED", "Aanvragersprofiel bijgewerkt")
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/contact")
def update_private_contact(
    request: Request,
    csrf_token: Annotated[str, Form()],
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    initials: Annotated[str, Form()],
    email: Annotated[str, Form()],
    phone: Annotated[str, Form()],
    address: Annotated[str, Form()],
    house_number: Annotated[str, Form()],
    city: Annotated[str, Form()],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    reaction_service.save_contact(
        PrivateContactData(
            first_name=first_name,
            last_name=last_name,
            initials=initials,
            email=email,
            phone=phone,
            address=address,
            house_number=house_number,
            city=city,
        )
    )
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/credentials/{source_name}")
def update_source_credential(
    source_name: str,
    request: Request,
    csrf_token: Annotated[str, Form()],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    if source_name not in ACCOUNT_SOURCES:
        raise HTTPException(status_code=400, detail="source does not use managed credentials")
    reaction_service.save_credential(
        source_name,
        SourceCredentialData(username=username, password=password),
    )
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/sessions/{source_name}")
def update_source_session(
    source_name: str,
    request: Request,
    csrf_token: Annotated[str, Form()],
    storage_state_json: Annotated[str, Form()],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    if source_name not in SESSION_SOURCES:
        raise HTTPException(status_code=400, detail="source does not support browser sessions")
    try:
        storage_state = json.loads(storage_state_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="invalid browser session JSON") from exc
    if not isinstance(storage_state, dict):
        raise HTTPException(status_code=422, detail="browser session must be a JSON object")
    reaction_service.save_browser_session(source_name, storage_state)
    return RedirectResponse("/settings", status_code=303)


@app.post("/sources/{source_id}/mode")
def update_source_mode(
    source_id: int,
    request: Request,
    csrf_token: Annotated[str, Form()],
    mode: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    auth.verify_csrf(request, csrf_token)
    allowed = {item.value for item in SourceMode}
    if mode not in allowed:
        raise HTTPException(status_code=400, detail="invalid source mode")
    source = db.get(SourceConfig, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    source.mode = mode
    add_audit(db, "SOURCE_MODE_UPDATED", f"{source.display_name}: modus gewijzigd naar {mode}")
    db.commit()
    return RedirectResponse("/settings", status_code=303)


__all__ = ["app"]
