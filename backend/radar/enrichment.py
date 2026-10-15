import hashlib
import json
import logging
import os
import re
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.db import Sponsor, check_dataset, demo_mode
from radar.pipeline import load_companies
from radar.raw import RawEnrichmentResponse
from radar.sources import CompanyConfig

logger = logging.getLogger(__name__)
GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
WIKIDATA_URL = "https://query.wikidata.org/sparql"


class NeedsReview(ValueError):
    pass


def normalized(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


class SourceClient:
    def __init__(self, engine, client: httpx.Client, sync_id: str):
        self.engine, self.client, self.sync_id = engine, client, sync_id
        self.provenance: list[dict] = []

    def get(self, source: str, url: str, params: dict) -> dict:
        request = self.client.build_request("GET", url, params=params)
        source_url = str(request.url)
        cutoff = datetime.now(UTC) - timedelta(days=30)
        with Session(self.engine) as session:
            cached = session.scalar(
                select(RawEnrichmentResponse)
                .where(
                    RawEnrichmentResponse.source_url == source_url,
                    RawEnrichmentResponse.status_code == 200,
                    RawEnrichmentResponse.fetched_at >= cutoff,
                )
                .order_by(RawEnrichmentResponse.fetched_at.desc())
                .limit(1)
            )
            if cached:
                self.provenance.append(
                    {
                        "source": source,
                        "url": source_url,
                        "raw_response_id": cached.id,
                        "sha256": cached.body_sha256,
                        "fetched_at": cached.fetched_at.isoformat(),
                        "cached": True,
                    }
                )
                return json.loads(cached.body)
        response = self.client.send(request)
        now, response_id = datetime.now(UTC), str(uuid4())
        digest = hashlib.sha256(response.content).hexdigest()
        with Session(self.engine) as session, session.begin():
            session.add(
                RawEnrichmentResponse(
                    id=response_id,
                    sync_id=self.sync_id,
                    source=source,
                    source_url=source_url,
                    status_code=response.status_code,
                    body=response.content,
                    body_sha256=digest,
                    fetched_at=now,
                )
            )
        self.provenance.append(
            {
                "source": source,
                "url": source_url,
                "raw_response_id": response_id,
                "sha256": digest,
                "fetched_at": now.isoformat(),
                "cached": False,
            }
        )
        response.raise_for_status()
        time.sleep(0.25)
        return response.json()


def legal_address(payload: dict, kvk: str, sponsor_name: str) -> dict:
    rows = payload["data"]
    total = payload["meta"]["pagination"]["total"]
    if total != len(rows):
        raise NeedsReview("GLEIF result is paginated or incomplete")
    matching = [
        row
        for row in rows
        if row["attributes"]["entity"]["registeredAs"] == kvk
        and row["attributes"]["entity"]["registeredAt"]["id"] == "RA000463"
    ]
    if len(matching) != 1:
        raise NeedsReview(f"Expected one Dutch KVK legal-entity match; found {len(matching)}")
    attributes = matching[0]["attributes"]
    entity, registration = attributes["entity"], attributes["registration"]
    if entity["status"] != "ACTIVE" or registration["status"] != "ISSUED":
        raise NeedsReview("Entity or LEI registration is not current")
    renewal = datetime.fromisoformat(registration["nextRenewalDate"].replace("Z", "+00:00"))
    if renewal <= datetime.now(UTC):
        raise NeedsReview("LEI registration renewal date has passed")
    if normalized(entity["legalName"]["name"]) not in {
        normalized(alias) for alias in sponsor_name.split(" / ")
    }:
        raise NeedsReview("Legal name differs from the IND record")
    address = entity["legalAddress"]
    if entity["jurisdiction"] != "NL" or address["country"] != "NL":
        raise NeedsReview("Legal entity/address is not in the Netherlands")
    postcode = re.sub(r"\s+", "", address["postalCode"]).upper()
    lines = address["addressLines"]
    if len(lines) != 1 or not re.fullmatch(r"\d{4}[A-Z]{2}", postcode):
        raise NeedsReview("Address needs manual review")
    line = lines[0].strip()
    if address.get("addressNumber"):
        line += f" {address['addressNumber']}"
    house = re.fullmatch(r"(.+?)\s+([1-9]\d*)", line)
    if not house or address.get("addressNumberWithinBuilding"):
        raise NeedsReview("House number or unit cannot be matched unambiguously")
    return {
        "lei": attributes["lei"],
        "legal_name": entity["legalName"]["name"],
        "street": house[1],
        "house_number": int(house[2]),
        "postcode": postcode,
        "city": address["city"],
        "registration_updated_at": registration["lastUpdateDate"],
    }


def geocode(payload: dict, address: dict, kvk: str) -> CompanyConfig:
    response = payload["response"]
    docs = response["docs"]
    if response["numFound"] != 1 or len(docs) != 1:
        raise NeedsReview("PDOK address match is missing or ambiguous")
    doc = docs[0]
    if (
        doc["type"] != "adres"
        or doc["bron"] != "BAG"
        or doc["postcode"] != address["postcode"]
        or doc["huisnummer"] != address["house_number"]
        or str(doc["huis_nlt"]) != str(address["house_number"])
        or normalized(doc["straatnaam"]) != normalized(address["street"])
        or normalized(doc["woonplaatsnaam"]) != normalized(address["city"])
    ):
        raise NeedsReview("PDOK address does not exactly corroborate the legal address")
    point = re.fullmatch(r"POINT\((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)", doc["centroide_ll"])
    if not point:
        raise NeedsReview("PDOK returned an invalid WGS84 point")
    return CompanyConfig(
        kvk=kvk, city=doc["woonplaatsnaam"], latitude=float(point[2]), longitude=float(point[1])
    )


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(payload, output, indent=2, ensure_ascii=True)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sync_enrichment(
    engine,
    kvks: list[str],
    config_path: Path,
    report_path: Path,
    apply: bool = False,
    client: httpx.Client | None = None,
) -> dict:
    if demo_mode():
        raise ValueError("Enrichment requires a live database, not demo data")
    check_dataset(engine)
    if not 1 <= len(kvks) <= 20 or any(not re.fullmatch(r"\d{8}", kvk) for kvk in kvks):
        raise ValueError("Supply between 1 and 20 explicit eight-digit KVK numbers")
    if len(set(kvks)) != len(kvks):
        raise ValueError("Duplicate KVK arguments")
    if config_path.resolve() == report_path.resolve():
        raise ValueError("Report path must differ from company configuration")
    original = config_path.read_bytes()
    existing = load_companies(config_path)
    with Session(engine) as session:
        sponsors = {
            row.kvk: row.name
            for row in session.scalars(
                select(Sponsor).where(Sponsor.kvk.in_(kvks), Sponsor.recognized.is_(True))
            )
        }
    if set(sponsors) != set(kvks):
        raise ValueError(
            "Every requested KVK must currently be listed in the live sponsor database"
        )
    report = {
        "sync_id": str(uuid4()),
        "observed_at": datetime.now(UTC).isoformat(),
        "applied": False,
        "companies": [],
        "errors": [],
    }
    own_client = client is None
    client = client or httpx.Client(
        timeout=45,
        follow_redirects=False,
        headers={
            "User-Agent": "OranjeSponsorRadar/0.1 (company enrichment)",
            "Accept": "application/json",
        },
    )
    source = SourceClient(engine, client, report["sync_id"])
    proposed = []
    try:
        for kvk in kvks:
            entry = {"kvk": kvk, "ind_name": sponsors[kvk], "status": "needs_review"}
            provenance_start = len(source.provenance)
            try:
                payload = source.get(
                    "gleif",
                    GLEIF_URL,
                    {
                        "filter[entity.registeredAs]": kvk,
                        "page[size]": 10,
                    },
                )
                address = legal_address(payload, kvk, sponsors[kvk])
                entry["legal_address"] = address
                payload = source.get(
                    "pdok",
                    PDOK_URL,
                    {
                        "q": f"{address['postcode']} {address['house_number']}",
                        "fq": [
                            "type:adres",
                            f"postcode:{address['postcode']}",
                            f"huisnummer:{address['house_number']}",
                        ],
                        "rows": 2,
                    },
                )
                config = geocode(payload, address, kvk)
                entry["location"] = config.model_dump(mode="json", exclude_none=True)
                entry["status"] = "corroborated_location"
                proposed.append(config)
            except NeedsReview as exc:
                entry["reason"] = str(exc)
                logger.warning("%s needs review: %s", kvk, exc)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                message = f"{kvk} location enrichment: {exc}"
                entry["reason"] = message
                report["errors"].append(message)
                logger.exception(message)
            try:
                query = (
                    "SELECT ?item ?website ?industry ?industryLabel WHERE { "
                    f'?item wdt:P3220 "{kvk}". '
                    "OPTIONAL { ?item wdt:P856 ?website } "
                    "OPTIONAL { ?item wdt:P452 ?industry } "
                    'SERVICE wikibase:label { bd:serviceParam wikibase:language "en,nl" } }'
                )
                payload = source.get("wikidata", WIKIDATA_URL, {"query": query, "format": "json"})
                entry["review_only_wikidata"] = payload["results"]["bindings"]
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                message = f"{kvk} Wikidata suggestions unavailable: {exc}"
                report["errors"].append(message)
                logger.exception(message)
            entry["sources"] = source.provenance[provenance_start:]
            report["companies"].append(entry)
        existing_keys = {company.kvk for company in existing}
        additions = [company for company in proposed if company.kvk not in existing_keys]
        report["configuration_additions"] = [company.kvk for company in additions]
        report["preserved_existing"] = [kvk for kvk in kvks if kvk in existing_keys]
        if apply:
            if config_path.read_bytes() != original:
                raise RuntimeError(
                    "Company configuration changed during sync; refusing to overwrite"
                )
            # Preserve existing JSON entries verbatim, including all manually curated fields.
            merged = json.loads(original) + [
                config.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                for config in additions
            ]
            atomic_json(config_path, merged)
            report["applied"] = True
    finally:
        if own_client:
            client.close()
        atomic_json(report_path, report)
    return report
