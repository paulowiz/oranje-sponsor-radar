select
    sponsor_id,
    count(*) as tracked_job_count,
    count(*) filter (where is_active) as active_job_count,
    count(*) filter (where is_discoverable) as fresh_open_role_count
from {{ ref('itm_jobs_eligible') }}
group by sponsor_id
