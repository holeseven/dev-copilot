package com.agent.mapper;

import com.agent.model.AgentConfigDO;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/**
 * 配置数据 Mapper
 * <p>
 * 继承 MyBatis-Plus BaseMapper，提供 agent_config 表的标准 CRUD 能力。
 * 支持按 agent_id + config_group 维度的配置查询。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Mapper
public interface AgentConfigMapper extends BaseMapper<AgentConfigDO> {
}
