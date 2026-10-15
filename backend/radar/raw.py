import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    LargeBinary,
    MetaData,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateSchema

from radar.db import Base, Dataset, Job, Snapshot, Sponsor, SponsorEvent, check_dataset

REFRESH_LOCK_ID = 61824791


class RawBase(DeclarativeBase):
    metadata = MetaData(schema="raw")


class RawResponse(RawBase):
    __tablename__ = "source_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pipeline_run_id: Mapped[int]
    source: Mapped[str]
    source_url: Mapped[str]
    status_code: Mapped[int]
    content_type: Mapped[str | None]
    body: Mapped[bytes] = mapped_column(LargeBinary)
    body_sha256: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RawBatch(RawBase):
    __tablename__ = "extract_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pipeline_run_id: Mapped[int | None]
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str]


class RawRecord(RawBase):
    __tablename__ = "entity_records"
    __table_args__ = (UniqueConstraint("batch_id", "source_table", "record_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("raw.extract_batches.id"), index=True)
    source_table: Mapped[str] = mapped_column(index=True)
    record_key: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON)


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(CreateSchema("raw", if_not_exists=True))
    RawBase.metadata.create_all(engine)


def archive_response(engine: Engine, response: httpx.Response, run_id: int, source: str) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            RawResponse(
                id=str(uuid4()),
                pipeline_run_id=run_id,
                source=source,
                source_url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                body=response.content,
                body_sha256=hashlib.sha256(response.content).hexdigest(),
                observed_at=datetime.now(UTC),
            )
        )


def extract_operational_records(session: Session, run_id: int | None = None) -> str:
    """Land unchanged source values; typing/cleansing belongs to dbt staging."""
    dataset = session.get(Dataset, 1)
    if dataset is None:
        raise ValueError("Initialize the dataset before extracting raw records")
    batch_id = str(uuid4())
    session.add(
        RawBatch(
            id=batch_id,
            pipeline_run_id=run_id,
            extracted_at=datetime.now(UTC),
            mode=dataset.mode,
        )
    )
    session.flush()
    serializer = TypeAdapter(dict)
    for model in (Sponsor, Job, SponsorEvent, Snapshot):
        key = model.__mapper__.primary_key[0].key
        records = []
        for row in session.scalars(select(model)):
            values = {column.key: getattr(row, column.key) for column in model.__table__.columns}
            records.append(
                RawRecord(
                    batch_id=batch_id,
                    source_table=model.__tablename__,
                    record_key=str(values[key]),
                    payload=serializer.dump_python(values, mode="json"),
                )
            )
        session.add_all(records)
    return batch_id


def export_raw(engine: Engine) -> str:
    check_dataset(engine)
    with Session(engine) as session, session.begin():
        if engine.dialect.name == "postgresql":
            acquired = session.scalar(text(f"SELECT pg_try_advisory_xact_lock({REFRESH_LOCK_ID})"))
            if not acquired:
                raise RuntimeError("Another refresh or raw export is already running")
        return extract_operational_records(session)
