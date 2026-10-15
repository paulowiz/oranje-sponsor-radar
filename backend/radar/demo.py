from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.db import Job, Snapshot, Sponsor, check_dataset, demo_mode
from radar.pipeline import apply_register
from radar.raw import extract_operational_records


def seed_demo(engine):
    if not demo_mode():
        raise ValueError("Set DEMO_MODE=true and use a dedicated demo database before seeding")
    check_dataset(engine)
    now = datetime.now(UTC)
    companies = [
        ("00000001", "Demo Canal Software", "Amsterdam", "Technology", 52.3676, 4.9041),
        ("00000002", "Demo Delta Energy", "Rotterdam", "Energy", 51.9244, 4.4777),
        ("00000003", "Demo Photon Labs", "Eindhoven", "Engineering", 51.4416, 5.4697),
        ("00000004", "Demo North Biotech", "Groningen", "Life sciences", 53.2194, 6.5665),
        ("00000005", "Demo Harbor Finance", "Utrecht", "Finance", 52.0907, 5.1214),
    ]
    with Session(engine) as session, session.begin():
        if session.scalar(select(Sponsor.id).limit(1)):
            raise ValueError("Demo seeding requires an empty database")
        apply_register(session, {row[0]: row[1] for row in companies}, now, "demo")
        for index, (kvk, _, city, industry, lat, lon) in enumerate(companies):
            sponsor = session.get(Sponsor, kvk)
            sponsor.city, sponsor.industry = city, industry
            sponsor.latitude, sponsor.longitude = lat, lon
            session.add(
                Job(
                    id=f"demo:{index}",
                    sponsor_id=kvk,
                    provider="demo",
                    board="demo",
                    title=[
                        "Frontend Engineer",
                        "Data Analyst",
                        "Engineering Intern",
                        "Research Associate",
                        "Graduate Data Engineer",
                    ][index],
                    location=city,
                    job_type="internship" if index == 2 else "full-time",
                    url="https://example.com",
                    active=True,
                    first_seen=now,
                    last_seen=now,
                    sponsorship="not_stated",
                )
            )
        session.flush()
        session.get(Snapshot, now.date()).active_job_count = len(companies)
        session.flush()
        extract_operational_records(session)
