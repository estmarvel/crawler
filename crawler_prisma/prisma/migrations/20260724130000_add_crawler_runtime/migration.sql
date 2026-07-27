ALTER TABLE `crawl_task`
    ADD COLUMN `schedule_id` INTEGER NULL,
    ADD COLUMN `run_config` JSON NULL,
    ADD COLUMN `process_pid` INTEGER NULL,
    ADD COLUMN `log_path` VARCHAR(1024) NULL,
    ADD COLUMN `output_path` VARCHAR(1024) NULL,
    ADD COLUMN `cancel_requested_at` DATETIME(3) NULL;

CREATE TABLE `crawl_schedule` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `data_source_id` INTEGER NOT NULL,
    `name` VARCHAR(128) NOT NULL,
    `task_type` VARCHAR(32) NOT NULL,
    `interval_minutes` INTEGER NOT NULL,
    `run_config` JSON NULL,
    `is_enabled` BOOLEAN NOT NULL DEFAULT true,
    `next_run_at` DATETIME(3) NOT NULL,
    `last_run_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `crawl_schedule_is_enabled_next_run_at_idx`(`is_enabled`, `next_run_at`),
    INDEX `crawl_schedule_data_source_id_idx`(`data_source_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE `crawl_task_event` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `crawl_task_id` BIGINT NOT NULL,
    `level` VARCHAR(16) NOT NULL DEFAULT 'INFO',
    `event_type` VARCHAR(32) NOT NULL,
    `message` TEXT NOT NULL,
    `detail` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `crawl_task_event_crawl_task_id_created_at_idx`(`crawl_task_id`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE `crawl_exception` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `data_source_id` INTEGER NOT NULL,
    `crawl_task_id` BIGINT NULL,
    `exception_type` VARCHAR(32) NOT NULL,
    `source_type` VARCHAR(32) NOT NULL,
    `source_ref` VARCHAR(256) NOT NULL,
    `message` TEXT NOT NULL,
    `status` VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    `retry_count` INTEGER NOT NULL DEFAULT 0,
    `handled_by` VARCHAR(128) NULL,
    `remark` TEXT NULL,
    `resolved_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `crawl_exception_source_type_source_ref_exception_type_key`(`source_type`, `source_ref`, `exception_type`),
    INDEX `crawl_exception_status_created_at_idx`(`status`, `created_at`),
    INDEX `crawl_exception_data_source_id_idx`(`data_source_id`),
    INDEX `crawl_exception_crawl_task_id_idx`(`crawl_task_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE INDEX `crawl_task_schedule_id_idx` ON `crawl_task`(`schedule_id`);

ALTER TABLE `crawl_task` ADD CONSTRAINT `crawl_task_schedule_id_fkey`
    FOREIGN KEY (`schedule_id`) REFERENCES `crawl_schedule`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE `crawl_schedule` ADD CONSTRAINT `crawl_schedule_data_source_id_fkey`
    FOREIGN KEY (`data_source_id`) REFERENCES `data_source`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE `crawl_task_event` ADD CONSTRAINT `crawl_task_event_crawl_task_id_fkey`
    FOREIGN KEY (`crawl_task_id`) REFERENCES `crawl_task`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE `crawl_exception` ADD CONSTRAINT `crawl_exception_data_source_id_fkey`
    FOREIGN KEY (`data_source_id`) REFERENCES `data_source`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE `crawl_exception` ADD CONSTRAINT `crawl_exception_crawl_task_id_fkey`
    FOREIGN KEY (`crawl_task_id`) REFERENCES `crawl_task`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;
