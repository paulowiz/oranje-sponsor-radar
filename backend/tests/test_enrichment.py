import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from radar.enrichment import NeedsReview, geocode, legal_address, sync_enrichment
from radar.pipeline import apply_register, load_companies
from radar.raw import RawEnrichmentResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def gleif():
    return {
        "meta": {"pagination": {"total": 1}},
        "data": [
            {
                "attributes": {
                    "lei": "TEST-LEI",
                    "entity": {
                        "registeredAs": "00000001",
                        "registeredAt": {"id": "RA000463"},
                        "legalName": {"name": "Test Company B.V."},
                        "status": "ACTIVE",
                        "jurisdiction": "NL",
                        "legalAddress": {
                            "country": "NL",
                            "postalCode": "1011 DJ",
                            "city": "Amsterdam",
                            "addressLines": ["Simon Carmiggeltstraat 6"],
                        },
                    },
                    "registration": {
                        "status": "ISSUED",
                        "lastUpdateDate": "2026-01-01T00:00:00Z",
                        "nextRenewalDate": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                    },
                }
            }
        ],
    }


def pdok():
    return {
        "response": {
            "numFound": 1,
            "docs": [
                {
                    "type": "adres",
                    "bron": "BAG",
                    "postcode": "1011DJ",
                    "huisnummer": 6,
                    "huis_nlt": "6",
                    "straatnaam": "Simon Carmiggeltstraat",
                    "woonplaatsnaam": "Amsterdam",
                    "centroide_ll": "POINT(4.90595647 52.37650726)",
                }
            ],
        }
    }


def test_exact_legal_entity_and_address_matching():
    address = legal_address(gleif(), "00000001", "Test Company B.V.")
    result = geocode(pdok(), address, "00000001")
    assert result.city == "Amsterdam" and result.latitude == 52.37650726
    assert result.longitude == 4.90595647 and result.kvk == "00000001"
    assert result.industry is None and result.careers_url is None


@pytest.mark.parametrize(
    "change",
    [
        "wrong_kvk",
        "wrong_authority",
        "different_legal_name",
        "foreign_address",
        "expired",
        "duplicate",
        "partial",
        "unit",
    ],
)
def test_uncertain_gleif_records_never_auto_match(change):
    payload = gleif()
    attrs = payload["data"][0]["attributes"]
    entity = attrs["entity"]
    if change == "wrong_kvk":
        entity["registeredAs"] = "00000002"
    elif change == "wrong_authority":
        entity["registeredAt"]["id"] = "OTHER"
    elif change == "different_legal_name":
        entity["legalName"]["name"] = "Test Company Holding B.V."
    elif change == "foreign_address":
        entity["legalAddress"]["country"] = "US"
    elif change == "expired":
        attrs["registration"]["nextRenewalDate"] = "2000-01-01T00:00:00Z"
    elif change == "duplicate":
        payload["data"].append(payload["data"][0])
        payload["meta"]["pagination"]["total"] = 2
    elif change == "partial":
        payload["meta"]["pagination"]["total"] = 11
    else:
        entity["legalAddress"]["addressLines"] = ["Simon Carmiggeltstraat 6 A"]
    with pytest.raises(NeedsReview):
        legal_address(payload, "00000001", "Test Company B.V.")


@pytest.mark.parametrize("change", ["ambiguous", "postcode", "street", "city", "unit", "point"])
def test_pdok_geocode_requires_full_address_corroboration(change):
    payload = pdok()
    doc = payload["response"]["docs"][0]
    if change == "ambiguous":
        payload["response"]["numFound"] = 2
    elif change == "postcode":
        doc["postcode"] = "9999ZZ"
    elif change == "street":
        doc["straatnaam"] = "Different Street"
    elif change == "city":
        doc["woonplaatsnaam"] = "Almere"
    elif change == "unit":
        doc["huis_nlt"] = "6 A"
    else:
        doc["centroide_ll"] = "POINT(40 60)"
    with pytest.raises(ValueError):
        geocode(payload, legal_address(gleif(), "00000001", "Test Company B.V."), "00000001")


def test_sync_preserves_raw_provenance_and_existing_curated_config(engine, tmp_path):
    with Session(engine) as session, session.begin():
        apply_register(session, {"00000001": "Test Company B.V."}, datetime.now(UTC), "x")
    path, report_path = tmp_path / "companies.json", tmp_path / "report.json"
    path.write_text("[]")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        payload = gleif() if request.url.host == "api.gleif.org" else pdok()
        if request.url.host == "query.wikidata.org":
            payload = {"results": {"bindings": [{"website": {"value": "https://example.com"}}]}}
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        preview = sync_enrichment(engine, ["00000001"], path, report_path, client=client)
        assert not preview["applied"] and json.loads(path.read_text()) == []
        report = sync_enrichment(engine, ["00000001"], path, report_path, True, client)
        assert report["applied"] and not report["errors"]
        assert report["configuration_additions"] == ["00000001"]
        assert len(calls) == 3
        assert all(row["cached"] for row in report["companies"][0]["sources"])
        configs = load_companies(path)
        assert configs[0].city == "Amsterdam" and configs[0].careers_url is None
        curated = json.loads(path.read_text())
        curated[0]["industry"] = "Curated industry"
        curated[0]["city"] = "Curated city"
        path.write_text(json.dumps(curated))
        sync_enrichment(engine, ["00000001"], path, report_path, True, client)
        assert json.loads(path.read_text()) == curated
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RawEnrichmentResponse)) == 3
        for row in session.scalars(select(RawEnrichmentResponse)):
            assert hashlib.sha256(row.body).hexdigest() == row.body_sha256


def test_failed_source_is_reported_and_does_not_add_guesses(engine, tmp_path):
    with Session(engine) as session, session.begin():
        apply_register(session, {"00000001": "Test Company B.V."}, datetime.now(UTC), "x")
    path, report_path = tmp_path / "companies.json", tmp_path / "report.json"
    path.write_text("[]")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, text="temporarily unavailable")
        )
    ) as client:
        report = sync_enrichment(engine, ["00000001"], path, report_path, True, client)
    assert report["errors"] and report["configuration_additions"] == []
    assert json.loads(path.read_text()) == []
    assert json.loads(report_path.read_text())["errors"]


def test_demo_unknown_and_unbounded_requests_are_rejected(engine, tmp_path, monkeypatch):
    path, report_path = tmp_path / "companies.json", tmp_path / "report.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="currently be listed"):
        sync_enrichment(engine, ["00000001"], path, report_path)
    with pytest.raises(ValueError, match="between 1 and 20"):
        sync_enrichment(engine, [], path, report_path)
    monkeypatch.setenv("DEMO_MODE", "true")
    with pytest.raises(ValueError, match="not demo"):
        sync_enrichment(engine, ["00000001"], path, report_path)


def test_concurrent_configuration_edit_is_not_overwritten(engine, tmp_path):
    with Session(engine) as session, session.begin():
        apply_register(session, {"00000001": "Test Company B.V."}, datetime.now(UTC), "x")
    path, report_path = tmp_path / "companies.json", tmp_path / "report.json"
    path.write_text("[]")
    edited = "[ ]\n"

    def handler(request):
        path.write_text(edited)
        if request.url.host == "api.gleif.org":
            return httpx.Response(200, json=gleif())
        if request.url.host == "query.wikidata.org":
            return httpx.Response(200, json={"results": {"bindings": []}})
        return httpx.Response(200, json=pdok())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="changed during sync"):
            sync_enrichment(engine, ["00000001"], path, report_path, True, client)
    assert path.read_text() == edited
    assert not json.loads(report_path.read_text())["applied"]
