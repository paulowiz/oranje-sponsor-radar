import pytest
from pydantic import ValidationError
from radar.sources import CompanyConfig, matches_location, parse_jobs, parse_register


def register_html(rows):
    return (
        "<table><thead><tr><th>Organisation</th><th>KVK number</th></tr></thead><tbody>"
        + "".join(f"<tr><th scope='row'>{name}</th><td>{kvk}</td></tr>" for name, kvk in rows)
        + "</tbody></table>"
    )


def company(provider="greenhouse", **overrides):
    return CompanyConfig(
        **{
            "kvk": "00000001",
            "city": "Amsterdam",
            "industry": "Technology",
            "latitude": 52.36,
            "longitude": 4.9,
            "careers_url": "https://example.com/careers",
            "provider": provider,
            "board": "example",
            "nl_location_terms": ["Amsterdam", "Netherlands"],
            **overrides,
        }
    )


def test_register_keeps_legal_identifiers_aliases_and_missing_kvk():
    html = register_html(
        [
            ("Alpha", "00123456"),
            ("Alpha Alias", "00123456"),
            ("Public body", ""),
        ]
    )
    records = parse_register(html, minimum=2)
    assert records["00123456"] == "Alpha / Alpha Alias"
    assert len(records) == 2
    assert next(k for k in records if k.startswith("name:"))
    assert records == parse_register(html, minimum=2)


@pytest.mark.parametrize(
    "html",
    [
        "<html>maintenance</html>",
        register_html([("Company", "not-a-kvk")]),
        register_html([("", "12345678")]),
        register_html([("Company", "12345678")]),
    ],
)
def test_bad_or_truncated_register_fails_closed(html):
    with pytest.raises(ValueError):
        parse_register(html)


def test_greenhouse_jobs_are_netherlands_only_and_types_not_guessed():
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "absolute_url": "https://example.com/1",
                "location": {"name": "Amsterdam"},
                "metadata": [],
            },
            {
                "id": 2,
                "title": "Intern",
                "absolute_url": "https://example.com/2",
                "location": {"name": "Netherlands - Remote"},
                "metadata": [{"name": "Employment Type", "value": "Internship"}],
            },
            {
                "id": 3,
                "title": "US job",
                "absolute_url": "https://example.com/3",
                "location": {"name": "New York"},
            },
        ],
        "meta": {"total": 3},
    }
    jobs = parse_jobs(payload, company())
    assert [job.external_id for job in jobs] == ["1", "2"]
    assert [job.job_type for job in jobs] == ["unknown", "internship"]


def test_lever_jobs():
    jobs = parse_jobs(
        [
            {
                "id": "abc",
                "text": "Engineer",
                "hostedUrl": "https://jobs.lever.co/example/abc",
                "categories": {"location": "Amsterdam", "commitment": "Full-time"},
            },
        ],
        company("lever"),
    )
    assert jobs[0].job_type == "full-time"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"jobs": None},
        {"jobs": [{"id": "1"}]},
        {"jobs": [], "meta": {"total": 3}},
        {
            "jobs": [
                {
                    "id": "1",
                    "title": "X",
                    "location": {"name": "Amsterdam"},
                    "absolute_url": "javascript:alert(1)",
                }
            ]
        },
    ],
)
def test_malformed_board_response_cannot_be_interpreted_as_empty(payload):
    with pytest.raises(ValueError):
        parse_jobs(payload, company())


def test_mapping_requires_verified_explicit_location_terms():
    with pytest.raises(ValidationError):
        company(nl_location_terms=[])
    assert not matches_location("Amsterdamville", ["Amsterdam"])
    assert not matches_location("Remote", ["Netherlands"])
    assert matches_location("Amsterdam, NL", ["Amsterdam"])
