package com.agent.controller;

import com.agent.mapper.AgentConfigMapper;
import com.agent.mapper.AgentMemoryMapper;
import com.agent.mapper.AgentSessionMapper;
import com.agent.model.AgentConfigDO;
import com.agent.model.AgentMemoryDO;
import com.agent.model.AgentSessionDO;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Agent 数据服务 REST API
 * <p>
 * 提供会话（Session）、记忆（Memory）、配置（Config）三大领域的 CRUD 接口。
 * 供前端页面查询历史会话，以及 Python 引擎通过 HTTP 回查持久化数据。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Slf4j
@RestController
@RequestMapping("/api/v1/agent")
public class AgentDataController {

    @Autowired
    private AgentSessionMapper sessionMapper;

    @Autowired
    private AgentMemoryMapper memoryMapper;

    @Autowired
    private AgentConfigMapper configMapper;

    // ==================== 会话 Session 接口 ====================

    /**
     * 查询用户的会话列表（分页）
     *
     * @param userId   用户标识
     * @param current  当前页码（默认1）
     * @param pageSize 每页条数（默认20）
     * @return 分页会话列表
     */
    @GetMapping("/sessions")
    public ResponseEntity<Map<String, Object>> listSessions(
            @RequestParam String userId,
            @RequestParam(defaultValue = "1") Integer current,
            @RequestParam(defaultValue = "20") Integer pageSize) {

        LambdaQueryWrapper<AgentSessionDO> wrapper = new LambdaQueryWrapper<AgentSessionDO>()
                .eq(AgentSessionDO::getUserId, userId)
                .orderByDesc(AgentSessionDO::getGmtCreate);

        IPage<AgentSessionDO> page = sessionMapper.selectPage(new Page<>(current, pageSize), wrapper);

        Map<String, Object> result = buildPageResult(page);
        return ResponseEntity.ok(result);
    }

