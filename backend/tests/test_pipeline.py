import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from radar.db import Dataset, Job, PipelineRun, Snapshot, Sponsor, SponsorEvent, check_dataset
from radar.pipeline import apply_jobs, apply_register, load_companies, refresh
from radar.sources import Vacancy
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.test_sources import company, register_html

NOW = datetime(2026, 10, 15, 4, tzinfo=UTC)
RECORDS = {f"{i:08d}": f"Company {i}" for i in range(1, 11)}


def observe(engine, records, now=NOW, **kwargs):
    with Session(engine) as session, session.begin():
        apply_register(session, records, now, "hash", **kwargs)


def test_lifecycle_baseline_add_remove_restore_and_idempotent_daily_snapshot(engine):
    observe(engine, RECORDS)
    next_records = {k: v for k, v in RECORDS.items() if k != "00000001"}
    next_records["00000011"] = "New company"
    tomorrow = NOW + timedelta(days=1)
    observe(engine, next_records, tomorrow)
    observe(engine, next_records, tomorrow + timedelta(hours=1))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 2
        snapshot = session.get(Snapshot, tomorrow.date())
        assert (snapshot.sponsor_count, snapshot.added_count, snapshot.removed_count) == (10, 1, 1)
        assert session.get(Sponsor, "00000001").recognized is False
        assert session.get(Snapshot, NOW.date()).added_count == 0
        assert session.scalar(select(func.count()).select_from(SponsorEvent)) == 12
    restored = {**next_records, "00000001": "Company 1 renamed"}
    observe(engine, restored, tomorrow + timedelta(days=1))
    with Session(engine) as session:
        sponsor = session.get(Sponsor, "00000001")
        assert sponsor.recognized and sponsor.removed_on is None
        assert sponsor.first_seen == NOW.date()
        assert sponsor.name == "Company 1 renamed"
        assert session.get(Snapshot, (tomorrow + timedelta(days=1)).date()).restored_count == 1


def test_repeated_same_day_transitions_reconcile(engine):
    observe(engine, RECORDS)
    without_one = {k: v for k, v in RECORDS.items() if k != "00000001"}
    tomorrow = NOW + timedelta(days=1)
    for hour, records in enumerate([without_one, RECORDS, without_one, RECORDS]):
        observe(engine, records, tomorrow + timedelta(hours=hour))
    with Session(engine) as session:
        snapshot = session.get(Snapshot, tomorrow.date())
        assert (snapshot.removed_count, snapshot.restored_count) == (2, 2)
        assert snapshot.sponsor_count == 10


def test_exact_removal_threshold_and_rollback(engine):
    observe(engine, RECORDS)
    with pytest.raises(ValueError, match="threshold"):
        observe(engine, dict(list(RECORDS.items())[2:]), NOW + timedelta(days=1))
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Sponsor).where(Sponsor.recognized.is_(True))
            )
            == 10
        )
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 1
    observe(engine, dict(list(RECORDS.items())[1:]), NOW + timedelta(days=1))


def test_out_of_order_observations_rejected(engine):
    observe(engine, RECORDS)
    with pytest.raises(ValueError, match="older"):
        observe(engine, RECORDS, NOW - timedelta(days=1))


def test_jobs_upsert_and_close_only_after_complete_success(engine):
    observe(engine, RECORDS)
    config = company()
    vacancy = Vacancy("1", "Engineer", "Amsterdam", "full-time", "https://example.com/job")
    with Session(engine) as session, session.begin():
        apply_jobs(session, config, [vacancy], NOW)
    with Session(engine) as session, session.begin():
        apply_jobs(session, config, [vacancy], NOW + timedelta(hours=1))
    with Session(engine) as session:
        job = session.scalar(select(Job))
        assert job.active and job.sponsorship == "not_stated"
        assert session.scalar(select(func.count()).select_from(Job)) == 1
    with Session(engine) as session, session.begin():
        apply_jobs(session, config, [], NOW + timedelta(hours=2))
    with Session(engine) as session:
        assert session.scalar(select(Job)).active is False


def test_failed_board_retains_previous_jobs_and_records_partial_run(engine, tmp_path):
    config = company()
    path = tmp_path / "companies.json"
    path.write_text(json.dumps([config.model_dump(mode="json")]))
    records = {f"{i:08d}": f"Company {i}" for i in range(1, 1002)}
    observe(engine, records, datetime.now(UTC))
    with Session(engine) as session, session.begin():
        apply_jobs(
            session,
            config,
            [Vacancy("1", "Engineer", "Amsterdam", "unknown", "https://example.com/job")],
            datetime.now(UTC),
        )

    def handler(request):
        if request.url.host == "ind.nl":
            return httpx.Response(200, text=register_html([(v, k) for k, v in records.items()]))
        return httpx.Response(503, text="temporarily unavailable")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert refresh(engine, path, client) is False
    with Session(engine) as session:
        assert session.scalar(select(Job)).active
        run = session.scalar(select(PipelineRun))
        assert run.status == "partial"
        assert "503" in run.errors[0]
        assert session.scalar(select(Snapshot).order_by(Snapshot.day.desc())).active_job_count == 1


def test_register_failure_is_audited_without_mutating_sponsors(engine, tmp_path):
    observe(engine, RECORDS)
    path = tmp_path / "companies.json"
    path.write_text("[]")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>maintenance</html>")
        )
    ) as client:
        with pytest.raises(ValueError):
            refresh(engine, path, client)
    with Session(engine) as session:
        assert session.scalar(select(PipelineRun)).status == "failed"
        assert session.scalar(select(func.count()).select_from(Sponsor)) == 10


def test_config_rejects_duplicate_identity_or_board(tmp_path):
    path = tmp_path / "companies.json"
    config = company().model_dump(mode="json")
    path.write_text(json.dumps([config, config]))
    with pytest.raises(ValueError, match="Duplicate"):
        load_companies(path)


def test_database_mode_cannot_be_relabelled(engine, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    with pytest.raises(ValueError, match="cannot be mixed"):
        check_dataset(engine)
    with Session(engine) as session:
        assert session.get(Dataset, 1).mode == "live"


@pytest.mark.parametrize("has_curated_details", [False, True])
def test_location_only_refresh_preserves_unknown_or_curated_details(
    engine, tmp_path, has_curated_details
):
    records = {f"{i:08d}": f"Company {i}" for i in range(1, 1002)}
    observe(engine, records, datetime.now(UTC))
    industry = "Curated industry" if has_curated_details else None
    careers_url = "https://example.com/careers" if has_curated_details else None
    with Session(engine) as session, session.begin():
        sponsor = session.get(Sponsor, "00000001")
        sponsor.industry, sponsor.careers_url = industry, careers_url
    path = tmp_path / "companies.json"
    path.write_text(
        json.dumps([{"kvk": "00000001", "city": "Amsterdam", "latitude": 52.37, "longitude": 4.90}])
    )

    def handler(request):
        assert request.url.host == "ind.nl"
        return httpx.Response(200, text=register_html([(v, k) for k, v in records.items()]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert refresh(engine, path, client)
    with Session(engine) as session:
        sponsor = session.get(Sponsor, "00000001")
        assert (sponsor.city, sponsor.latitude, sponsor.longitude) == ("Amsterdam", 52.37, 4.90)
        assert sponsor.industry == industry and sponsor.careers_url == careers_url
        assert session.scalar(select(func.count()).select_from(Job)) == 0
