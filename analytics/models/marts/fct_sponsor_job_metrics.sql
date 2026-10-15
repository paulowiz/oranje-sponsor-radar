select
    s.sponsor_id as sponsor_key,
    coalesce(m.tracked_job_count, 0)::bigint as tracked_job_count,
    coalesce(m.active_job_count, 0)::bigint as active_job_count,
    coalesce(m.fresh_open_role_count, 0)::bigint as fresh_open_role_count,
    s.dataset_mode,
    current_timestamp as mart_built_at
from {{ ref('int_sponsors_deduplicated') }} as s
left join {{ ref('int_sponsor_job_metrics') }} as m on s.sponsor_id = m.sponsor_id
