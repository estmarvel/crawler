-- CreateTable
CREATE TABLE `company` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `company_name` VARCHAR(191) NOT NULL,
    `credit_code` VARCHAR(191) NULL,
    `province` VARCHAR(191) NULL,
    `city` VARCHAR(191) NULL,
    `company_type` VARCHAR(191) NULL,
    `qualification_level` VARCHAR(191) NULL,
    `business_scope` VARCHAR(191) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `company_credit_code_key`(`credit_code`),
    INDEX `company_company_name_idx`(`company_name`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `company_alias` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `company_id` INTEGER NOT NULL,
    `alias_name` VARCHAR(191) NOT NULL,
    `source` VARCHAR(191) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `company_alias_company_id_idx`(`company_id`),
    INDEX `company_alias_alias_name_idx`(`alias_name`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `project` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `project_code` VARCHAR(191) NULL,
    `project_name` VARCHAR(191) NOT NULL,
    `project_nature` VARCHAR(191) NULL,
    `industry` VARCHAR(191) NULL,
    `project_type` VARCHAR(191) NULL,
    `tender_method` VARCHAR(191) NULL,
    `organization_form` VARCHAR(191) NULL,
    `province` VARCHAR(191) NULL,
    `city` VARCHAR(191) NULL,
    `location_text` VARCHAR(191) NULL,
    `owner_company_id` INTEGER NULL,
    `owner_company_name` VARCHAR(191) NULL,
    `agency_company_name` VARCHAR(191) NULL,
    `estimated_amount` DECIMAL(18, 2) NULL,
    `tender_amount` DECIMAL(18, 2) NULL,
    `fund_source` VARCHAR(191) NULL,
    `bid_open_time` DATETIME(3) NULL,
    `duration` VARCHAR(191) NULL,
    `quality_requirement` VARCHAR(191) NULL,
    `supervisor_department` VARCHAR(191) NULL,
    `current_status` VARCHAR(191) NOT NULL DEFAULT 'TENDER',
    `first_publish_date` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `project_project_code_idx`(`project_code`),
    INDEX `project_project_name_idx`(`project_name`),
    INDEX `project_current_status_province_city_industry_idx`(`current_status`, `province`, `city`, `industry`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `project_notice` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `project_id` INTEGER NOT NULL,
    `notice_type` VARCHAR(191) NOT NULL,
    `title` VARCHAR(191) NOT NULL,
    `content` VARCHAR(191) NULL,
    `structured_data` JSON NULL,
    `publish_date` DATETIME(3) NULL,
    `source_site` VARCHAR(191) NULL,
    `source_url` VARCHAR(191) NULL,
    `source_notice_id` VARCHAR(191) NULL,
    `crawl_time` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `project_notice_project_id_idx`(`project_id`),
    INDEX `project_notice_notice_type_idx`(`notice_type`),
    INDEX `project_notice_publish_date_idx`(`publish_date`),
    INDEX `project_notice_source_notice_id_idx`(`source_notice_id`),
    INDEX `project_notice_notice_type_publish_date_idx`(`notice_type`, `publish_date`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `project_notice_attachment` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `notice_id` INTEGER NOT NULL,
    `file_name` VARCHAR(191) NOT NULL,
    `file_url` VARCHAR(191) NULL,
    `file_type` VARCHAR(191) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `project_notice_attachment_notice_id_idx`(`notice_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `project_company_relation` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `project_id` INTEGER NOT NULL,
    `notice_id` INTEGER NULL,
    `company_id` INTEGER NULL,
    `company_name` VARCHAR(191) NOT NULL,
    `relation_type` VARCHAR(191) NOT NULL,
    `stage_type` VARCHAR(191) NULL,
    `ranking` INTEGER NULL,
    `bid_amount` DECIMAL(18, 2) NULL,
    `is_winner` BOOLEAN NOT NULL DEFAULT false,
    `is_consortium` BOOLEAN NOT NULL DEFAULT false,
    `is_consortium_leader` BOOLEAN NOT NULL DEFAULT false,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `project_company_relation_project_id_idx`(`project_id`),
    INDEX `project_company_relation_company_id_idx`(`company_id`),
    INDEX `project_company_relation_notice_id_idx`(`notice_id`),
    INDEX `project_company_relation_project_id_relation_type_idx`(`project_id`, `relation_type`),
    INDEX `project_company_relation_company_id_relation_type_idx`(`company_id`, `relation_type`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `project_consortium_member` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `relation_id` INTEGER NOT NULL,
    `member_company_id` INTEGER NULL,
    `member_company_name` VARCHAR(191) NOT NULL,
    `member_role` VARCHAR(191) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `project_consortium_member_relation_id_idx`(`relation_id`),
    INDEX `project_consortium_member_member_company_id_member_role_idx`(`member_company_id`, `member_role`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `project_company_person` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `relation_id` INTEGER NOT NULL,
    `person_name` VARCHAR(191) NOT NULL,
    `person_role` VARCHAR(191) NULL,
    `certificate_name` VARCHAR(191) NULL,
    `certificate_no` VARCHAR(191) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `project_company_person_relation_id_idx`(`relation_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `company_profile_snapshot` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `company_id` INTEGER NOT NULL,
    `snapshot_date` DATETIME(3) NOT NULL,
    `win_project_count_3y` INTEGER NOT NULL DEFAULT 0,
    `main_industries` JSON NULL,
    `main_regions` JSON NULL,
    `avg_win_amount` DECIMAL(18, 2) NULL,
    `profile_json` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `company_profile_snapshot_company_id_snapshot_date_idx`(`company_id`, `snapshot_date`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- AddForeignKey
ALTER TABLE `company_alias` ADD CONSTRAINT `company_alias_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project` ADD CONSTRAINT `project_owner_company_id_fkey` FOREIGN KEY (`owner_company_id`) REFERENCES `company`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_notice` ADD CONSTRAINT `project_notice_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_notice_attachment` ADD CONSTRAINT `project_notice_attachment_notice_id_fkey` FOREIGN KEY (`notice_id`) REFERENCES `project_notice`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_company_relation` ADD CONSTRAINT `project_company_relation_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_company_relation` ADD CONSTRAINT `project_company_relation_notice_id_fkey` FOREIGN KEY (`notice_id`) REFERENCES `project_notice`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_company_relation` ADD CONSTRAINT `project_company_relation_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_consortium_member` ADD CONSTRAINT `project_consortium_member_relation_id_fkey` FOREIGN KEY (`relation_id`) REFERENCES `project_company_relation`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `project_company_person` ADD CONSTRAINT `project_company_person_relation_id_fkey` FOREIGN KEY (`relation_id`) REFERENCES `project_company_relation`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `company_profile_snapshot` ADD CONSTRAINT `company_profile_snapshot_company_id_fkey` FOREIGN KEY (`company_id`) REFERENCES `company`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

