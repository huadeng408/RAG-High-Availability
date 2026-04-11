CREATE TABLE users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(255) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    role ENUM('USER', 'ADMIN') NOT NULL DEFAULT 'USER',
    org_tags VARCHAR(255) DEFAULT NULL,
    primary_org VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE organization_tags (
    tag_id VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    parent_tag VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_tag) REFERENCES organization_tags(tag_id) ON DELETE SET NULL,
    FOREIGN KEY (created_by) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE file_upload (
    id BIGINT NOT NULL AUTO_INCREMENT,
    file_md5 VARCHAR(32) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    total_size BIGINT NOT NULL,
    status TINYINT NOT NULL DEFAULT 0,
    user_id VARCHAR(64) NOT NULL,
    org_tag VARCHAR(50) DEFAULT NULL,
    is_public TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    merged_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_md5_user (file_md5, user_id),
    INDEX idx_user (user_id),
    INDEX idx_org_tag (org_tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE chunk_info (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_md5 VARCHAR(32) NOT NULL,
    chunk_index INT NOT NULL,
    chunk_md5 VARCHAR(32) NOT NULL,
    storage_path VARCHAR(255) NOT NULL,
    UNIQUE KEY uk_chunk_file_index (file_md5, chunk_index),
    INDEX idx_chunk_file_md5 (file_md5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE pipeline_task (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_md5 VARCHAR(32) NOT NULL,
    stage VARCHAR(20) NOT NULL,
    chunk_id INT NOT NULL DEFAULT -1,
    status VARCHAR(20) NOT NULL,
    retry_count INT NOT NULL DEFAULT 0,
    last_error TEXT,
    idempotency_key VARCHAR(96) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_pipeline_idempotency_key (idempotency_key),
    UNIQUE KEY uk_pipeline_file_stage_chunk (file_md5, stage, chunk_id),
    INDEX idx_pipeline_status (status),
    INDEX idx_pipeline_file_md5 (file_md5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE document_vectors (
    vector_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_md5 VARCHAR(32) NOT NULL,
    chunk_id INT NOT NULL,
    text_content TEXT,
    model_version VARCHAR(128),
    user_id VARCHAR(64) NOT NULL,
    org_tag VARCHAR(50),
    is_public TINYINT(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE working_memory_snapshots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    conversation_id VARCHAR(64) NOT NULL,
    summary TEXT NOT NULL,
    facts_json TEXT,
    entities_json TEXT,
    message_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_working_memory_user_conv (user_id, conversation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE user_profile_slots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    slot_key VARCHAR(64) NOT NULL,
    slot_value VARCHAR(255) NOT NULL,
    confidence DOUBLE NOT NULL DEFAULT 0,
    source VARCHAR(128) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_profile_slot (user_id, slot_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE long_term_memories (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    memory_id VARCHAR(96) NOT NULL,
    user_id BIGINT NOT NULL,
    conversation_id VARCHAR(64) NOT NULL,
    memory_type VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    entities_json TEXT,
    importance DOUBLE NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_long_term_memory_id (memory_id),
    INDEX idx_long_term_memory_user (user_id),
    INDEX idx_long_term_memory_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO users (username, password, role)
VALUES ('admin', '$2a$10$CuNbcCAjuZPTu/VnBT/kgeU4Pu.bcEo23GJxvugZt/3yTQ8iIF4hC', 'ADMIN');

INSERT INTO users (username, password, role)
VALUES ('testuser', '$2a$10$zUiAOXogIuHnNyR7vf8Q3usknDJcvmbc.36Kl2iC0gdAWyrecoGZa', 'USER');

INSERT INTO organization_tags (tag_id, name, description, parent_tag, created_by)
SELECT 'PRIVATE_admin', 'admin private space', 'private org tag for admin', NULL, id
FROM users WHERE username = 'admin';

INSERT INTO organization_tags (tag_id, name, description, parent_tag, created_by)
SELECT 'PRIVATE_testuser', 'testuser private space', 'private org tag for testuser', NULL, id
FROM users WHERE username = 'testuser';

UPDATE users SET org_tags = 'PRIVATE_admin', primary_org = 'PRIVATE_admin' WHERE username = 'admin';
UPDATE users SET org_tags = 'PRIVATE_testuser', primary_org = 'PRIVATE_testuser' WHERE username = 'testuser';
