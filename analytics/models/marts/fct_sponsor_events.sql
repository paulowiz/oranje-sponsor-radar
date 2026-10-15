select
    event_id as sponsor_event_key,
    sponsor_id as sponsor_key,
    to_char(observed_on, 'YYYYMMDD')::integer as observed_date_key,
    observed_at,
    event_kind,
    dataset_mode,
    1 as event_count
from {{ ref('int_sponsor_events_deduplicated') }}
