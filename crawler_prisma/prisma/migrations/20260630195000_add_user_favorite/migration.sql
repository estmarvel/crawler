-- CreateTable
CREATE TABLE `user_favorite` (
    `id` INTEGER NOT NULL AUTO_INCREMENT,
    `user_id` INTEGER NOT NULL,
    `project_id` INTEGER NOT NULL,
    `status` ENUM('pending', 'following', 'bid', 'abandoned') NOT NULL DEFAULT 'pending',
    `note` TEXT NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `user_favorite_user_id_idx`(`user_id`),
    INDEX `user_favorite_project_id_idx`(`project_id`),
    INDEX `user_favorite_status_idx`(`status`),
    INDEX `user_favorite_created_at_idx`(`created_at`),
    UNIQUE INDEX `user_favorite_user_id_project_id_key`(`user_id`, `project_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- AddForeignKey
ALTER TABLE `user_favorite` ADD CONSTRAINT `user_favorite_project_id_fkey` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;
