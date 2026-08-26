-- ============================================================
-- Agent 数据服务建表 SQL
-- 数据库：agent_db
-- @author lxy
-- @date 2026-08-26
--
-- 说明：Python Agent 引擎产生的会话/记忆数据由 Java 侧持久化到 MySQL。
-- 表设计遵循规范：审计字段（creator_emp_no/modifier_emp_no）、
-- 逻辑删除（deleted）、时间戳（gmt_create/gmt_modified）。
-- ============================================================

CREATE DATABASE IF NOT EXISTS agent_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE agent_db;

-- -----------------------------------------------------------
-- 表：agent_session — Agent 会话记录
-- 存储用户与 Agent 之间的会话元数据
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_session` (
    `id`              BIGINT        NOT NULL COMMENT '主键ID（雪花算法）',
    `session_id`      VARCHAR(64)   NOT NULL COMMENT '会话唯一标识（UUID，Python引擎生成）',
    `user_id`         VARCHAR(64)   NOT NULL COMMENT '用户标识',
    `agent_id`        VARCHAR(64)   DEFAULT NULL COMMENT '关联Agent ID',
    `title`           VARCHAR(256)  DEFAULT NULL COMMENT '会话标题（首轮问题摘要）',
    `status`          VARCHAR(32)   NOT NULL DEFAULT 'ACTIVE' COMMENT '会话状态：ACTIVE/ARCHIVED/DELETED',
    `round_count`     INT           NOT NULL DEFAULT 0 COMMENT '会话轮次计数',
    `metadata`        TEXT          DEFAULT NULL COMMENT '扩展元数据（JSON）',
    `creator_emp_no`  VARCHAR(32)   DEFAULT NULL COMMENT '创建人工号',
    `modifier_emp_no` VARCHAR(32)   DEFAULT NULL COMMENT '修改人工号',
    `deleted`         TINYINT       NOT NULL DEFAULT 0 COMMENT '逻辑删除：0-未删除，1-已删除',
    `gmt_create`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_session_id` (`session_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_agent_id` (`agent_id`),
    KEY `idx_gmt_create` (`gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent会话记录表';

-- -----------------------------------------------------------
-- 表：agent_memory — Agent 记忆存储
-- 存储会话中的对话记忆（短期/长期/摘要）
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_memory` (
    `id`              BIGINT        NOT NULL COMMENT '主键ID（雪花算法）',
    `session_id`      VARCHAR(64)   NOT NULL COMMENT '关联会话ID',
    `user_id`         VARCHAR(64)   NOT NULL COMMENT '用户标识',
    `agent_id`        VARCHAR(64)   DEFAULT NULL COMMENT '关联Agent ID',
    `memory_type`     VARCHAR(32)   NOT NULL DEFAULT 'SHORT_TERM' COMMENT '记忆类型：SHORT_TERM/LONG_TERM/SUMMARY',
    `role`            VARCHAR(16)   NOT NULL COMMENT '角色：user/assistant/system',
    `content`         MEDIUMTEXT    NOT NULL COMMENT '记忆内容（对话原文或摘要）',
    `score`           DOUBLE        DEFAULT 0.0 COMMENT '记忆权重/重要性分数',
    `round_index`     INT           NOT NULL DEFAULT 0 COMMENT '轮次序号',
    `token_count`     INT           DEFAULT 0 COMMENT 'Token数量（截断计算用）',
    `metadata`        TEXT          DEFAULT NULL COMMENT '扩展元数据（JSON）',
    `creator_emp_no`  VARCHAR(32)   DEFAULT NULL COMMENT '创建人工号',
    `modifier_emp_no` VARCHAR(32)   DEFAULT NULL COMMENT '修改人工号',
    `deleted`         TINYINT       NOT NULL DEFAULT 0 COMMENT '逻辑删除：0-未删除，1-已删除',
    `gmt_create`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    KEY `idx_session_id` (`session_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_memory_type` (`memory_type`),
    KEY `idx_gmt_create` (`gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent记忆存储表';

-- -----------------------------------------------------------
-- 表：agent_config — Agent 运行时配置
-- 存储 Agent 的动态配置项（类似 Diamond 配置中心）
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_config` (
    `id`              BIGINT        NOT NULL COMMENT '主键ID（雪花算法）',
    `agent_id`        VARCHAR(64)   DEFAULT NULL COMMENT '所属Agent ID（空=全局配置）',
    `config_key`      VARCHAR(128)  NOT NULL COMMENT '配置键',
    `config_value`    TEXT          NOT NULL COMMENT '配置值（JSON或文本）',
    `value_type`      VARCHAR(16)   NOT NULL DEFAULT 'STRING' COMMENT '值类型：STRING/JSON/NUMBER/BOOLEAN',
    `config_group`    VARCHAR(64)   DEFAULT NULL COMMENT '配置分组：memory/routing/model',
    `description`     VARCHAR(256)  DEFAULT NULL COMMENT '配置描述',
    `creator_emp_no`  VARCHAR(32)   DEFAULT NULL COMMENT '创建人工号',
    `modifier_emp_no` VARCHAR(32)   DEFAULT NULL COMMENT '修改人工号',
    `deleted`         TINYINT       NOT NULL DEFAULT 0 COMMENT '逻辑删除：0-未删除，1-已删除',
    `gmt_create`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_agent_config_key` (`agent_id`, `config_key`),
    KEY `idx_config_group` (`config_group`),
    KEY `idx_gmt_create` (`gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent运行时配置表';
