import os
from uuid import uuid4

import pytest
from radar.db import Base, Dataset
from radar.raw import RawBase
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema, DropSchema


@pytest.fixture(params=["sqlite"] + (["postgres"] if os.getenv("TEST_DATABASE_URL") else []))
def engine(monkeypatch, request):
    monkeypatch.setenv("DEMO_MODE", "false")
    schema = None
    if request.param == "postgres":
        base_engine = create_engine(os.environ["TEST_DATABASE_URL"])
        schema = f"radar_test_{uuid4().hex}"
        with base_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = base_engine.execution_options(schema_translate_map={None: schema, "raw": schema})
    else:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"raw": None}},
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    RawBase.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        session.add(Dataset(id=1, mode="live"))
    yield engine
    if schema:
        RawBase.metadata.drop_all(engine)
        Base.metadata.drop_all(engine)
        with base_engine.begin() as connection:
            connection.execute(DropSchema(schema))
    engine.dispose()
