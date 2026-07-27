-- Logical crawl run on 2026-07-16, aggregated per data source.
-- The run had two continuous phases:
--   1) recent 180 days, starting at 15:06:29;
--   2) full-history completion, starting at 16:32:29 and reusing dedup state.
-- The crawl_task schema belongs to one data source, so this logical run creates
-- one task for huaxin and one task for jiubang.
-- Foreign keys from seed_priority_data_sources.sql when inserted into an empty
-- data_source table: huaxin = 6, jiubang = 14.

USE `crawler`;

-- Preflight check: verify the two foreign keys before executing the INSERT.
SELECT `id`, `name`, `short_code`
FROM `data_source`
WHERE (`id` = 6 AND `short_code` = 'huaxin')
   OR (`id` = 14 AND `short_code` = 'jiubang')
ORDER BY `id`;

-- Task 1: huaxin (data_source_id = 6)
START TRANSACTION;
INSERT INTO `crawl_task` (
    `data_source_id`,
    `task_type`,
    `status`,
    `scheduled_at`,
    `started_at`,
    `finished_at`,
    `total_count`,
    `success_count`,
    `fail_count`,
    `error_message`,
    `created_at`
) VALUES (
    6,
    'HISTORY_FULL',
    'SUCCESS',
    NULL,
    '2026-07-16 15:06:29.000',
    '2026-07-16 18:01:54.797',
    2691,
    2691,
    0,
    NULL,
    '2026-07-16 15:06:29.000'
);
COMMIT;

-- Task 2: jiubang (data_source_id = 14)
START TRANSACTION;
INSERT INTO `crawl_task` (
    `data_source_id`,
    `task_type`,
    `status`,
    `scheduled_at`,
    `started_at`,
    `finished_at`,
    `total_count`,
    `success_count`,
    `fail_count`,
    `error_message`,
    `created_at`
) VALUES (
    14,
    'HISTORY_FULL',
    'SUCCESS',
    NULL,
    '2026-07-16 15:06:29.000',
    '2026-07-16 17:13:45.802',
    1153,
    1153,
    0,
    NULL,
    '2026-07-16 15:06:29.000'
);
COMMIT;

-- Verification query (read-only): exactly two rows should be returned.
SELECT
    task.`id`,
    source.`short_code`,
    task.`task_type`,
    task.`status`,
    task.`scheduled_at`,
    task.`started_at`,
    task.`finished_at`,
    task.`total_count`,
    task.`success_count`,
    task.`fail_count`,
    task.`error_message`,
    task.`created_at`
FROM `crawl_task` AS task
INNER JOIN `data_source` AS source
    ON source.`id` = task.`data_source_id`
WHERE task.`data_source_id` IN (6, 14)
  AND task.`task_type` = 'HISTORY_FULL'
  AND task.`started_at` = '2026-07-16 15:06:29.000'
ORDER BY source.`short_code`;
