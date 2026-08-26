package com.agent.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.serializer.StringRedisSerializer;

/**
 * Redis 连接配置
 * <p>
 * 配置 Redis 连接工厂和序列化方式。
 * 主要用途：
 * 1. 消费 Redis Stream（Python 引擎写入的记忆/会话事件）
 * 2. 作为数据中转管道（Python ↔ Java 跨语言通信）
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@Configuration
public class RedisConfig {

    /**
     * 配置 StringRedisTemplate
     * <p>
     * 使用 String 序列化方式，与 Python 引擎写入的数据格式兼容。
     * Python 的 redis-py 默认以 UTF-8 字符串方式写入 Stream，
     * Java 侧使用 StringRedisTemplate 确保序列化/反序列化一致性。
     * </p>
     *
     * @param connectionFactory Redis 连接工厂（由 Spring Boot 自动配置）
     * @return StringRedisTemplate 实例
     */
    @Bean
    public StringRedisTemplate stringRedisTemplate(RedisConnectionFactory connectionFactory) {
        StringRedisTemplate template = new StringRedisTemplate();
        template.setConnectionFactory(connectionFactory);
        template.setKeySerializer(new StringRedisSerializer());
        template.setValueSerializer(new StringRedisSerializer());
        template.setHashKeySerializer(new StringRedisSerializer());
        template.setHashValueSerializer(new StringRedisSerializer());
        template.afterPropertiesSet();
        return template;
    }
}
