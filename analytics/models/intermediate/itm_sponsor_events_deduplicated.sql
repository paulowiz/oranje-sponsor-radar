with ranked as (
    select
        *,
        row_number() over (
            partition by event_id order by extracted_at desc, raw_record_id desc
        ) as row_priority
    from {{ ref('stg_sponsor_events') }}
)
select event_id, sponsor_id, observed_on, observed_at, event_kind, dataset_mode
from ranked
where row_priority = 1
