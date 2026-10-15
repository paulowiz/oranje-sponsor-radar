from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from radar.api import create_app
from radar.db import Sponsor
from radar.pipeline import apply_jobs, apply_register
from radar.sources import Vacancy
from sqlalchemy.orm import Session

from tests.test_sources import company


def populate(engine):
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        apply_register(session, {"00000001": "Canal Software", "00000002": "Other Firm"}, now, "x")
        sponsor = session.get(Sponsor, "00000001")
        sponsor.city, sponsor.industry = "Amsterdam", "Technology"
        sponsor.latitude, sponsor.longitude = 52.36, 4.9
        apply_jobs(
            session,
            company(),
            [
                Vacancy("1", "Data Engineer", "Amsterdam", "full-time", "https://example.com/1"),
                Vacancy(
                    "2", "Engineering Intern", "Amsterdam", "internship", "https://example.com/2"
                ),
            ],
            now,
        )
    return now


def test_filters_and_response_shape(engine):
    populate(engine)
    with TestClient(create_app(engine)) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        page = client.get(
            "/api/sponsors",
            params={
                "q": "Engineer",
                "city": "Amsterdam",
                "industry": "Technology",
                "job_type": "internship",
                "has_jobs": True,
            },
        ).json()
        assert page["total"] == 1
        sponsor = page["items"][0]
        assert sponsor["id"] == sponsor["kvk"] == "00000001"
        assert sponsor["job_count"] == 1
        assert sponsor["newly_observed"] is False
        assert sponsor["jobs"][0]["job_type"] == "internship"
        assert sponsor["jobs"][0]["sponsorship"] == "not_stated"
        assert client.get("/api/sponsors", params={"q": "Canal"}).json()["total"] == 1
        assert client.get("/api/sponsors", params={"q": "%"}).json()["total"] == 0
        assert client.get("/api/sponsors", params={"industry": "Energy"}).json()["items"] == []
        assert client.get("/api/sponsors", params={"limit": 101}).status_code == 422
        assert client.get("/api/sponsors", params={"job_type": "invalid"}).status_code == 422
        meta = client.get("/api/meta").json()
        assert meta["sponsors"] == 2 and meta["mapped_sponsors"] == 1
        assert meta["fresh_jobs"] == 2 and meta["demo"] is False
        assert meta["cities"] == ["Amsterdam"]
        history = client.get("/api/history").json()
        assert len(history["snapshots"]) == 1
        assert history["events"] == []


def test_removed_sponsors_and_stale_jobs_never_appear_as_open_opportunities(engine):
    now = populate(engine)
    with Session(engine) as session, session.begin():
        apply_jobs(
            session,
            company(),
            [
                Vacancy("1", "Data Engineer", "Amsterdam", "full-time", "https://example.com/1"),
            ],
            now - timedelta(hours=73),
        )
    with TestClient(create_app(engine)) as client:
        assert client.get("/api/sponsors?has_jobs=true").json()["total"] == 0
        assert client.get("/api/meta").json()["fresh_jobs"] == 0
    with Session(engine) as session, session.begin():
        session.get(Sponsor, "00000001").recognized = False
    with TestClient(create_app(engine)) as client:
        result = client.get("/api/sponsors").json()
        assert result["total"] == 1
        assert result["items"][0]["name"] == "Other Firm"


def test_pagination_is_stable_and_has_no_overlap(engine):
    populate(engine)
    with TestClient(create_app(engine)) as client:
        first = client.get("/api/sponsors?limit=1").json()
        second = client.get("/api/sponsors?limit=1&offset=1").json()
        assert first["total"] == second["total"] == 2
        assert first["items"][0]["id"] != second["items"][0]["id"]


def test_newly_observed_flag_excludes_baseline_and_history_explains_removals(engine):
    now = populate(engine)
    with Session(engine) as session, session.begin():
        apply_register(
            session,
            {"00000001": "Canal Software", "00000003": "New arrival"},
            now + timedelta(seconds=1),
            "new",
            max_removal_fraction=0.5,
        )
    with TestClient(create_app(engine)) as client:
        page = client.get("/api/sponsors?q=New").json()
        assert page["items"][0]["newly_observed"] is True
        events = client.get("/api/history").json()["events"]
        assert {event["kind"] for event in events} == {"added", "removed"}
        assert all(event["kind"] != "baseline" for event in events)


def test_company_job_preview_limit_preserves_total_count(engine):
    now = populate(engine)
    with Session(engine) as session, session.begin():
        apply_jobs(
            session,
            company(),
            [
                Vacancy(
                    str(i),
                    f"Engineer {i:02d}",
                    "Amsterdam",
                    "full-time",
                    f"https://example.com/{i}",
                )
                for i in range(25)
            ],
            now,
        )
    with TestClient(create_app(engine)) as client:
        sponsor = client.get("/api/sponsors?has_jobs=true").json()["items"][0]
        assert sponsor["job_count"] == 25
        assert len(sponsor["jobs"]) == 20
