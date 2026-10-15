from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.orm import Session

from radar.db import (
    Job,
    PipelineRun,
    Snapshot,
    Sponsor,
    SponsorEvent,
    check_dataset,
    demo_mode,
    make_engine,
)
from radar.sources import IND_URL

JOB_FRESHNESS_HOURS = 72


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    location: str
    job_type: str
    url: str
    last_seen: datetime
    sponsorship: str


class SponsorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kvk: str | None
    name: str
    city: str | None
    industry: str | None
    latitude: float | None
    longitude: float | None
    careers_url: str | None
    first_seen: date
    last_seen: date
    jobs: list[JobOut] = []
    job_count: int = 0
    newly_observed: bool = False


def create_app(engine=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = engine or make_engine()
        check_dataset(app.state.engine)
        yield
        if engine is None:
            app.state.engine.dispose()

    app = FastAPI(title="Oranje Sponsor Radar", lifespan=lifespan)

    def get_session():
        with Session(app.state.engine) as session:
            yield session

    Database = Annotated[Session, Depends(get_session)]

    @app.get("/api/health")
    def health(session: Database):
        session.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta(session: Database):
        latest = session.scalar(select(Snapshot).order_by(Snapshot.day.desc()).limit(1))
        run = session.scalar(select(PipelineRun).order_by(PipelineRun.id.desc()).limit(1))
        sponsors = session.scalar(
            select(func.count()).select_from(Sponsor).where(Sponsor.recognized.is_(True))
        )
        mapped = session.scalar(
            select(func.count())
            .select_from(Sponsor)
            .where(
                Sponsor.recognized.is_(True),
                Sponsor.latitude.is_not(None),
                Sponsor.longitude.is_not(None),
            )
        )
        fresh = datetime.now(UTC) - timedelta(hours=JOB_FRESHNESS_HOURS)
        jobs = session.scalar(
            select(func.count())
            .select_from(Job)
            .join(Sponsor)
            .where(Sponsor.recognized.is_(True), Job.active.is_(True), Job.last_seen >= fresh)
        )
        return {
            "demo": demo_mode(),
            "source_url": IND_URL,
            "last_registry_check": latest.observed_at if latest else None,
            "registry_stale": (
                not latest or latest.day < datetime.now(UTC).date() - timedelta(days=2)
            ),
            "sponsors": sponsors,
            "mapped_sponsors": mapped,
            "fresh_jobs": jobs,
            "job_freshness_hours": JOB_FRESHNESS_HOURS,
            "latest_run": {
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "errors": run.errors,
                "boards_succeeded": run.boards_succeeded,
            }
            if run
            else None,
            "cities": list(
                session.scalars(
                    select(Sponsor.city)
                    .where(Sponsor.recognized.is_(True), Sponsor.city.is_not(None))
                    .distinct()
                    .order_by(Sponsor.city)
                )
            ),
            "industries": list(
                session.scalars(
                    select(Sponsor.industry)
                    .where(Sponsor.recognized.is_(True), Sponsor.industry.is_not(None))
                    .distinct()
                    .order_by(Sponsor.industry)
                )
            ),
        }

    @app.get("/api/sponsors")
    def sponsors(
        session: Database,
        q: str = Query(default="", max_length=200),
        city: str | None = Query(default=None, max_length=100),
        industry: str | None = Query(default=None, max_length=100),
        job_type: str | None = Query(
            default=None, pattern="^(full-time|part-time|internship|contract|unknown)$"
        ),
        has_jobs: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        job_filters = [
            Job.active.is_(True),
            Job.last_seen >= datetime.now(UTC) - timedelta(hours=JOB_FRESHNESS_HOURS),
        ]
        if job_type:
            job_filters.append(Job.job_type == job_type)
        # Escape LIKE metacharacters: a search is literal text, not a SQL pattern.
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        name_matches = Sponsor.name.ilike(f"%{term}%", escape="\\")
        if term:
            job_filters.append(or_(name_matches, Job.title.ilike(f"%{term}%", escape="\\")))
        matching_job = exists(select(Job.id).where(Job.sponsor_id == Sponsor.id, *job_filters))
        filters = [Sponsor.recognized.is_(True)]
        if city:
            filters.append(Sponsor.city == city)
        if industry:
            filters.append(Sponsor.industry == industry)
        if term:
            filters.append(or_(name_matches, matching_job))
        if has_jobs or job_type:
            filters.append(matching_job)
        total = session.scalar(select(func.count()).select_from(Sponsor).where(*filters))
        rows = session.scalars(
            select(Sponsor)
            .where(*filters)
            .order_by(Sponsor.name, Sponsor.id)
            .limit(limit)
            .offset(offset)
        ).all()
        keys = [row.id for row in rows]
        newly_observed = set(
            session.scalars(
                select(SponsorEvent.sponsor_id).where(
                    SponsorEvent.sponsor_id.in_(keys),
                    SponsorEvent.kind == "added",
                    SponsorEvent.observed_on >= datetime.now(UTC).date() - timedelta(days=29),
                )
            )
        )
        jobs_by_company: dict[str, list[JobOut]] = {kvk: [] for kvk in keys}
        if keys:
            jobs = session.scalars(
                select(Job)
                .join(Sponsor)
                .where(Job.sponsor_id.in_(keys), *job_filters)
                .order_by(Job.title, Job.id)
            )
            for job in jobs:
                jobs_by_company[job.sponsor_id].append(JobOut.model_validate(job))
        items = []
        for row in rows:
            item = SponsorOut.model_validate(row)
            item.newly_observed = row.id in newly_observed
            item.job_count = len(jobs_by_company[row.id])
            item.jobs = jobs_by_company[row.id][:20]
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/history")
    def history(session: Database, days: int = Query(default=30, ge=1, le=365)):
        since = datetime.now(UTC).date() - timedelta(days=days - 1)
        snapshots = session.scalars(
            select(Snapshot).where(Snapshot.day >= since).order_by(Snapshot.day)
        ).all()
        events = session.execute(
            select(SponsorEvent, Sponsor.name)
            .join(Sponsor)
            .where(SponsorEvent.observed_on >= since, SponsorEvent.kind != "baseline")
            .order_by(SponsorEvent.observed_on.desc(), SponsorEvent.id.desc())
            .limit(100)
        )
        return {
            "snapshots": [
                {
                    "day": row.day,
                    "sponsors": row.sponsor_count,
                    "added": row.added_count,
                    "removed": row.removed_count,
                    "restored": row.restored_count,
                    "active_jobs": row.active_job_count,
                }
                for row in snapshots
            ],
            "events": [
                {
                    "sponsor_id": event.sponsor_id,
                    "name": name,
                    "day": event.observed_on,
                    "kind": event.kind,
                }
                for event, name in events
            ],
            "events_limit": 100,
        }

    return app


app = create_app()
