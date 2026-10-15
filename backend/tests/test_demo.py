import pytest
from fastapi.testclient import TestClient
from radar.api import create_app
from radar.db import Base
from radar.demo import seed_demo
from radar.pipeline import refresh
from radar.raw import RawBase
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def test_demo_is_explicit_and_cannot_be_relabelled_or_refreshed(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_MODE", "true")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"raw": None}},
    )
    Base.metadata.create_all(engine)
    RawBase.metadata.create_all(engine)
    try:
        seed_demo(engine)
        with TestClient(create_app(engine)) as client:
            assert client.get("/api/meta").json()["demo"] is True
            assert client.get("/api/sponsors").json()["total"] == 5
        with pytest.raises(ValueError, match="empty database"):
            seed_demo(engine)
        with pytest.raises(ValueError, match="disabled in demo"):
            refresh(engine, tmp_path / "unused.json")
        monkeypatch.setenv("DEMO_MODE", "false")
        with pytest.raises(ValueError, match="cannot be mixed"):
            with TestClient(create_app(engine)):
                pass
    finally:
        engine.dispose()
