package com.agent;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Agent 数据服务启动类
 * <p>
 * 定位：Java 数据持久化服务，消费 Python Agent 引擎产生的会话/记忆数据，
 * 通过 Redis Stream 接收事件并批量持久化到 MySQL。
 * 同时提供 REST API 供前端和 Python 引擎查询历史数据。
 * </p>
 *
 * @author lxy
 * @date 2026-08-26
 */
@SpringBootApplication
@MapperScan("com.agent.mapper")
@EnableScheduling
public class AgentApplication {

    public static void main(String[] args) {
        SpringApplication.run(AgentApplication.class, args);
    }
}
