"""Validate the demo build's grains and layer boundaries after dbt build."""

import json
from pathlib import Path

from radar.db import make_engine
from sqlalchemy import MetaData, Table, func, select


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
        expected = {
            "stg_sponsors": 10,
            "int_sponsors_deduplicated": 5,
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
    engine.dispose()
    print("Verified 16 model layer boundaries, demo grains, deduplication and job metrics.")


if __name__ == "__main__":
    main()
