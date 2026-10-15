select
    r.id as raw_record_id,
    r.batch_id,
    b.extracted_at,
    b.mode as dataset_mode,
    (r.payload ->> 'day')::date as observation_date,
    (r.payload ->> 'sponsor_count')::integer as sponsor_count,
    (r.payload ->> 'added_count')::integer as added_count,
    (r.payload ->> 'removed_count')::integer as removed_count,
    (r.payload ->> 'restored_count')::integer as restored_count,
    (r.payload ->> 'active_job_count')::integer as active_job_count,
    btrim(r.payload ->> 'source_hash') as source_hash,
    btrim(r.payload ->> 'source_url') as source_url,
    (r.payload ->> 'observed_at')::timestamptz as observed_at
from {{ source('raw', 'entity_records') }} as r
inner join {{ source('raw', 'extract_batches') }} as b on r.batch_id = b.id
where r.source_table = 'daily_snapshots'
