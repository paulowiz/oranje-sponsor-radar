import hashlib
import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import TypeAdapter
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from radar.db import Job, PipelineRun, Snapshot, Sponsor, SponsorEvent, check_dataset, demo_mode
from radar.raw import REFRESH_LOCK_ID, archive_response, extract_operational_records
from radar.sources import (
    IND_URL,
    CompanyConfig,
    Vacancy,
    fetch_job_response,
    parse_jobs,
    parse_register,
)

logger = logging.getLogger(__name__)


def load_companies(path: Path) -> list[CompanyConfig]:
    companies = TypeAdapter(list[CompanyConfig]).validate_python(
        json.loads(path.read_text(encoding="utf-8"))
    )
    keys = [company.kvk for company in companies]
    boards = [(company.provider, company.board) for company in companies if company.provider]
    if len(keys) != len(set(keys)) or len(boards) != len(set(boards)):
        raise ValueError("Duplicate KVK or ATS board mappings are not allowed")
    return companies


def apply_register(
    session: Session,
    records: dict[str, str],
    observed_at: datetime,
    source_hash: str,
    max_removal_fraction: float = 0.1,
) -> None:
    day = observed_at.date()
    existing = {sponsor.id: sponsor for sponsor in session.scalars(select(Sponsor))}
    active = {kvk for kvk, sponsor in existing.items() if sponsor.recognized}
    missing = active - records.keys()
    latest = session.scalar(select(func.max(Snapshot.day)))
    if latest and day < latest:
        raise ValueError("Cannot apply an observation older than the latest snapshot")
    if active and len(missing) / len(active) > max_removal_fraction:
        raise ValueError("Register removal threshold exceeded; review source before retrying")
    baseline = not existing
    changes: list[tuple[str, str]] = []
    for key, name in records.items():
        sponsor = existing.get(key)
        kind = None
        if sponsor is None:
            sponsor = Sponsor(
                id=key,
                kvk=key if key.isdigit() else None,
                name=name,
                recognized=True,
                first_seen=day,
                last_seen=day,
            )
            session.add(sponsor)
            kind = "baseline" if baseline else "added"
        elif not sponsor.recognized:
            kind = "restored"
        sponsor.name = name
        sponsor.recognized = True
        sponsor.last_seen = day
        sponsor.removed_on = None
        if kind:
            changes.append((key, kind))
    for kvk in missing:
        existing[kvk].recognized = False
        existing[kvk].removed_on = day
        changes.append((kvk, "removed"))
    session.flush()
    session.add_all(
        SponsorEvent(sponsor_id=key, observed_on=day, observed_at=observed_at, kind=kind)
        for key, kind in changes
    )
    session.flush()
    counts = Counter(
        session.scalars(select(SponsorEvent.kind).where(SponsorEvent.observed_on == day))
    )
    job_count = active_job_count(session)
    snapshot = session.get(Snapshot, day)
    if snapshot is None:
        snapshot = Snapshot(day=day)
        session.add(snapshot)
    snapshot.sponsor_count = len(records)
    snapshot.added_count = counts["added"]
    snapshot.removed_count = counts["removed"]
    snapshot.restored_count = counts["restored"]
    snapshot.active_job_count = job_count
    snapshot.source_hash = source_hash
    snapshot.source_url = IND_URL
    snapshot.observed_at = observed_at


def active_job_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .join(Sponsor)
            .where(Job.active.is_(True), Sponsor.recognized.is_(True))
        )
        or 0
    )


