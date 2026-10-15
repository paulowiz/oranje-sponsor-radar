select
    r.id as raw_record_id,
    r.batch_id,
    b.extracted_at,
    b.mode as dataset_mode,
    btrim(r.payload ->> 'id') as job_id,
    btrim(r.payload ->> 'sponsor_id') as sponsor_id,
    lower(btrim(r.payload ->> 'provider')) as provider,
    btrim(r.payload ->> 'board') as board,
    nullif(btrim(r.payload ->> 'title'), '') as job_title,
    nullif(btrim(r.payload ->> 'location'), '') as job_location,
    lower(btrim(r.payload ->> 'job_type')) as job_type,
    nullif(btrim(r.payload ->> 'url'), '') as job_url,
    (r.payload ->> 'active')::boolean as is_active,
    (r.payload ->> 'first_seen')::timestamptz as first_seen_at,
    (r.payload ->> 'last_seen')::timestamptz as last_seen_at,
    lower(btrim(r.payload ->> 'sponsorship')) as sponsorship
from {{ source('raw', 'entity_records') }} as r
inner join {{ source('raw', 'extract_batches') }} as b on r.batch_id = b.id
where r.source_table = 'jobs'
