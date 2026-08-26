package com.agent.model;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * Agent 记忆 DO
 * <p>
 * 对应表 agent_memory，存储 Agent 的长期记忆条目。
 * Python 引擎将记忆写入 Redis ZSET 后，通过 Redis Stream 通知 Java 侧落库。
 * 支持按 session_id 或 user_id 维度检索历史记忆。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Data
@TableName("agent_memory")
public class AgentMemoryDO {

    /** 主键 ID */
    @TableId(type = IdType.ASSIGN_ID)
    private Long id;

    /** 关联会话 ID */
    private String sessionId;

    /** 用户标识 */
    private String userId;

    /** 关联的 Agent ID */
    private String agentId;

    /** 记忆类型：SHORT_TERM（短期）/ LONG_TERM（长期）/ SUMMARY（摘要） */
    private String memoryType;

    /** 角色：user / assistant / system */
    private String role;

    /** 记忆内容（对话原文或摘要） */
    private String content;

    /** 记忆权重/重要性分数（用于排序和截断） */
    private Double score;

    /** 轮次序号（在会话中的第几轮） */
    private Integer roundIndex;

    /** Token 数量（用于截断计算） */
    private Integer tokenCount;

    /** 扩展元数据（JSON 格式） */
    private String metadata;

    /** 创建人工号 */
    private String creatorEmpNo;

    /** 修改人工号 */
    private String modifierEmpNo;

    /** 逻辑删除标记：0-未删除，1-已删除 */
    @TableLogic
    private Integer deleted;

    /** 创建时间 */
    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime gmtCreate;

    /** 修改时间 */
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime gmtModified;
}
