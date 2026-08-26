package com.agent.service;

import com.agent.mapper.AgentMemoryMapper;
import com.agent.mapper.AgentSessionMapper;
import com.agent.model.AgentMemoryDO;
import com.agent.model.AgentSessionDO;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.connection.stream.*;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 记忆持久化服务
 * <p>
 * 核心职责：消费 Redis Stream 中 Python Agent 引擎产出的记忆/会话事件，
 * 批量持久化到 MySQL。实现 Python(产生数据) → Redis Stream(消息管道) → Java(持久化) 的双栈协作链路。
 * </p>
 * <p>
 * 消费模式：Consumer Group + 定时轮询拉取，保证消息不丢失、支持多实例并行消费。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Slf4j
@Service
public class MemoryPersistenceService extends ServiceImpl<AgentMemoryMapper, AgentMemoryDO> {

    /** Redis Stream key：记忆事件流 */
    private static final String MEMORY_STREAM_KEY = "agent:stream:memory";
    /** Redis Stream key：会话事件流 */
    private static final String SESSION_STREAM_KEY = "agent:stream:session";
    /** Consumer Group 名称 */
    private static final String CONSUMER_GROUP = "java-persistence-group";
    /** Consumer 名称（可配合实例 ID 做区分） */
    private static final String CONSUMER_NAME = "persistence-worker-1";
    /** 单次拉取最大消息数 */
    private static final int BATCH_SIZE = 50;

    @Autowired
    private StringRedisTemplate redisTemplate;

    @Autowired
    private AgentMemoryMapper memoryMapper;

    @Autowired
    private AgentSessionMapper sessionMapper;

    /**
     * 初始化 Consumer Group（幂等创建）
     * 应用启动时自动执行
     */
    @jakarta.annotation.PostConstruct
    public void initConsumerGroups() {
        try {
            createGroupIfNotExists(MEMORY_STREAM_KEY, CONSUMER_GROUP);
            createGroupIfNotExists(SESSION_STREAM_KEY, CONSUMER_GROUP);
            log.info("[MemoryPersistence] Consumer Groups 初始化完成");
        } catch (Exception e) {
            log.warn("[MemoryPersistence] Consumer Group 初始化异常（Stream 可能尚未创建）: {}", e.getMessage());
        }
    }

    /**
     * 定时消费记忆事件流（每 5 秒拉取一批）
     * <p>
     * 从 Redis Stream 拉取未确认的记忆消息，解析后批量写入 MySQL，成功后 ACK。
     * </p>
     */
    @Scheduled(fixedDelay = 5000)
    @Transactional(rollbackFor = Exception.class)
    public void consumeMemoryStream() {
        try {
            List<MapRecord<String, Object, Object>> records = readFromStream(MEMORY_STREAM_KEY);
            if (records == null || records.isEmpty()) {
                return;
            }

            List<AgentMemoryDO> batchList = new ArrayList<>();
            List<RecordId> ackIds = new ArrayList<>();

            for (MapRecord<String, Object, Object> record : records) {
                try {
                    AgentMemoryDO memoryDO = parseMemoryRecord(record.getValue());
                    batchList.add(memoryDO);
                    ackIds.add(record.getId());
                } catch (Exception e) {
                    log.error("[MemoryPersistence] 解析记忆消息失败, recordId={}", record.getId(), e);
                    // 解析失败也 ACK，避免阻塞消费（记录错误日志后人工排查）
                    ackIds.add(record.getId());
                }
            }

            // 批量写入 MySQL（MyBatis-Plus saveBatch）
            if (!batchList.isEmpty()) {
                this.saveBatch(batchList, BATCH_SIZE);
                log.info("[MemoryPersistence] 批量写入记忆 {} 条", batchList.size());
            }

            // 批量 ACK
            acknowledgeMessages(MEMORY_STREAM_KEY, ackIds);
        } catch (Exception e) {
            log.error("[MemoryPersistence] 消费记忆流异常", e);
        }
    }

