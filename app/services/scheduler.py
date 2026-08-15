from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app.db import SessionLocal
from app.models import SourceConfig
from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)


class SourceScheduler:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline
        self.scheduler = BackgroundScheduler(timezone="Europe/Amsterdam")

    def start(self) -> None:
        self.refresh_jobs()
        now = datetime.now(self.scheduler.timezone)
        self.scheduler.add_job(
            self.refresh_jobs,
            "interval",
            seconds=60,
            id="system:refresh-sources",
            name="Bronconfiguratie vernieuwen",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
            next_run_time=now + timedelta(seconds=30),
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.pipeline.retry_failed_reactions,
            "interval",
            seconds=180,
            id="system:retry-reactions",
            name="Mislukte reacties opnieuw proberen",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            next_run_time=now + timedelta(seconds=90),
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.pipeline.dispatch_pending_auto_reactions,
            "interval",
            seconds=30,
            id="system:dispatch-pending-reactions",
            name="Nieuwe auto-react kandidaten direct uitvoeren",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=20,
            next_run_time=now + timedelta(seconds=10),
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(
            "source scheduler started",
            extra={"context": {"jobs": len(self.scheduler.get_jobs())}},
        )

    def refresh_jobs(self) -> None:
        with SessionLocal() as db:
            sources = db.scalars(select(SourceConfig)).all()
        enabled_sources = [source for source in sources if source.enabled]
        wanted: set[str] = set()
        now = datetime.now(self.scheduler.timezone)
        spread_seconds = max(1, 55 // max(1, len(enabled_sources) - 1))
        for index, source in enumerate(enabled_sources):
            job_id = f"source:{source.name}"
            wanted.add(job_id)
            interval_seconds = max(60, source.poll_interval_seconds)
            existing = self.scheduler.get_job(job_id)
            if existing is not None:
                current_interval = getattr(existing.trigger, "interval", None)
                if current_interval and int(current_interval.total_seconds()) != interval_seconds:
                    self.scheduler.reschedule_job(job_id, trigger="interval", seconds=interval_seconds)
                continue
            self.scheduler.add_job(
                self.pipeline.run_source,
                "interval",
                args=[source.name],
                seconds=interval_seconds,
                id=job_id,
                name=source.display_name,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
                next_run_time=now + timedelta(seconds=index * spread_seconds),
                replace_existing=True,
            )
        for job in self.scheduler.get_jobs():
            if job.id.startswith("source:") and job.id not in wanted:
                self.scheduler.remove_job(job.id)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
