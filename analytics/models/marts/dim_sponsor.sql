select
    sponsor_id as sponsor_key,
    kvk,
    sponsor_name,
    is_recognized,
    city,
    industry,
    latitude,
    longitude,
    careers_url,
    first_seen_on,
    last_seen_on,
    removed_on,
    dataset_mode,
    source_extracted_at
from {{ ref('itm_sponsors_deduplicated') }}
