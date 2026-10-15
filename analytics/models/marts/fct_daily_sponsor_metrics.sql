select
    to_char(observation_date, 'YYYYMMDD')::integer as observation_date_key,
    observation_date,
    sponsor_count,
    added_count,
    removed_count,
    restored_count,
    active_job_count,
    net_sponsor_change,
    source_hash,
    source_url,
    observed_at,
    dataset_mode
from {{ ref('int_daily_sponsor_metrics') }}
