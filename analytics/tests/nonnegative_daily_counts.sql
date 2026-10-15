select *
from {{ ref('fct_daily_sponsor_metrics') }}
where sponsor_count < 0 or added_count < 0 or removed_count < 0
    or restored_count < 0 or active_job_count < 0
