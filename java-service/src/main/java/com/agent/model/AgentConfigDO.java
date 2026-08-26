package com.agent.model;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * Agent 配置 DO
 * <p>
 * 对应表 agent_config，存储 Agent 运行时配置项。
 * 支持动态配置热更新（类似 Diamond 配置中心），
 * Python 引擎通过 REST API 或 Redis Pub/Sub 感知配置变更。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Data
@TableName("agent_config")
public class AgentConfigDO {

    /** 主键 ID */
    @TableId(type = IdType.ASSIGN_ID)
    private Long id;

    /** 配置所属 Agent ID（为空表示全局配置） */
    private String agentId;

    /** 配置键 */
    private String configKey;

    /** 配置值（JSON 字符串或普通文本） */
    private String configValue;

    /** 配置类型：STRING / JSON / NUMBER / BOOLEAN */
    private String valueType;

    /** 配置分组（如：memory / routing / model） */
    private String configGroup;

    /** 配置描述 */
    private String description;

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
