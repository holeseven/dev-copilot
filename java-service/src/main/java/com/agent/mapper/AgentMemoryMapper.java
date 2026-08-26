package com.agent.mapper;

import com.agent.model.AgentMemoryDO;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/**
 * 记忆数据 Mapper
 * <p>
 * 继承 MyBatis-Plus BaseMapper，提供 agent_memory 表的标准 CRUD 能力。
 * 支持按 session_id / user_id 维度的批量查询与写入。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Mapper
public interface AgentMemoryMapper extends BaseMapper<AgentMemoryDO> {
}
