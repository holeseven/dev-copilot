package com.agent.mapper;

import com.agent.model.AgentSessionDO;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/**
 * 会话数据 Mapper
 * <p>
 * 继承 MyBatis-Plus BaseMapper，提供 agent_session 表的标准 CRUD 能力。
 * 复杂查询可在此接口中扩展自定义 SQL 方法。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Mapper
public interface AgentSessionMapper extends BaseMapper<AgentSessionDO> {
}
