with ranked as (
    select
        *,
        row_number() over (
            partition by observation_date order by extracted_at desc, raw_record_id desc
        ) as row_priority
    from {{ ref('stg_daily_snapshots') }}
),
daily as (
    select * from ranked where row_priority = 1
)
select
    observation_date, sponsor_count, added_count, removed_count, restored_count,
    active_job_count, source_hash, source_url, observed_at, dataset_mode,
    sponsor_count - lag(sponsor_count) over (order by observation_date) as net_sponsor_change
from daily
