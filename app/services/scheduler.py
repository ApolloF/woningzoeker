from __future__ import annotations

import logging

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
        self.scheduler.add_job(
            self.refresh_jobs,
            "interval",
            seconds=60,
            id="system:refresh-sources",
            name="Bronconfiguratie vernieuwen",
            max_instances=1,
            coalesce=True,
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
        wanted: set[str] = set()
        for source in sources:
            job_id = f"source:{source.name}"
            if not source.enabled:
                continue
            wanted.add(job_id)
            self.scheduler.add_job(
                self.pipeline.run_source,
                "interval",
                args=[source.name],
                seconds=max(60, source.poll_interval_seconds),
                id=job_id,
                name=source.display_name,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
                replace_existing=True,
            )
        for job in self.scheduler.get_jobs():
            if job.id.startswith("source:") and job.id not in wanted:
                self.scheduler.remove_job(job.id)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