    /**
     * 定时消费会话事件流（每 5 秒拉取一批）
     */
    @Scheduled(fixedDelay = 5000)
    @Transactional(rollbackFor = Exception.class)
    public void consumeSessionStream() {
        try {
            List<MapRecord<String, Object, Object>> records = readFromStream(SESSION_STREAM_KEY);
            if (records == null || records.isEmpty()) {
                return;
            }

            List<AgentSessionDO> batchList = new ArrayList<>();
            List<RecordId> ackIds = new ArrayList<>();

            for (MapRecord<String, Object, Object> record : records) {
                try {
                    AgentSessionDO sessionDO = parseSessionRecord(record.getValue());
                    batchList.add(sessionDO);
                    ackIds.add(record.getId());
                } catch (Exception e) {
                    log.error("[MemoryPersistence] 解析会话消息失败, recordId={}", record.getId(), e);
                    ackIds.add(record.getId());
                }
            }

            if (!batchList.isEmpty()) {
                for (AgentSessionDO session : batchList) {
                    sessionMapper.insert(session);
                }
                log.info("[MemoryPersistence] 批量写入会话 {} 条", batchList.size());
            }

            acknowledgeMessages(SESSION_STREAM_KEY, ackIds);
        } catch (Exception e) {
            log.error("[MemoryPersistence] 消费会话流异常", e);
        }
    }

    // ==================== 私有方法 ====================

    /**
     * 从 Stream 中以 Consumer Group 模式读取消息
     */
    @SuppressWarnings("unchecked")
    private List<MapRecord<String, Object, Object>> readFromStream(String streamKey) {
        try {
            return redisTemplate.opsForStream().read(
                    Consumer.from(CONSUMER_GROUP, CONSUMER_NAME),
                    StreamReadOptions.empty().count(BATCH_SIZE).block(Duration.ofMillis(2000)),
                    StreamOffset.create(streamKey, ReadOffset.lastConsumed())
            );
        } catch (Exception e) {
            log.debug("[MemoryPersistence] 读取 Stream {} 无数据或异常: {}", streamKey, e.getMessage());
            return null;
        }
    }

    /**
     * 批量 ACK 已处理的消息
     */
    private void acknowledgeMessages(String streamKey, List<RecordId> recordIds) {
        if (recordIds.isEmpty()) {
            return;
        }
        redisTemplate.opsForStream().acknowledge(
                streamKey, CONSUMER_GROUP,
                recordIds.toArray(new RecordId[0])
        );
    }

    /**
     * 解析记忆消息为 DO 对象
     */
    private AgentMemoryDO parseMemoryRecord(Map<Object, Object> data) {
        AgentMemoryDO memory = new AgentMemoryDO();
        memory.setSessionId(getStringValue(data, "session_id"));
        memory.setUserId(getStringValue(data, "user_id"));
        memory.setAgentId(getStringValue(data, "agent_id"));
        memory.setMemoryType(getStringValue(data, "memory_type"));
        memory.setRole(getStringValue(data, "role"));
        memory.setContent(getStringValue(data, "content"));
        memory.setScore(data.containsKey("score") ? Double.parseDouble(data.get("score").toString()) : 0.0);
        memory.setRoundIndex(data.containsKey("round_index") ? Integer.parseInt(data.get("round_index").toString()) : 0);
        memory.setTokenCount(data.containsKey("token_count") ? Integer.parseInt(data.get("token_count").toString()) : 0);
        memory.setMetadata(getStringValue(data, "metadata"));
        memory.setDeleted(0);
        memory.setGmtCreate(LocalDateTime.now());
        memory.setGmtModified(LocalDateTime.now());
        return memory;
    }

    /**
     * 解析会话消息为 DO 对象
     */
    private AgentSessionDO parseSessionRecord(Map<Object, Object> data) {
        AgentSessionDO session = new AgentSessionDO();
        session.setSessionId(getStringValue(data, "session_id"));
        session.setUserId(getStringValue(data, "user_id"));
        session.setAgentId(getStringValue(data, "agent_id"));
        session.setTitle(getStringValue(data, "title"));
        session.setStatus(getStringValue(data, "status"));
        session.setRoundCount(data.containsKey("round_count") ? Integer.parseInt(data.get("round_count").toString()) : 0);
        session.setMetadata(getStringValue(data, "metadata"));
        session.setDeleted(0);
        session.setGmtCreate(LocalDateTime.now());
        session.setGmtModified(LocalDateTime.now());
        return session;
    }

    /**
     * 安全获取 Map 中的 String 值
     */
    private String getStringValue(Map<Object, Object> data, String key) {
        Object value = data.get(key);
        return value != null ? value.toString() : null;
    }

    /**
     * 幂等创建 Consumer Group
     */
    private void createGroupIfNotExists(String streamKey, String groupName) {
        try {
            redisTemplate.opsForStream().createGroup(streamKey, ReadOffset.from("0"), groupName);
        } catch (Exception e) {
            // BUSYGROUP: Consumer Group name already exists — 忽略
            if (!e.getMessage().contains("BUSYGROUP")) {
                log.warn("[MemoryPersistence] 创建 Consumer Group 失败: stream={}, group={}, error={}",
                        streamKey, groupName, e.getMessage());
            }
        }
    }
}
