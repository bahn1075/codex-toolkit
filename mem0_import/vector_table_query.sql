select count(*) from MEM0.CODEX_MEMORIES;

select sum(bytes/1024/1024) from dba_segments where segment_name = 'CODEX_MEMORIES' group by segment_name;

select segment_name, sum(bytes/1024/1024) from dba_segments where segment_name like '%CODEX_MEMORIES%' group by segment_name;

-- json 컬럼을 varchar 변환해서 조회
SELECT id,vector, JSON_SERIALIZE(
         payload
         RETURNING VARCHAR2(2048)
         TRUNCATE
       ) AS json_text
FROM MEM0.CODEX_MEMORIES;