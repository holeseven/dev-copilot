package com.agent.model;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 会话记录 DO
 * <p>
 * 对应表 agent_session，存储 Agent 与用户之间的会话元数据。
 * Python 引擎创建会话后通过 Redis Stream 推送，由 Java 侧持久化。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Data
@TableName("agent_session")
public class AgentSessionDO {

    /** 主键 ID */
    @TableId(type = IdType.ASSIGN_ID)
    private Long id;

    /** 会话唯一标识（UUID，由 Python 引擎生成） */
    private String sessionId;

    /** 用户标识 */
    private String userId;

    /** 关联的 Agent ID */
    private String agentId;

    /** 会话标题（首轮问题摘要） */
    private String title;

    /** 会话状态：ACTIVE / ARCHIVED / DELETED */
    private String status;

    /** 会话轮次计数 */
    private Integer roundCount;

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
