package com.agent.client;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientRequestException;
import reactor.core.publisher.Mono;

/**
 * Python Agent Workspace API 的透明 JSON 客户端。
 *
 * @author lxy
 */
@Component
public class PythonWorkspaceClient {

    private final WebClient webClient;

    @Autowired
    public PythonWorkspaceClient(
            WebClient.Builder webClientBuilder,
            @Value("${dev-copilot.engine.base-url}") String engineBaseUrl
    ) {
        this(webClientBuilder.baseUrl(engineBaseUrl).build());
    }

    protected PythonWorkspaceClient(WebClient webClient) {
        this.webClient = webClient;
    }

    public Mono<ResponseEntity<JsonNode>> listWorkspaces() {
        return exchange(webClient.get().uri("/workspaces"));
    }

    public Mono<ResponseEntity<JsonNode>> createWorkspace(JsonNode workspace) {
        return exchange(webClient.post()
                .uri("/workspaces")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(workspace));
    }

    public Mono<ResponseEntity<JsonNode>> getWorkspace(String workspaceId) {
        return exchange(webClient.get().uri(builder -> builder
                .path("/workspaces")
                .pathSegment(workspaceId)
                .build()));
    }

    public Mono<ResponseEntity<JsonNode>> deleteWorkspace(String workspaceId) {
        return exchange(webClient.delete().uri(builder -> builder
                .path("/workspaces")
                .pathSegment(workspaceId)
                .build()));
    }

    public Mono<ResponseEntity<JsonNode>> addRepository(
            String workspaceId,
            JsonNode repository
    ) {
        return exchange(webClient.post().uri(builder -> builder
                        .path("/workspaces")
                        .pathSegment(workspaceId, "repositories")
                        .build())
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(repository));
    }

    public Mono<ResponseEntity<JsonNode>> scanWorkspace(String workspaceId) {
        return exchange(webClient.post().uri(builder -> builder
                .path("/workspaces")
                .pathSegment(workspaceId, "scan")
                .build()));
    }

    public Mono<ResponseEntity<JsonNode>> activateWorkspace(String workspaceId) {
        return exchange(webClient.post().uri(builder -> builder
                .path("/workspaces")
                .pathSegment(workspaceId, "activate")
                .build()));
    }

    private Mono<ResponseEntity<JsonNode>> exchange(WebClient.RequestHeadersSpec<?> request) {
        return request.accept(MediaType.APPLICATION_JSON)
                .exchangeToMono(response -> {
                    int status = response.statusCode().value();
                    if (!response.statusCode().is2xxSuccessful()) {
                        return response.releaseBody().then(Mono.error(
                                new PythonAgentGatewayException(
                                        "Python Agent 返回异常状态: " + status,
                                        status
                                )
                        ));
                    }
                    if (status == 204) {
                        return response.releaseBody().thenReturn(
                                ResponseEntity.status(status).build());
                    }
                    return response.bodyToMono(JsonNode.class)
                            .map(body -> ResponseEntity.status(status).body(body))
                            .switchIfEmpty(Mono.fromSupplier(
                                    () -> ResponseEntity.status(status).build()));
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
