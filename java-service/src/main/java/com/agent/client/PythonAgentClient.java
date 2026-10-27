package com.agent.client;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientRequestException;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/**
 * 调用 Python Agent 的 SSE 客户端。
 *
 * @author lxy
 */
@Component
public class PythonAgentClient {

    private static final ParameterizedTypeReference<ServerSentEvent<String>> SSE_TYPE =
            new ParameterizedTypeReference<>() {
            };

    private final WebClient webClient;

    @Autowired
    public PythonAgentClient(
            WebClient.Builder webClientBuilder,
            @Value("${dev-copilot.engine.base-url}") String engineBaseUrl
    ) {
        this(webClientBuilder.baseUrl(engineBaseUrl).build());
    }

    PythonAgentClient(WebClient webClient) {
        this.webClient = webClient;
    }

    /**
     * 将聊天请求转发给 Python Agent，并按收到顺序透传 SSE 事件。
     */
    public Flux<ServerSentEvent<String>> streamChat(AgentChatRequest request) {
        return stream("/chat", request);
    }

    /**
     * 将一次批准或拒绝转发给 Python Agent，并继续透传同一 SSE 事件协议。
     */
    public Flux<ServerSentEvent<String>> streamResume(AgentResumeRequest request) {
        return stream("/chat/resume", request);
    }

    /**
     * 查询同一 Workspace 会话当前由 checkpoint 保存的待审批项。
     */
    public Mono<AgentPendingApprovalResponse> pendingApprovals(
            AgentPendingApprovalRequest request
    ) {
        return webClient.post()
                .uri("/chat/pending")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .bodyValue(request)
                .exchangeToMono(response -> {
                    if (response.statusCode().is2xxSuccessful()) {
                        return response.bodyToMono(AgentPendingApprovalResponse.class)
                                .switchIfEmpty(Mono.error(new PythonAgentGatewayException(
                                        "Python Agent 返回空待审批响应",
                                        new IllegalStateException("empty pending approval response")
                                )));
                    }
                    int status = response.statusCode().value();
                    return response.releaseBody().then(Mono.error(
                            new PythonAgentGatewayException(
                                    "Python Agent 返回异常状态 " + status,
                                    status
                            )
                    ));
                })
                .onErrorMap(
                        exception -> !(exception instanceof PythonAgentGatewayException),
                        exception -> new PythonAgentGatewayException(
                                "读取 Python Agent 待审批响应失败",
                                exception
                        )
                );
    }

    private Flux<ServerSentEvent<String>> stream(String uri, Object request) {
        return webClient.post()
                .uri(uri)
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(request)
                .exchangeToFlux(response -> {
                    if (response.statusCode().is2xxSuccessful()) {
                        return response.bodyToFlux(SSE_TYPE);
                    }
                    int status = response.statusCode().value();
                    return response.releaseBody().thenMany(Flux.error(
                            new PythonAgentGatewayException(
                                    "Python Agent 返回异常状态 " + status,
                                    status
                            )
                    ));
                })
                .onErrorMap(
                        WebClientRequestException.class,
                        exception -> new PythonAgentGatewayException(
                                "连接 Python Agent 失败: " + exception.getMessage(),
                                exception
                        )
                );
    }
}