    /**
     * 根据 sessionId 查询单个会话详情
     *
     * @param sessionId 会话唯一标识
     * @return 会话详情
     */
    @GetMapping("/sessions/{sessionId}")
    public ResponseEntity<AgentSessionDO> getSession(@PathVariable String sessionId) {
        LambdaQueryWrapper<AgentSessionDO> wrapper = new LambdaQueryWrapper<AgentSessionDO>()
                .eq(AgentSessionDO::getSessionId, sessionId);
        AgentSessionDO session = sessionMapper.selectOne(wrapper);
        if (session == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(session);
    }

    /**
     * 创建新会话
     *
     * @param session 会话信息
     * @return 创建结果
     */
    @PostMapping("/sessions")
    public ResponseEntity<AgentSessionDO> createSession(@RequestBody AgentSessionDO session) {
        session.setDeleted(0);
        session.setGmtCreate(LocalDateTime.now());
        session.setGmtModified(LocalDateTime.now());
        sessionMapper.insert(session);
        log.info("[AgentData] 创建会话: sessionId={}, userId={}", session.getSessionId(), session.getUserId());
        return ResponseEntity.ok(session);
    }

    // ==================== 记忆 Memory 接口 ====================

    /**
     * 查询某会话的记忆列表
     *
     * @param sessionId 会话 ID
     * @param limit     返回条数（默认50）
     * @return 记忆列表（按轮次排序）
     */
    @GetMapping("/memories")
    public ResponseEntity<List<AgentMemoryDO>> listMemories(
            @RequestParam String sessionId,
            @RequestParam(defaultValue = "50") Integer limit) {

        LambdaQueryWrapper<AgentMemoryDO> wrapper = new LambdaQueryWrapper<AgentMemoryDO>()
                .eq(AgentMemoryDO::getSessionId, sessionId)
                .orderByAsc(AgentMemoryDO::getRoundIndex)
                .last("LIMIT " + limit);

        List<AgentMemoryDO> memories = memoryMapper.selectList(wrapper);
        return ResponseEntity.ok(memories);
    }

    /**
     * 查询用户的长期记忆（跨会话）
     *
     * @param userId     用户标识
     * @param memoryType 记忆类型（可选，如 LONG_TERM）
     * @param limit      返回条数（默认20）
     * @return 记忆列表
     */
    @GetMapping("/memories/user")
    public ResponseEntity<List<AgentMemoryDO>> listUserMemories(
            @RequestParam String userId,
            @RequestParam(required = false) String memoryType,
            @RequestParam(defaultValue = "20") Integer limit) {

        LambdaQueryWrapper<AgentMemoryDO> wrapper = new LambdaQueryWrapper<AgentMemoryDO>()
                .eq(AgentMemoryDO::getUserId, userId)
                .eq(memoryType != null, AgentMemoryDO::getMemoryType, memoryType)
                .orderByDesc(AgentMemoryDO::getGmtCreate)
                .last("LIMIT " + limit);

        List<AgentMemoryDO> memories = memoryMapper.selectList(wrapper);
        return ResponseEntity.ok(memories);
    }

    /**
     * 写入记忆（供 Python 引擎直接调用的同步写入接口）
     *
     * @param memory 记忆数据
     * @return 写入结果
     */
    @PostMapping("/memories")
    public ResponseEntity<AgentMemoryDO> createMemory(@RequestBody AgentMemoryDO memory) {
        memory.setDeleted(0);
        memory.setGmtCreate(LocalDateTime.now());
        memory.setGmtModified(LocalDateTime.now());
        memoryMapper.insert(memory);
        log.info("[AgentData] 写入记忆: sessionId={}, role={}", memory.getSessionId(), memory.getRole());
        return ResponseEntity.ok(memory);
    }

    // ==================== 配置 Config 接口 ====================

    /**
     * 查询 Agent 配置列表
     *
     * @param agentId     Agent ID（可选，为空查全局配置）
     * @param configGroup 配置分组（可选）
     * @return 配置列表
     */
    @GetMapping("/configs")
    public ResponseEntity<List<AgentConfigDO>> listConfigs(
            @RequestParam(required = false) String agentId,
            @RequestParam(required = false) String configGroup) {

        LambdaQueryWrapper<AgentConfigDO> wrapper = new LambdaQueryWrapper<AgentConfigDO>()
                .eq(agentId != null, AgentConfigDO::getAgentId, agentId)
                .eq(configGroup != null, AgentConfigDO::getConfigGroup, configGroup)
                .orderByAsc(AgentConfigDO::getConfigGroup)
                .orderByAsc(AgentConfigDO::getConfigKey);

        List<AgentConfigDO> configs = configMapper.selectList(wrapper);
        return ResponseEntity.ok(configs);
    }

    /**
     * 查询单个配置项
     *
     * @param agentId   Agent ID
     * @param configKey 配置键
     * @return 配置值
     */
    @GetMapping("/configs/{agentId}/{configKey}")
    public ResponseEntity<AgentConfigDO> getConfig(
            @PathVariable String agentId,
            @PathVariable String configKey) {

        LambdaQueryWrapper<AgentConfigDO> wrapper = new LambdaQueryWrapper<AgentConfigDO>()
                .eq(AgentConfigDO::getAgentId, agentId)
                .eq(AgentConfigDO::getConfigKey, configKey);

        AgentConfigDO config = configMapper.selectOne(wrapper);
        if (config == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(config);
    }

    /**
     * 创建或更新配置项（UPSERT 语义）
     *
     * @param config 配置数据
     * @return 保存结果
     */
    @PostMapping("/configs")
    public ResponseEntity<AgentConfigDO> saveConfig(@RequestBody AgentConfigDO config) {
        // 尝试查找已存在的配置
        LambdaQueryWrapper<AgentConfigDO> wrapper = new LambdaQueryWrapper<AgentConfigDO>()
                .eq(AgentConfigDO::getAgentId, config.getAgentId())
                .eq(AgentConfigDO::getConfigKey, config.getConfigKey());

        AgentConfigDO existing = configMapper.selectOne(wrapper);
        if (existing != null) {
            // 更新
            existing.setConfigValue(config.getConfigValue());
            existing.setValueType(config.getValueType());
            existing.setDescription(config.getDescription());
            existing.setGmtModified(LocalDateTime.now());
            configMapper.updateById(existing);
            log.info("[AgentData] 更新配置: agentId={}, key={}", config.getAgentId(), config.getConfigKey());
            return ResponseEntity.ok(existing);
        } else {
            // 新建
            config.setDeleted(0);
            config.setGmtCreate(LocalDateTime.now());
            config.setGmtModified(LocalDateTime.now());
            configMapper.insert(config);
            log.info("[AgentData] 创建配置: agentId={}, key={}", config.getAgentId(), config.getConfigKey());
            return ResponseEntity.ok(config);
        }
    }

    // ==================== 工具方法 ====================

    /**
     * 构建分页结果
     */
    private Map<String, Object> buildPageResult(IPage<?> page) {
        Map<String, Object> result = new HashMap<>(4);
        result.put("records", page.getRecords());
        result.put("total", page.getTotal());
        result.put("current", page.getCurrent());
        result.put("pages", page.getPages());
        return result;
    }
}
