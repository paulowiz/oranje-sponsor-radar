with ranked as (
    select
        *,
        row_number() over (
            partition by job_id order by extracted_at desc, raw_record_id desc
        ) as row_priority
    from {{ ref('stg_jobs') }}
)
select
    job_id, sponsor_id, provider, board, job_title, job_location, job_type,
    job_url, is_active, first_seen_at, last_seen_at, sponsorship, dataset_mode,
    extracted_at as source_extracted_at
from ranked
where row_priority = 1
