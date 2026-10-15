import os
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Sponsor(Base):
    __tablename__ = "sponsors"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kvk: Mapped[str | None] = mapped_column(String(8), unique=True)
    name: Mapped[str]
    recognized: Mapped[bool] = mapped_column(Boolean, index=True)
    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)
    removed_on: Mapped[date | None] = mapped_column(Date)
    city: Mapped[str | None]
    industry: Mapped[str | None]
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    careers_url: Mapped[str | None]


class SponsorEvent(Base):
    __tablename__ = "sponsor_events"
    __table_args__ = (UniqueConstraint("sponsor_id", "observed_at", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sponsor_id: Mapped[str] = mapped_column(ForeignKey("sponsors.id"), index=True)
    observed_on: Mapped[date] = mapped_column(Date, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(primary_key=True)
    sponsor_id: Mapped[str] = mapped_column(ForeignKey("sponsors.id"), index=True)
    provider: Mapped[str]
    board: Mapped[str]
    title: Mapped[str]
    location: Mapped[str]
    job_type: Mapped[str]
    url: Mapped[str]
    active: Mapped[bool] = mapped_column(Boolean, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Recognition is not evidence of sponsorship for an individual vacancy.
    sponsorship: Mapped[str] = mapped_column(default="not_stated")


class Snapshot(Base):
    __tablename__ = "daily_snapshots"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    sponsor_count: Mapped[int]
    added_count: Mapped[int]
    removed_count: Mapped[int]
    restored_count: Mapped[int]
    active_job_count: Mapped[int]
    source_hash: Mapped[str]
    source_url: Mapped[str]
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str]
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    boards_succeeded: Mapped[int] = mapped_column(Integer, default=0)


class Dataset(Base):
    __tablename__ = "dataset"

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str]


def make_engine(url: str | None = None):
    connection = (
        url
        or os.getenv("DATABASE_URL")
        or URL.create(
            "postgresql+psycopg",
            username=os.getenv("POSTGRES_USER", "radar"),
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "radar"),
        )
    )
    return create_engine(
        connection,
        pool_pre_ping=True,
    )


def demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "false").lower() == "true"


def check_dataset(engine) -> None:
    expected = "demo" if demo_mode() else "live"
    with Session(engine) as session, session.begin():
        dataset = session.get(Dataset, 1)
        if dataset is None:
            if session.scalar(select(Sponsor.id).limit(1)):
                raise ValueError("Database has records but no dataset mode; manual review required")
            session.add(Dataset(id=1, mode=expected))
        elif dataset.mode != expected:
            raise ValueError(
                f"Database is {dataset.mode}, but configured mode is {expected}. "
                "Use a separate database; demo and live records cannot be mixed."
            )
