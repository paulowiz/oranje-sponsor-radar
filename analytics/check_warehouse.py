"""Validate the demo build's grains and layer boundaries after dbt build."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from radar.db import Dataset, Job, Sponsor, make_engine
from radar.raw import RawRecord, extract_operational_records
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.orm import Session


def main():
    manifest = json.loads(
        (Path(__file__).parent / "target" / "manifest.json").read_text(encoding="utf-8")
    )
    models = {
        node["name"]: node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model"
    }
    assert len(models) == 16, f"Unexpected model count: {len(models)}"
    for model in models.values():
        layer = model["fqn"][1]
        assert layer in {"staging", "intermediate", "marts"}
        assert model["config"]["materialized"] == ("table" if layer == "marts" else "view")
        for dependency in model["depends_on"]["nodes"]:
            if dependency.startswith("source."):
                assert layer == "staging", f"{model['name']} bypasses staging"
                assert manifest["sources"][dependency]["source_name"] == "raw"
            else:
                parent = manifest["nodes"][dependency]
                allowed = {"staging"} if layer == "intermediate" else {"intermediate"}
                if layer == "intermediate":
                    allowed.add("intermediate")
                assert parent["fqn"][1] in allowed, f"Invalid dependency for {model['name']}"

    engine = make_engine()
    metadata = MetaData()

    def relation(name):
        model = models[name]
        return Table(model["alias"], metadata, schema=model["schema"], autoload_with=engine)

    with engine.connect() as connection:
        dataset = Table("dataset", metadata, schema="public", autoload_with=engine)
        assert connection.scalar(select(dataset.c.mode)) == "demo", "Demo fixtures required"
        expected = {
            "stg_sponsors": 10,
            "itm_sponsors_deduplicated": 5,
            "dim_sponsor": 5,
            "stg_jobs": 10,
            "fct_job_openings": 5,
            "fct_sponsor_events": 5,
            "fct_daily_sponsor_metrics": 1,
            "fct_sponsor_job_metrics": 5,
        }
        for name, count in expected.items():
            actual = connection.scalar(select(func.count()).select_from(relation(name)))
            assert actual == count, f"{name}: expected {count}, got {actual}"
        sponsors = relation("dim_sponsor")
        assert (
            connection.scalar(select(sponsors.c.kvk).order_by(sponsors.c.kvk).limit(1))
            == "00000001"
        )
        jobs = relation("fct_job_openings")
        assert (
            connection.scalar(
                select(func.count()).select_from(jobs).where(jobs.c.is_discoverable_at_build)
            )
            == 5
        )
        metrics = relation("fct_sponsor_job_metrics")
        assert connection.scalar(select(func.sum(metrics.c.fresh_open_role_count))) == 5
    with Session(engine) as session:
        assert session.get(Dataset, 1).mode == "demo"
        sponsor = session.get(Sponsor, "00000001")
        sponsor.name, sponsor.city = "  Updated sponsor  ", "  Amsterdam  "
        sponsor.recognized = False
        stale_job = session.get(Job, "demo:1")
        stale_job.first_seen = datetime.now(UTC) - timedelta(days=7)
        stale_job.last_seen = datetime.now(UTC) - timedelta(hours=73)
        session.get(Job, "demo:2").active = False
        session.flush()
        batch_id = extract_operational_records(session)
        session.flush()
        raw_row = session.scalar(
            select(RawRecord).where(
                RawRecord.batch_id == batch_id,
                RawRecord.source_table == "sponsors",
                RawRecord.record_key == "00000001",
            )
        )
        assert raw_row.payload["name"] == "  Updated sponsor  "
        staging = relation("stg_sponsors")
        staged = (
            session.execute(
                select(staging).where(
                    staging.c.batch_id == batch_id, staging.c.sponsor_id == "00000001"
                )
            )
            .mappings()
            .one()
        )
        assert staged["sponsor_name"] == "Updated sponsor"
        assert staged["city"] == "Amsterdam" and staged["kvk"] == "00000001"
        assert staged["is_recognized"] is False
        intermediate = relation("itm_sponsors_deduplicated")
        assert (
            session.scalar(
                select(intermediate.c.sponsor_name).where(intermediate.c.sponsor_id == "00000001")
            )
            == "Updated sponsor"
        )
        eligible = relation("itm_jobs_eligible")
        assert (
            session.scalar(
                select(func.count()).select_from(eligible).where(eligible.c.is_discoverable)
            )
            == 2
        ), "Removed sponsors, stale vacancies and closed jobs must be excluded"
        # Synthetic changes exercise the views only; leave fixture data and raw history untouched.
        session.rollback()
    engine.dispose()
    print(
        "Verified 16 model layer boundaries, demo grains, raw preservation, staging cleansing, "
        "latest-version deduplication and business eligibility."
    )


if __name__ == "__main__":
    main()
