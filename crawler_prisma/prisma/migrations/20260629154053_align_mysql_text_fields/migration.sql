-- AlterTable
ALTER TABLE `company` MODIFY `business_scope` TEXT NULL;

-- AlterTable
ALTER TABLE `project` MODIFY `location_text` TEXT NULL,
    MODIFY `quality_requirement` TEXT NULL;

-- AlterTable
ALTER TABLE `project_notice` MODIFY `title` VARCHAR(512) NOT NULL,
    MODIFY `content` LONGTEXT NULL,
    MODIFY `source_url` VARCHAR(1024) NULL;

-- AlterTable
ALTER TABLE `project_notice_attachment` MODIFY `file_url` VARCHAR(1024) NULL;
