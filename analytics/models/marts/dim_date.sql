with dates as (
    select observation_date as date_day from {{ ref('int_daily_sponsor_metrics') }}
    union all
    select observed_on from {{ ref('int_sponsor_events_deduplicated') }}
    union all
    select (first_seen_at at time zone 'UTC')::date from {{ ref('int_jobs_deduplicated') }}
    union all
    select (last_seen_at at time zone 'UTC')::date from {{ ref('int_jobs_deduplicated') }}
),
bounds as (
    select
        coalesce(min(date_day), (current_timestamp at time zone 'UTC')::date) as first_day,
        greatest(
            max(date_day), (current_timestamp at time zone 'UTC')::date
        ) as last_day
    from dates
),
calendar as (
    select generate_series(first_day::timestamp, last_day::timestamp, interval '1 day')::date
        as date_day
    from bounds
)
select
    to_char(date_day, 'YYYYMMDD')::integer as date_key,
    date_day,
    extract(year from date_day)::integer as year_number,
    extract(month from date_day)::integer as month_number,
    extract(day from date_day)::integer as day_of_month,
    extract(isodow from date_day)::integer as iso_day_of_week
from calendar
