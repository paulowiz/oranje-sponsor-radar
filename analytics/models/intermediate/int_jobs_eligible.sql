select
    j.*,
    s.is_recognized,
    j.is_active
        and s.is_recognized
        and j.last_seen_at >= '{{ run_started_at.isoformat() }}'::timestamptz
            - interval '72 hours' as is_discoverable
from {{ ref('int_jobs_deduplicated') }} as j
inner join {{ ref('int_sponsors_deduplicated') }} as s on j.sponsor_id = s.sponsor_id
