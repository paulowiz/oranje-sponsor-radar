import hashlib
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

IND_URL = "https://ind.nl/en/public-register-recognised-sponsors/public-register-work"


class CompanyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kvk: str = Field(pattern=r"^\d{8}$")
    city: str = Field(min_length=1)
    industry: str = Field(min_length=1)
    latitude: float = Field(ge=50.7, le=53.6)
    longitude: float = Field(ge=3.2, le=7.3)
    careers_url: HttpUrl
    provider: Literal["greenhouse", "lever"] | None = None
    board: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]+$")
    nl_location_terms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_board(self):
        if bool(self.provider) != bool(self.board):
            raise ValueError("provider and board must be configured together")
        if self.provider and not self.nl_location_terms:
            raise ValueError("ATS mappings require explicit Netherlands location terms")
        if any(not term.strip() for term in self.nl_location_terms):
            raise ValueError("Location terms cannot be blank")
        return self


@dataclass(frozen=True)
class Vacancy:
    external_id: str
    title: str
    location: str
    job_type: str
    url: str


def parse_register(html: str, minimum: int = 1000) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for table in soup.find_all("table"):
        headers = " ".join(th.get_text(" ", strip=True) for th in table.select("thead th"))
        if "KVK" in headers or "Chamber of Commerce" in headers:
            candidates.append(table)
    if len(candidates) != 1:
        raise ValueError("Expected exactly one IND work-register table with a KVK header")
    records: dict[str, set[str]] = {}
    for row in candidates[0].select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells or row.find_parent("thead"):
            continue
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if len(values) != 2 or not values[0]:
            raise ValueError(f"Malformed IND register row: {values!r}")
        name, kvk = values
        if kvk and not re.fullmatch(r"\d{8}", kvk):
            raise ValueError(f"Invalid KVK number: {kvk!r}")
        # A handful of public bodies have no KVK. Name changes appear as removal/addition.
        key = kvk or f"name:{hashlib.sha256(name.casefold().encode()).hexdigest()}"
        records.setdefault(key, set()).add(name)
    if len(records) < minimum:
        raise ValueError(f"Register has only {len(records)} entries; expected at least {minimum}")
    return {key: " / ".join(sorted(names)) for key, names in records.items()}


def normalize_job_type(value: str) -> str:
    normalized = re.sub(r"[\s_-]+", "", value.casefold())
    return {
        "fulltime": "full-time",
        "parttime": "part-time",
        "intern": "internship",
        "internship": "internship",
        "contract": "contract",
        "temporary": "contract",
    }.get(normalized, "unknown")


def matches_location(location: str, terms: list[str]) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(term.strip())}(?!\w)", location, re.IGNORECASE)
        for term in terms
    )


def parse_jobs(payload: object, config: CompanyConfig) -> list[Vacancy]:
    if config.provider == "greenhouse":
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("Malformed Greenhouse response")
        rows = payload["jobs"]
        # Greenhouse is unpaginated; count mismatch must not close existing jobs.
        total = payload.get("meta", {}).get("total")
        if total is not None and total != len(rows):
            raise ValueError("Incomplete Greenhouse response")
    else:
        if not isinstance(payload, list):
            raise ValueError("Malformed Lever response")
        rows = payload
    vacancies = []
    ids: set[str] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), (str, int))
            or isinstance(row.get("id"), bool)
            or not str(row["id"]).strip()
        ):
            raise ValueError("Malformed job record")
        if config.provider == "greenhouse":
            title, url = row.get("title"), row.get("absolute_url")
            location = row.get("location", {}).get("name")
            commitment = next(
                (
                    str(item.get("value") or "")
                    for item in (row.get("metadata") or [])
                    if str(item.get("name", "")).casefold() in ("employment type", "job type")
                ),
                "",
            )
        else:
            categories = row.get("categories", {})
            title, url = row.get("text"), row.get("hostedUrl")
            location = categories.get("location")
            commitment = categories.get("commitment") or ""
        if not all(isinstance(value, str) and value.strip() for value in (title, url, location)):
            raise ValueError("Job is missing title, URL, or location")
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("Job URL must be absolute HTTPS")
        external_id = str(row["id"])
        if external_id in ids:
            raise ValueError(f"Duplicate job ID: {external_id}")
        ids.add(external_id)
        if matches_location(location, config.nl_location_terms):
            vacancies.append(
                Vacancy(external_id, title, location, normalize_job_type(commitment), url)
            )
    return vacancies


def fetch_jobs(client: httpx.Client, config: CompanyConfig) -> list[Vacancy]:
    response = fetch_job_response(client, config)
    response.raise_for_status()
    return parse_jobs(response.json(), config)


def fetch_job_response(client: httpx.Client, config: CompanyConfig) -> httpx.Response:
    if config.provider == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{config.board}/jobs"
    else:
        url = f"https://api.lever.co/v0/postings/{config.board}?mode=json"
    return client.get(url)
