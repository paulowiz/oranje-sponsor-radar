with ranked as (
    select
        *,
        row_number() over (
            partition by sponsor_id order by extracted_at desc, raw_record_id desc
        ) as row_priority
    from {{ ref('stg_sponsors') }}
)
select
    sponsor_id, kvk, sponsor_name, is_recognized, first_seen_on, last_seen_on,
    removed_on, city, industry, latitude, longitude, careers_url, dataset_mode,
    extracted_at as source_extracted_at
from ranked
where row_priority = 1
