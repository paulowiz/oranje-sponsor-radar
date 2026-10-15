# Company enrichment: sources and sync

The IND work register establishes recognised-sponsor membership. It does not
provide company coordinates, industry, websites, career pages or vacancies.
This document records enrichment options and the implemented bounded sync.

## Source options

| Source | Free-access capability | Limitations and usage |
| --- | --- | --- |
| [Wikidata](https://www.wikidata.org/) | Official website (`P856`), industry (`P452`), headquarters and sometimes coordinates; exact [KVK identifier `P3220`](https://www.wikidata.org/wiki/Property:P3220) | Uneven coverage; entity/parent and office ambiguity. Structured data is [CC0](https://www.wikidata.org/wiki/Wikidata:Licensing). Review suggestions; absence is not evidence of no website or industry. |
| [GLEIF API](https://www.gleif.org/en/lei-data/gleif-api) | Legal-entity identity, legal address, registration authority and LEI renewal metadata, without an API key | Only entities with LEIs; not the complete Dutch business register. Match both KVK and Dutch register authority `RA000463`. LEI status is not IND recognition. Follow [LEI data terms](https://www.gleif.org/en/meta/lei-data-terms-of-use) and [API documentation](https://api.gleif.org/docs). |
| [PDOK Locatieserver](https://www.pdok.nl/restful-api/-/article/pdok-locatieserver-1) | Public free address search and geocoding using Dutch address data | Not a company directory or proof of company occupancy. Match the full legal address, not a company-name search. Confirm current dataset/service terms before scaling. |
| [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) | Public published vacancies, locations, departments; read endpoints do not require submission credentials | Board ownership must be mapped to the sponsoring entity. A public vacancy does not prove visa support; observe applicable terms and service limits. |
| [Lever Postings API](https://github.com/lever/postings-api) | Public published vacancies and location/category data | Needs a verified board/site identifier; global and EU instances differ. Current project connector supports the global endpoint only. |
| Official company sites | Careers links, address, business descriptions; some expose structured `Organization` data | Formats vary. Respect robots/terms; do not bypass access controls or assume a homepage is a careers page. No generic site crawler is implemented. |
| [KVK APIs](https://www.kvk.nl/en/ordering-products/get-business-data-with-kvk-apis/) | Authoritative register integration option | Do not assume production access is free. Check current access requirements, pricing and redistribution terms; no KVK API integration is implemented. |
| [OpenStreetMap data](https://www.openstreetmap.org/copyright) | Some company/office POIs and coordinates | Coverage and identity are uncertain; ODbL attribution/share-alike obligations can apply. Not implemented for company matching. |

### Public Nominatim is not a daily bulk enrichment backend

The [public Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/)
sets an absolute maximum of one request per second, identification and attribution
requirements, and mandatory caching for bulk tasks. Periodic/bulk use is
discouraged; regularly scheduled or longer-than-one-day scripts are restricted
to four requests per minute. Do not geocode all sponsors daily through that
public server. PDOK is the implemented Dutch-address option; use a suitable
provider or your own infrastructure if requirements outgrow public services.

## Implemented sync

The `enrich` command processes **1-20 explicitly selected live sponsor KVKs**.
It is manual, not part of the daily scheduler.

1. Require current IND work-register membership in the live database.
2. Query GLEIF by exact KVK, then check Dutch authority, legal name (including
   known IND aliases), active entity, issued LEI and future renewal date.
3. Require one unambiguous Netherlands legal address. Unsupported unit/complex
   addresses go to review instead of guessing.
4. Query PDOK by postcode/house number and require exactly one BAG address with
   matching street, city, postcode and full house number. Validate WGS84 bounds.
5. Propose the corroborated city and coordinates. Never infer an industry,
   website or careers URL simply to satisfy configuration requirements.
6. Query Wikidata using exact KVK `P3220`. Website and industry results are
   **review-only**, not automatically promoted. Conflicting/multiple results
   remain visible in the report.
7. `--apply` adds new location mappings only. Existing company entries are never
   overwritten, even if sources differ. Curated industry, career and ATS fields
   are preserved.

"Corroborated" means the source checks above passed, not that we independently
audited the address or verified that staff work there. A legal address can be a
registered office rather than the job location. Missing matches stay unmapped.

HTTP errors and malformed responses are logged and included in the report.
The command exits nonzero if source errors occurred. Independently corroborated
locations may still be applied; a failing Wikidata suggestion lookup does not
turn a valid GLEIF/PDOK match into a guess. Review the report after every run.

## Run locally with uv

From the repository root, export your local `POSTGRES_*` variables (or
`DATABASE_URL`), and keep `DEMO_MODE=false`:

```powershell
uv sync --locked --group analytics
uv run --no-sync python -m radar.cli init

# Preview: writes provenance/report, but does not change company configuration.
uv run --no-sync python -m radar.cli enrich `
  --kvk 17085815 --kvk 17052456 --kvk 34259528

# Apply only newly corroborated location mappings.
uv run --no-sync python -m radar.cli enrich `
  --kvk 17085815 --kvk 17052456 --kvk 34259528 --apply
```

The example selects ASML Holding N.V., ASML Netherlands B.V. and Adyen N.V.;
membership and source details are checked at execution time. Subsidiaries are
not merged just because they share a brand or address.

The default report is [config/enrichment-report.json](config/enrichment-report.json)
after the first run. Override it with `--report` if needed.
Enrichment supports location-only mappings: industry and careers URL are
optional and remain unknown until curated.

After applying, update the operational database and warehouse:

```powershell
uv run --no-sync python -m radar.cli refresh
uv run --no-sync dbt build --project-dir analytics --profiles-dir analytics
```

## Existing Docker deployment

Rebuild the backend services to include this command and create the additive
raw table. There is no destructive database reset:

```powershell
docker compose build api scheduler analytics
docker compose run --rm --no-deps api python -m radar.cli init
```

The normal configuration mount is read-only. Grant a writable mount **only to
this one-shot command** so it can persist its configuration/report. Use the
separate `/enrichment` target; the normal `/app/config` target stays read-only:

```powershell
docker compose run --rm --no-deps `
  --volume "${PWD}\config:/enrichment:rw" `
  --env COMPANIES_FILE=/enrichment/companies.json `
  api python -m radar.cli enrich `
  --kvk 17085815 --kvk 17052456 --kvk 34259528 --apply `
  --report /enrichment/enrichment-report.json

docker compose run --rm --no-deps api python -m radar.cli refresh
docker compose --profile tools run --rm --no-deps analytics
```

Refresh the website to see newly mapped sponsors. The long-running API and
scheduler can be recreated with the rebuilt image using:

```powershell
docker compose --profile live up -d --no-deps api scheduler
```

The React frontend does not need a rebuild for location-only enrichment.

## Provenance, caching and warehouse flow

- Original enrichment response bodies, URLs, timestamps, source names, status
  codes and SHA-256 hashes are stored append-only in `raw.enrichment_responses`.
  This is separate from pipeline-run response archives.
- Successful HTTP responses are reused for up to 30 days by exact request URL.
  Non-2xx responses are archived but not cached for reuse. Network failures have
  no response body and appear in the report/logs only.
- Requests are sequential with a short pause after successful uncached requests.
  The small explicit batch cap is intentional; this is not a license to loop
  through 13,000 companies against public services.
- Each report includes raw-response IDs and field-source evidence. Company
  configuration is written atomically and checked for concurrent edits before
  application. Existing entries remain untouched.
- The next normal refresh reads company mappings into the operational database
  and lands unchanged operational extracts. Existing staging -> intermediate
  (`itm_*`) -> marts models then expose enriched sponsor locations.
- Enrichment raw responses are audit evidence, not directly consumed by dbt
  models yet. Source-specific staging for GLEIF/PDOK/Wikidata is a future step.
- The report contains public legal-entity/address metadata. Review before
  publishing, especially if extending this to sole traders or personal contact
  data. Never add tokens, private contact details or personal-data scraping.

## Review before expanding

Add field-level verification/provenance and a review workflow before promoting
industry, homepage, careers page, employer size or logos. Keep current sponsor
recognition, entity identity, office location and vacancy-level sponsorship as
separate claims. For a larger rollout, add explicit source budgets, retry/backoff,
retention policies and source-specific dbt staging rather than expanding the
manual batch limit without controls.
