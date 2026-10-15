select batch_id, source_table, record_key
from {{ source('raw', 'entity_records') }}
group by batch_id, source_table, record_key
having count(*) > 1