def apply_jobs(
    session: Session, config: CompanyConfig, vacancies: list[Vacancy], now: datetime
) -> None:
    existing = {
        job.id: job
        for job in session.scalars(
            select(Job).where(
                Job.sponsor_id == config.kvk,
                Job.provider == config.provider,
                Job.board == config.board,
            )
        )
    }
    seen = set()
    for vacancy in vacancies:
        job_id = f"{config.provider}:{config.board}:{vacancy.external_id}"
        seen.add(job_id)
        job = existing.get(job_id)
        if job is None:
            job = Job(
                id=job_id,
                sponsor_id=config.kvk,
                provider=config.provider,
                board=config.board,
                first_seen=now,
            )
            session.add(job)
        job.title, job.location = vacancy.title, vacancy.location
        job.job_type, job.url = vacancy.job_type, vacancy.url
        job.active, job.last_seen = True, now
        job.sponsorship = "not_stated"
    for job_id, job in existing.items():
        if job_id not in seen:
            job.active = False


def refresh(engine, config_path: Path, client: httpx.Client | None = None) -> bool:
    if demo_mode():
        raise ValueError("Live refresh is disabled in demo mode; use a separate live database")
    check_dataset(engine)
    # Hold a database-level lock across the complete run, including HTTP requests.
    with engine.connect() as connection:
        postgres = engine.dialect.name == "postgresql"
        if postgres:
            acquired = connection.scalar(text(f"SELECT pg_try_advisory_lock({REFRESH_LOCK_ID})"))
            connection.commit()
            if not acquired:
                raise RuntimeError("Another refresh is already running")
        try:
            return _refresh(engine, config_path, client)
        finally:
            if postgres:
                connection.execute(text(f"SELECT pg_advisory_unlock({REFRESH_LOCK_ID})"))
                connection.commit()


def _refresh(engine, config_path: Path, client: httpx.Client | None) -> bool:
    now = datetime.now(UTC)
    with Session(engine) as session:
        run = PipelineRun(started_at=now, status="running", errors=[], boards_succeeded=0)
        session.add(run)
        session.commit()
        run_id = run.id
    own_client = client is None
    client = client or httpx.Client(
        timeout=60, follow_redirects=False, headers={"User-Agent": "OranjeSponsorRadar/0.1"}
    )
    errors: list[str] = []
    succeeded = 0
    status = "failed"
    try:
        companies = load_companies(config_path)
        response = client.get(IND_URL)
        archive_response(engine, response, run_id, "ind")
        response.raise_for_status()
        records = parse_register(response.text)
        digest = hashlib.sha256(response.content).hexdigest()
        with Session(engine) as session, session.begin():
            apply_register(session, records, now, digest)
            for config in companies:
                sponsor = session.get(Sponsor, config.kvk)
                if sponsor is None:
                    raise ValueError(f"Configured KVK {config.kvk} is absent from sponsor history")
                sponsor.city = config.city
                if config.industry is not None:
                    sponsor.industry = config.industry
                sponsor.latitude, sponsor.longitude = config.latitude, config.longitude
                if config.careers_url is not None:
                    sponsor.careers_url = str(config.careers_url)
        for config in companies:
            if not config.provider or config.kvk not in records:
                continue
            try:
                response = fetch_job_response(client, config)
                archive_response(engine, response, run_id, config.provider)
                response.raise_for_status()
                vacancies = parse_jobs(response.json(), config)
                with Session(engine) as session, session.begin():
                    apply_jobs(session, config, vacancies, now)
                succeeded += 1
            except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
                message = f"{config.kvk} ({config.provider}/{config.board}): {exc}"
                logger.exception("ATS refresh failed: %s", message)
                errors.append(message)
        with Session(engine) as session, session.begin():
            snapshot = session.get(Snapshot, now.date())
            snapshot.active_job_count = active_job_count(session)
            session.flush()
            extract_operational_records(session, run_id)
        status = "partial" if errors else "success"
    except Exception as exc:
        # Persist the failed run, then re-raise so the scheduler/CLI exits unsuccessfully.
        logger.exception("Pipeline failed")
        errors.append(str(exc))
        raise
    finally:
        if own_client:
            client.close()
        with Session(engine) as session, session.begin():
            run = session.get(PipelineRun, run_id)
            run.status, run.errors = status, errors
            run.boards_succeeded = succeeded
            run.finished_at = datetime.now(UTC)
    return status == "success"
