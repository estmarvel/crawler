-- Persist the six-stage processing funnel for every crawler task.
CREATE TABLE `crawl_task_stage` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `crawl_task_id` BIGINT NOT NULL,
    `stage` VARCHAR(32) NOT NULL,
    `status` VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    `input_count` INTEGER NOT NULL DEFAULT 0,
    `success_count` INTEGER NOT NULL DEFAULT 0,
    `skipped_count` INTEGER NOT NULL DEFAULT 0,
    `failed_count` INTEGER NOT NULL DEFAULT 0,
    `error_message` TEXT NULL,
    `metadata` JSON NULL,
    `started_at` DATETIME(3) NULL,
    `finished_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `crawl_task_stage_crawl_task_id_stage_key`(`crawl_task_id`, `stage`),
    INDEX `crawl_task_stage_stage_status_idx`(`stage`, `status`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE UNIQUE INDEX `project_project_code_key` ON `project`(`project_code`);
CREATE UNIQUE INDEX `project_notice_source_identity` ON `project_notice`(`source_site`, `source_notice_id`);
CREATE UNIQUE INDEX `notice_extraction_model_version` ON `notice_extraction`(`raw_notice_id`, `extraction_model`, `extraction_version`);

-- Keep immutable per-run discovery history without moving raw_notice.crawl_task_id.
CREATE TABLE `crawl_task_notice` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `crawl_task_id` BIGINT NOT NULL,
    `raw_notice_id` BIGINT NOT NULL,
    `is_new` BOOLEAN NOT NULL DEFAULT false,
    `is_updated` BOOLEAN NOT NULL DEFAULT false,
    `content_hash` VARCHAR(64) NULL,
    `discovered_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `crawl_task_notice_crawl_task_id_raw_notice_id_key`(`crawl_task_id`, `raw_notice_id`),
    INDEX `crawl_task_notice_raw_notice_id_idx`(`raw_notice_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

ALTER TABLE `crawl_task_stage`
  ADD CONSTRAINT `crawl_task_stage_crawl_task_id_fkey`
  FOREIGN KEY (`crawl_task_id`) REFERENCES `crawl_task`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE `crawl_task_notice`
  ADD CONSTRAINT `crawl_task_notice_crawl_task_id_fkey`
  FOREIGN KEY (`crawl_task_id`) REFERENCES `crawl_task`(`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT `crawl_task_notice_raw_notice_id_fkey`
  FOREIGN KEY (`raw_notice_id`) REFERENCES `raw_notice`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- Best-effort baseline for tasks created before stage tracking existed.
INSERT IGNORE INTO `crawl_task_notice` (`crawl_task_id`, `raw_notice_id`, `is_new`, `is_updated`, `content_hash`, `discovered_at`)
SELECT `crawl_task_id`, `id`, false, false, `fingerprint`, `crawl_time`
FROM `raw_notice`
WHERE `crawl_task_id` IS NOT NULL;

INSERT INTO `crawl_task_stage` (`crawl_task_id`, `stage`, `status`, `input_count`, `success_count`, `skipped_count`, `failed_count`, `started_at`, `finished_at`, `created_at`, `updated_at`)
SELECT id, 'FETCH', IF(status IN ('SUCCESS', 'PARTIAL_SUCCESS'), 'SUCCESS', status), total_count, total_count, 0, fail_count, started_at, finished_at, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
FROM `crawl_task`;

INSERT INTO `crawl_task_stage` (`crawl_task_id`, `stage`, `status`, `input_count`, `success_count`, `skipped_count`, `failed_count`, `started_at`, `finished_at`, `created_at`, `updated_at`)
SELECT t.id, s.stage, IF(t.status IN ('SUCCESS', 'PARTIAL_SUCCESS'), 'SUCCESS', IF(t.status IN ('FAILED', 'TIMED_OUT'), 'SKIPPED', t.status)),
       t.total_count,
       CASE s.stage
         WHEN 'PARSE' THEN (SELECT COUNT(DISTINCT rn.id) FROM raw_notice rn JOIN notice_extraction ne ON ne.raw_notice_id = rn.id WHERE rn.crawl_task_id = t.id)
         WHEN 'DEDUPLICATE' THEN (SELECT COUNT(DISTINCT rn.id) FROM raw_notice rn JOIN notice_extraction ne ON ne.raw_notice_id = rn.id WHERE rn.crawl_task_id = t.id)
         WHEN 'STORE' THEN (SELECT COUNT(DISTINCT rn.id) FROM raw_notice rn JOIN notice_extraction ne ON ne.raw_notice_id = rn.id WHERE rn.crawl_task_id = t.id)
         WHEN 'MATCH' THEN (SELECT COUNT(DISTINCT rn.id) FROM raw_notice rn JOIN notice_extraction ne ON ne.raw_notice_id = rn.id WHERE rn.crawl_task_id = t.id AND ne.project_notice_id IS NOT NULL)
         ELSE 0
       END,
       0, 0, t.started_at, t.finished_at, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
FROM crawl_task t
CROSS JOIN (
  SELECT 'PARSE' AS stage UNION ALL SELECT 'DEDUPLICATE' UNION ALL SELECT 'STORE' UNION ALL SELECT 'MATCH'
) s;

INSERT INTO `crawl_task_stage` (`crawl_task_id`, `stage`, `status`, `input_count`, `success_count`, `skipped_count`, `failed_count`, `started_at`, `finished_at`, `created_at`, `updated_at`)
SELECT id, 'RECOMMEND', 'SKIPPED', 0, 0, 0, 0, started_at, finished_at, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
FROM `crawl_task`;
