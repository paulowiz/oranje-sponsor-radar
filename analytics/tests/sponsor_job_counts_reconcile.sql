select m.*
from {{ ref('fct_sponsor_job_metrics') }} as m
left join (
    select
        sponsor_key,
        count(*) as tracked_count,
        count(*) filter (where is_active) as active_count,
        count(*) filter (where is_discoverable_at_build) as fresh_count
    from {{ ref('fct_job_openings') }}
    group by sponsor_key
) as j on m.sponsor_key = j.sponsor_key
where m.tracked_job_count != coalesce(j.tracked_count, 0)
    or m.active_job_count != coalesce(j.active_count, 0)
    or m.fresh_open_role_count != coalesce(j.fresh_count, 0)
