import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from radar.db import PipelineRun, Sponsor
from radar.pipeline import apply_register, refresh
from radar.raw import (
    RawBatch,
    RawRecord,
    RawResponse,
    archive_response,
    export_raw,
    extract_operational_records,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.test_sources import company, register_html


def test_original_response_bytes_are_preserved_even_when_invalid(engine, tmp_path):
    body = b"<html>maintenance \xc3\xa9 \r\n  </html>"
    path = tmp_path / "companies.json"
    path.write_text("[]")
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(ValueError):
            refresh(engine, path, client)
    with Session(engine) as session:
        response = session.scalar(select(RawResponse))
        assert response.body == body
        assert response.body_sha256 == hashlib.sha256(body).hexdigest()
        assert response.source == "ind" and response.source_url.startswith("https://ind.nl/")
        assert session.get(PipelineRun, response.pipeline_run_id).status == "failed"
        assert session.scalar(select(func.count()).select_from(RawBatch)) == 0


def test_raw_operational_extract_is_unchanged_append_only_and_atomic(engine):
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        apply_register(session, {"00000001": "  A Company  "}, now, "hash")
        sponsor = session.get(Sponsor, "00000001")
        sponsor.city = "  Amsterdam  "
    first = export_raw(engine)
    with Session(engine) as session:
        before = session.scalar(
            select(RawRecord).where(
                RawRecord.batch_id == first, RawRecord.source_table == "sponsors"
            )
        ).payload
        assert before["kvk"] == "00000001"
        assert before["name"] == "  A Company  "
        assert before["city"] == "  Amsterdam  "
        assert before["first_seen"] == now.date().isoformat()
        assert before["recognized"] is True
        assert before["latitude"] is None
    with Session(engine) as session, session.begin():
        session.get(Sponsor, "00000001").name = "A renamed company"
    second = export_raw(engine)
    assert first != second
    with Session(engine) as session:
        records = session.scalars(
            select(RawRecord).where(RawRecord.source_table == "sponsors").order_by(RawRecord.id)
        ).all()
        assert len(records) == 2
        assert records[0].payload == before
        assert records[1].payload["name"] == "A renamed company"
        assert session.scalar(select(func.count()).select_from(RawBatch)) == 2


@pytest.mark.parametrize("duplicate_ids", [False, True])
def test_ats_response_retains_foreign_jobs_unknown_fields_and_duplicates_before_parsing(
    engine, tmp_path, duplicate_ids
):
    config = company()
    path = tmp_path / "companies.json"
    path.write_text(json.dumps([config.model_dump(mode="json")]))
    register = register_html([(f"Company {i}", f"{i:08d}") for i in range(1, 1002)])
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "  Engineer  ",
                "absolute_url": "https://example.com/1",
                "location": {"name": "Amsterdam"},
                "unmapped_field": ["preserve", 123],
            },
            {
                "id": 2,
                "title": "US Engineer",
                "absolute_url": "https://example.com/2",
                "location": {"name": "New York"},
            },
        ],
        "meta": {"total": 2},
    }
    body = json.dumps(payload, indent=3).encode()
    if duplicate_ids:
        payload["jobs"][1]["id"] = 1
        body = json.dumps(payload, indent=3).encode()

    def handler(request):
        return httpx.Response(
            200, content=register.encode() if request.url.host == "ind.nl" else body
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert refresh(engine, path, client) == (not duplicate_ids)
    with Session(engine) as session:
        response = session.scalar(select(RawResponse).where(RawResponse.source == "greenhouse"))
        assert response.body == body
        assert json.loads(response.body)["jobs"][0]["unmapped_field"] == ["preserve", 123]
        assert len(json.loads(response.body)["jobs"]) == 2
        landed_jobs = session.scalars(
            select(RawRecord).where(RawRecord.source_table == "jobs")
        ).all()
        assert len(landed_jobs) == (0 if duplicate_ids else 1)
        if not duplicate_ids:
            assert landed_jobs[0].payload["location"] == "Amsterdam"


def test_non_success_status_is_not_discarded_by_archival(engine):
    response = httpx.Response(
        503,
        content=b'{"error":"unavailable"}',
        request=httpx.Request("GET", "https://api.lever.co/v0/postings/example"),
    )
    archive_response(engine, response, 1, "lever")
    with Session(engine) as session:
        landed = session.scalar(select(RawResponse))
        assert landed.status_code == 503
        assert landed.body == response.content


def test_interrupted_extract_rolls_back_the_entire_batch(engine):
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        apply_register(session, {"00000001": "Company"}, now, "hash")
    with pytest.raises(RuntimeError, match="interrupted"):
        with Session(engine) as session, session.begin():
            extract_operational_records(session)
            session.flush()
            raise RuntimeError("interrupted")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RawBatch)) == 0
        assert session.scalar(select(func.count()).select_from(RawRecord)) == 0
        assert session.get(Sponsor, "00000001").name == "Company"
