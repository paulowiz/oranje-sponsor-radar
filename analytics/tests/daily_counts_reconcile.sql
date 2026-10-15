select *
from {{ ref('fct_daily_sponsor_metrics') }}
where net_sponsor_change is not null
    and net_sponsor_change != added_count + restored_count - removed_count
