from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.scheduler as scheduler_module
from app.db import Base
from app.models import SourceConfig
from app.services.scheduler import SourceScheduler


class FakePipeline:
    def run_source(self, _: str) -> dict[str, Any]:
        return {}

    def retry_failed_reactions(self) -> list[dict[str, Any]]:
        return []


def test_refresh_keeps_existing_schedule_and_staggers_sources(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(scheduler_module, "SessionLocal", testing_session)
    with testing_session() as db:
        db.add_all(
            [
                SourceConfig(name="one", display_name="One", base_url="https://one.test"),
                SourceConfig(name="two", display_name="Two", base_url="https://two.test"),
                SourceConfig(name="three", display_name="Three", base_url="https://three.test"),
            ]
        )
        db.commit()

    source_scheduler = SourceScheduler(FakePipeline())  # type: ignore[arg-type]
    source_scheduler.scheduler.start(paused=True)
    try:
        source_scheduler.refresh_jobs()
        jobs = sorted(source_scheduler.scheduler.get_jobs(), key=lambda job: job.id)
        first_times = {job.id: job.next_run_time for job in jobs}
        assert len(set(first_times.values())) == 3

        source_scheduler.refresh_jobs()
        second_times = {
            job.id: job.next_run_time for job in source_scheduler.scheduler.get_jobs()
        }
        assert second_times == first_times
    finally:
        source_scheduler.shutdown()
