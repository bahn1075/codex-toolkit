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

-- 실제 적재된 데이터 확인
SELECT
    id,
    JSON_VALUE(
        payload,
        '$.updated_at'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    ) AS updated_at,
    JSON_VALUE(
        payload,
        '$.data'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    ) AS data
    -- ,JSON_SERIALIZE(
    --     payload
    --     RETURNING VARCHAR2(2048)
    --     TRUNCATE
    -- ) AS json_text
FROM MEM0.CODEX_MEMORIES
ORDER BY updated_at DESC
-- FETCH FIRST 30 ROWS ONLY
;

SELECT
    id,
    JSON_VALUE(
        payload,
        '$.updated_at'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    ) AS updated_at,
    JSON_VALUE(
        payload,
        '$.data'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    ) AS data,
    JSON_SERIALIZE(
        payload
        RETURNING VARCHAR2(2048)
        TRUNCATE
    ) AS json_text
FROM MEM0.CODEX_MEMORIES
WHERE JSON_VALUE(
          payload,
          '$.source_root'
          RETURNING VARCHAR2(4000)
          NULL ON ERROR
      ) IS NULL
ORDER BY id;

-- 파일별
WITH memory_values AS (
    SELECT
        JSON_VALUE(
            payload,
            '$.source_root'
            RETURNING VARCHAR2(4000)
            NULL ON ERROR
        ) AS source_root,
        JSON_VALUE(
            payload,
            '$.updated_at'
            RETURNING VARCHAR2(4000)
            NULL ON ERROR
        ) AS updated_at
    FROM MEM0.CODEX_MEMORIES
), parsed_values AS (
    SELECT
        source_root,
        TO_TIMESTAMP(
            SUBSTR(updated_at, 1, 19),
            'YYYY-MM-DD"T"HH24:MI:SS'
        ) AS updated_at
    FROM memory_values
)
SELECT
    source_root,
    COUNT(*) AS row_count,
    TO_CHAR(MAX(updated_at), 'YYYY-MM-DD HH24:MI:SS') AS max_updated_at
FROM parsed_values
GROUP BY source_root
ORDER BY source_root DESC;


-- 실제 적재된 데이터 중 키워드 확인
SELECT
    id,
    JSON_VALUE(
        payload,
        '$.updated_at'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    ) AS updated_at,
    JSON_VALUE(
        payload,
        '$.source_root'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    ) AS source_root,
    JSON_VALUE(
        payload,
        '$.data'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    ) AS data
    -- ,JSON_SERIALIZE(
    --     payload
    --     RETURNING VARCHAR2(2048)
    --     TRUNCATE
    -- ) AS json_text
FROM MEM0.CODEX_MEMORIES
where     JSON_VALUE(
        payload,
        '$.data'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    )  like '%vkfrhd%'
ORDER BY updated_at DESC
-- FETCH FIRST 30 ROWS ONLY
;

DELETE from mem0.codex_memories
where     JSON_VALUE(
        payload,
        '$.data'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    )  like '%Wkdwlsgh%';

DELETE from mem0.codex_memories
where     JSON_VALUE(
        payload,
        '$.data'
        RETURNING VARCHAR2(4000)
        NULL ON ERROR
    )  like '%vkfrhd%';

    commit;


grant GRAPH_DEVELOPER to mem0;
ALTER USER MEM0 GRANT CONNECT THROUGH GRAPH$PROXY_USER;