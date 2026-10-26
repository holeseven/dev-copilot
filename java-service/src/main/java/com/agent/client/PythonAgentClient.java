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
        return webClient.post()
                .uri("/chat")
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
