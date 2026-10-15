select
    r.id as raw_record_id,
    r.batch_id,
    b.extracted_at,
    b.mode as dataset_mode,
    (r.payload ->> 'id')::bigint as event_id,
    btrim(r.payload ->> 'sponsor_id') as sponsor_id,
    (r.payload ->> 'observed_on')::date as observed_on,
    (r.payload ->> 'observed_at')::timestamptz as observed_at,
    lower(btrim(r.payload ->> 'kind')) as event_kind
from {{ source('raw', 'entity_records') }} as r
inner join {{ source('raw', 'extract_batches') }} as b on r.batch_id = b.id
where r.source_table = 'sponsor_events'
