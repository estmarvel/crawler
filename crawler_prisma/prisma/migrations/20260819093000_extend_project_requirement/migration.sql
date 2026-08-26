ALTER TABLE `project_requirement`
  ADD COLUMN `requirement_subtype` VARCHAR(64) NOT NULL DEFAULT 'OTHER' AFTER `requirement_type`,
  ADD COLUMN `structured_data` JSON NULL AFTER `keywords`,
  ADD COLUMN `verification_status` VARCHAR(32) NOT NULL DEFAULT 'UNVERIFIED' AFTER `is_mandatory`,
  ADD COLUMN `effective_status` VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' AFTER `verification_status`,
  ADD COLUMN `content_hash` CHAR(64) NULL AFTER `effective_status`,
  ADD COLUMN `updated_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) AFTER `created_at`,
  ADD UNIQUE KEY `project_requirement_notice_hash_key` (`project_id`, `notice_id`, `content_hash`),
  ADD KEY `project_requirement_active_idx` (`project_id`, `effective_status`, `requirement_type`),
  ADD KEY `project_requirement_subtype_idx` (`requirement_subtype`);
