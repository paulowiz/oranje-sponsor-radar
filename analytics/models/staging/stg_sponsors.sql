select
    r.id as raw_record_id,
    r.batch_id,
    b.extracted_at,
    b.mode as dataset_mode,
    btrim(r.payload ->> 'id') as sponsor_id,
    nullif(btrim(r.payload ->> 'kvk'), '') as kvk,
    nullif(btrim(r.payload ->> 'name'), '') as sponsor_name,
    (r.payload ->> 'recognized')::boolean as is_recognized,
    (r.payload ->> 'first_seen')::date as first_seen_on,
    (r.payload ->> 'last_seen')::date as last_seen_on,
    (r.payload ->> 'removed_on')::date as removed_on,
    nullif(btrim(r.payload ->> 'city'), '') as city,
    nullif(btrim(r.payload ->> 'industry'), '') as industry,
    (r.payload ->> 'latitude')::double precision as latitude,
    (r.payload ->> 'longitude')::double precision as longitude,
    nullif(btrim(r.payload ->> 'careers_url'), '') as careers_url
from {{ source('raw', 'entity_records') }} as r
inner join {{ source('raw', 'extract_batches') }} as b on r.batch_id = b.id
where r.source_table = 'sponsors'
