-- AlterTable
ALTER TABLE `company`
    ADD COLUMN `registered_capital` DECIMAL(18, 2) NULL,
    ADD COLUMN `established_date` DATE NULL,
    ADD COLUMN `legal_person` VARCHAR(64) NULL,
    ADD COLUMN `employee_count` INTEGER NULL;

-- AlterTable
ALTER TABLE `project_notice_attachment`
    ADD COLUMN `storage_path` VARCHAR(1024) NULL,
    ADD COLUMN `file_hash` VARCHAR(64) NULL,
    ADD COLUMN `file_size_bytes` BIGINT NULL,
    ADD COLUMN `parse_status` VARCHAR(32) NOT NULL DEFAULT 'PENDING';

-- AlterTable
ALTER TABLE `company_profile_snapshot`
    DROP INDEX `company_profile_snapshot_company_id_snapshot_date_idx`,
    ADD UNIQUE INDEX `company_profile_snapshot_company_id_snapshot_date_key`(`company_id`, `snapshot_date`);

-- AlterTable
ALTER TABLE `project_company_relation`
    ADD UNIQUE INDEX `project_company_relation_stage_unique`(`project_id`, `company_name`, `relation_type`, `stage_type`);

-- CreateTable
CREATE TABLE `data_source` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(200) NOT NULL,
    `short_code` VARCHAR(32) NOT NULL,
    `base_url` VARCHAR(512) NULL,
    `source_level` VARCHAR(32) NULL,
    `province` VARCHAR(32) NULL,
    `crawl_frequency_minutes` INTEGER NOT NULL DEFAULT 60,
    `crawl_config` JSON NULL,
    `is_enabled` BOOLEAN NOT NULL DEFAULT true,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `data_source_short_code_key`(`short_code`),
    INDEX `data_source_is_enabled_idx`(`is_enabled`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `crawl_task` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `data_source_id` INTEGER NOT NULL,
    `task_type` VARCHAR(32) NOT NULL,
    `status` VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    `scheduled_at` DATETIME(3) NULL,
    `started_at` DATETIME(3) NULL,
    `finished_at` DATETIME(3) NULL,
    `total_count` INTEGER NOT NULL DEFAULT 0,
    `success_count` INTEGER NOT NULL DEFAULT 0,
    `fail_count` INTEGER NOT NULL DEFAULT 0,
    `error_message` TEXT NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `crawl_task_data_source_id_status_idx`(`data_source_id`, `status`),
    INDEX `crawl_task_status_scheduled_at_idx`(`status`, `scheduled_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `raw_notice` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `data_source_id` INTEGER NOT NULL,
    `crawl_task_id` BIGINT NULL,
    `source_url` VARCHAR(1024) NOT NULL,
    `source_notice_id` VARCHAR(256) NULL,
    `title` VARCHAR(512) NULL,
    `raw_html` LONGTEXT NULL,
    `raw_text` LONGTEXT NULL,
    `publish_date` DATETIME(3) NULL,
    `crawl_time` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `parse_status` VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    `fingerprint` VARCHAR(64) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `raw_notice_data_source_id_source_notice_id_key`(`data_source_id`, `source_notice_id`),
    INDEX `raw_notice_parse_status_idx`(`parse_status`),
    INDEX `raw_notice_fingerprint_idx`(`fingerprint`),
    INDEX `raw_notice_crawl_task_id_idx`(`crawl_task_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `raw_notice_attachment` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `raw_notice_id` BIGINT NOT NULL,
    `file_name` VARCHAR(512) NULL,
    `file_url` VARCHAR(1024) NULL,
    `storage_path` VARCHAR(1024) NULL,
    `file_hash` VARCHAR(64) NULL,
    `file_size_bytes` BIGINT NULL,
    `file_type` VARCHAR(32) NULL,
    `parse_status` VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `raw_notice_attachment_raw_notice_id_idx`(`raw_notice_id`),
    INDEX `raw_notice_attachment_file_hash_idx`(`file_hash`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `notice_extraction` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `raw_notice_id` BIGINT NOT NULL,
    `project_notice_id` INTEGER NULL,
    `notice_type` VARCHAR(64) NOT NULL,
    `extracted_fields` JSON NOT NULL,
    `extraction_model` VARCHAR(64) NULL,
    `extraction_version` VARCHAR(32) NULL,
    `confidence_score` DECIMAL(5, 4) NULL,
    `field_confidences` JSON NULL,
    `source_text_snippet` TEXT NULL,
    `is_verified` BOOLEAN NOT NULL DEFAULT false,
    `verified_by` INTEGER NULL,
    `verified_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `notice_extraction_raw_notice_id_idx`(`raw_notice_id`),
    INDEX `notice_extraction_project_notice_id_idx`(`project_notice_id`),
    INDEX `notice_extraction_notice_type_is_verified_idx`(`notice_type`, `is_verified`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `project_requirement` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `project_id` INTEGER NOT NULL,
    `notice_id` INTEGER NULL,
    `requirement_type` VARCHAR(64) NOT NULL,
    `requirement_text` TEXT NOT NULL,
    `keywords` JSON NULL,
    `is_mandatory` BOOLEAN NOT NULL DEFAULT true,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `project_requirement_project_id_requirement_type_idx`(`project_id`, `requirement_type`),
    INDEX `project_requirement_notice_id_idx`(`notice_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `recommendation_result` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `company_id` INTEGER NOT NULL,
    `project_id` INTEGER NOT NULL,
    `match_score` DECIMAL(5, 2) NOT NULL,
    `win_probability` DECIMAL(5, 2) NULL,
    `recommend_level` VARCHAR(32) NULL,
    `competition_level` VARCHAR(16) NULL,
    `score_breakdown` JSON NULL,
    `reason` JSON NULL,
    `risk` JSON NULL,
    `algorithm_version` VARCHAR(32) NOT NULL,
    `is_read` BOOLEAN NOT NULL DEFAULT false,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `expired_at` DATETIME(3) NULL,

    UNIQUE INDEX `recommendation_result_unique_version`(`company_id`, `project_id`, `algorithm_version`),
    INDEX `recommendation_result_company_id_match_score_idx`(`company_id`, `match_score`),
    INDEX `recommendation_result_company_id_created_at_idx`(`company_id`, `created_at`),
    INDEX `recommendation_result_project_id_idx`(`project_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `user_feedback` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `user_id` INTEGER NOT NULL,
    `company_id` INTEGER NOT NULL,
    `project_id` INTEGER NOT NULL,
    `feedback_type` VARCHAR(32) NOT NULL,
    `comment` TEXT NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `user_feedback_user_id_project_id_key`(`user_id`, `project_id`),
    INDEX `user_feedback_company_id_feedback_type_idx`(`company_id`, `feedback_type`),
    INDEX `user_feedback_project_id_idx`(`project_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `recommendation_model_version` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `version_code` VARCHAR(32) NOT NULL,
    `model_type` VARCHAR(32) NOT NULL,
    `feature_config` JSON NULL,
    `description` TEXT NULL,
    `is_active` BOOLEAN NOT NULL DEFAULT false,
    `metrics` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `recommendation_model_version_version_code_key`(`version_code`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `competition_analysis` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `project_id` INTEGER NOT NULL,
    `target_company_id` INTEGER NOT NULL,
    `competitor_company_id` INTEGER NULL,
    `competitor_name` VARCHAR(200) NULL,
    `threat_level` VARCHAR(32) NULL,
    `competitor_score` DECIMAL(5, 2) NULL,
    `overall_win_rate` DECIMAL(5, 2) NULL,
    `encounter_count` INTEGER NOT NULL DEFAULT 0,
    `encounter_opponent_wins` INTEGER NOT NULL DEFAULT 0,
    `advantages` JSON NULL,
    `weaknesses` JSON NULL,
    `our_advantages` JSON NULL,
    `our_weaknesses` JSON NULL,
    `radar_data` JSON NULL,
    `algorithm_version` VARCHAR(32) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `competition_analysis_project_id_target_company_id_idx`(`project_id`, `target_company_id`),
    INDEX `competition_analysis_competitor_company_id_idx`(`competitor_company_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `win_probability_analysis` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `project_id` INTEGER NOT NULL,
    `company_id` INTEGER NOT NULL,
    `win_probability` DECIMAL(5, 2) NULL,
    `probability_lower` DECIMAL(5, 2) NULL,
    `probability_upper` DECIMAL(5, 2) NULL,
    `positive_factors` JSON NULL,
    `negative_factors` JSON NULL,
    `risk_factors` JSON NULL,
    `suggestions` JSON NULL,
    `competition_intensity` VARCHAR(16) NULL,
    `known_competitors_count` INTEGER NOT NULL DEFAULT 0,
    `algorithm_version` VARCHAR(32) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `win_probability_analysis_project_id_company_id_idx`(`project_id`, `company_id`),
    INDEX `win_probability_analysis_company_id_idx`(`company_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `company_qualification` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `company_id` INTEGER NOT NULL,
    `cert_name` VARCHAR(200) NOT NULL,
    `cert_level` VARCHAR(64) NULL,
    `cert_no` VARCHAR(128) NULL,
    `issue_date` DATE NULL,
    `expiry_date` DATE NULL,
    `issuing_authority` VARCHAR(200) NULL,
    `status` VARCHAR(32) NOT NULL DEFAULT 'VALID',
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `company_qualification_company_id_status_idx`(`company_id`, `status`),
    INDEX `company_qualification_expiry_date_idx`(`expiry_date`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `company_personnel` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `company_id` INTEGER NOT NULL,
    `person_name` VARCHAR(64) NULL,
    `person_role` VARCHAR(64) NULL,
    `cert_name` VARCHAR(200) NULL,
    `cert_no` VARCHAR(128) NULL,
    `cert_level` VARCHAR(64) NULL,
    `is_available` BOOLEAN NOT NULL DEFAULT true,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `company_personnel_company_id_idx`(`company_id`),
    INDEX `company_personnel_cert_name_idx`(`cert_name`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `contract` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `project_id` INTEGER NOT NULL,
    `notice_id` INTEGER NULL,
    `contract_no` VARCHAR(128) NULL,
    `contract_name` VARCHAR(512) NULL,
    `buyer_name` VARCHAR(200) NULL,
    `seller_company_id` INTEGER NULL,
    `seller_name` VARCHAR(200) NULL,
    `contract_amount` DECIMAL(18, 2) NULL,
    `sign_date` DATE NULL,
    `start_date` DATE NULL,
    `end_date` DATE NULL,
    `contract_content` TEXT NULL,
    `performance_status` VARCHAR(32) NOT NULL DEFAULT 'ONGOING',
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `contract_project_id_idx`(`project_id`),
    INDEX `contract_notice_id_idx`(`notice_id`),
    INDEX `contract_seller_company_id_idx`(`seller_company_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `report` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `user_id` INTEGER NOT NULL,
    `company_id` INTEGER NULL,
    `project_id` INTEGER NULL,
    `report_type` VARCHAR(64) NOT NULL,
    `title` VARCHAR(512) NULL,
    `content_json` JSON NULL,
    `status` VARCHAR(32) NOT NULL DEFAULT 'GENERATING',
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `report_user_id_report_type_idx`(`user_id`, `report_type`),
    INDEX `report_project_id_idx`(`project_id`),
    INDEX `report_company_id_idx`(`company_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `sys_dict` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `dict_type` VARCHAR(64) NOT NULL,
    `dict_code` VARCHAR(64) NOT NULL,
    `dict_label` VARCHAR(128) NOT NULL,
    `sort_order` INTEGER NOT NULL DEFAULT 0,
    `is_enabled` BOOLEAN NOT NULL DEFAULT true,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `sys_dict_dict_type_dict_code_key`(`dict_type`, `dict_code`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateIndex
CREATE INDEX `project_notice_attachment_file_hash_idx` ON `project_notice_attachment`(`file_hash`);

-- AddForeignKey
ALTER TABLE `crawl_task` ADD CONSTRAINT `crawl_task_data_source_id_fkey` FOREIGN KEY (`data_source_id`) REFERENCES `data_source`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `raw_notice` ADD CONSTRAINT `raw_notice_data_source_id_fkey` FOREIGN KEY (`data_source_id`) REFERENCES `data_source`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `raw_notice` ADD CONSTRAINT `raw_notice_crawl_task_id_fkey` FOREIGN KEY (`crawl_task_id`) REFERENCES `crawl_task`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `raw_notice_attachment` ADD CONSTRAINT `raw_notice_attachment_raw_notice_id_fkey` FOREIGN KEY (`raw_notice_id`) REFERENCES `raw_notice`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `notice_extraction` ADD CONSTRAINT `notice_extraction_raw_notice_id_fkey` FOREIGN KEY (`raw_notice_id`) REFERENCES `raw_notice`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `notice_extraction` ADD CONSTRAINT `notice_extraction_project_notice_id_fkey` FOREIGN KEY (`project_notice_id`) REFERENCES `project_notice`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_requirement` ADD CONSTRAINT `project_requirement_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_requirement` ADD CONSTRAINT `project_requirement_notice_id_fkey` FOREIGN KEY (`notice_id`) REFERENCES `project_notice`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `recommendation_result` ADD CONSTRAINT `recommendation_result_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `recommendation_result` ADD CONSTRAINT `recommendation_result_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `user_feedback` ADD CONSTRAINT `user_feedback_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `user_feedback` ADD CONSTRAINT `user_feedback_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `competition_analysis` ADD CONSTRAINT `competition_analysis_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `competition_analysis` ADD CONSTRAINT `competition_analysis_target_company_id_fkey` FOREIGN KEY (`target_company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `competition_analysis` ADD CONSTRAINT `competition_analysis_competitor_company_id_fkey` FOREIGN KEY (`competitor_company_id`) REFERENCES `company`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `win_probability_analysis` ADD CONSTRAINT `win_probability_analysis_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `win_probability_analysis` ADD CONSTRAINT `win_probability_analysis_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `company_qualification` ADD CONSTRAINT `company_qualification_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `company_personnel` ADD CONSTRAINT `company_personnel_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `contract` ADD CONSTRAINT `contract_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `contract` ADD CONSTRAINT `contract_notice_id_fkey` FOREIGN KEY (`notice_id`) REFERENCES `project_notice`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `contract` ADD CONSTRAINT `contract_seller_company_id_fkey` FOREIGN KEY (`seller_company_id`) REFERENCES `company`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `report` ADD CONSTRAINT `report_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `report` ADD CONSTRAINT `report_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- FullTextIndex
ALTER TABLE `company` ADD FULLTEXT INDEX `company_company_name_ft`(`company_name`) WITH PARSER ngram;

-- FullTextIndex
ALTER TABLE `project` ADD FULLTEXT INDEX `project_project_name_ft`(`project_name`) WITH PARSER ngram;

-- FullTextIndex
ALTER TABLE `project_notice` ADD FULLTEXT INDEX `project_notice_title_ft`(`title`) WITH PARSER ngram;

-- FullTextIndex
ALTER TABLE `project_notice` ADD FULLTEXT INDEX `project_notice_content_ft`(`content`) WITH PARSER ngram;

-- FullTextIndex
ALTER TABLE `raw_notice` ADD FULLTEXT INDEX `raw_notice_title_ft`(`title`) WITH PARSER ngram;
