package com.agent.controller;

import com.agent.client.AgentChatRequest;
import com.agent.client.PythonAgentClient;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;

/**
 * 浏览器访问 Agent 的统一 Java API 入口。
 *
 * @author lxy
 */
@RestController
@RequestMapping("/api/v1/agent")
public class AgentChatController {

    private final PythonAgentClient pythonAgentClient;

    public AgentChatController(PythonAgentClient pythonAgentClient) {
        this.pythonAgentClient = pythonAgentClient;
    }

    @PostMapping(
            value = "/chat",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.TEXT_EVENT_STREAM_VALUE
    )
    public Flux<ServerSentEvent<String>> chat(@Valid @RequestBody AgentChatRequest request) {
        return pythonAgentClient.streamChat(request).switchOnFirst((first, stream) -> {
            if (first.isOnError()) {
                return Flux.error(first.getThrowable());
            }
            if (!first.hasValue()) {
                return stream;
            }
            return stream.onErrorResume(ignored -> Flux.just(
                    event("error", "{\"message\":\"Agent 流中断，请稍后重试\"}"),
                    event("done", "{\"status\":\"failed\"}")
            ));
        });
    }

    private ServerSentEvent<String> event(String name, String data) {
        return ServerSentEvent.<String>builder(data).event(name).build();
    }
}
