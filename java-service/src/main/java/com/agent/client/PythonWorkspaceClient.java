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

import java.util.function.Function;

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

    public Mono<ResponseEntity<RagIndexStatusResponse>> ragIndexStatus(String workspaceId) {
        return exchangeRequired(webClient.get().uri(builder -> builder
                .path("/workspaces")
                .pathSegment(workspaceId, "rag", "index")
                .build()), RagIndexStatusResponse.class, null, workspaceId,
                RagIndexStatusResponse::workspaceId);
    }

    public Mono<ResponseEntity<RagIndexStatusResponse>> rebuildRagIndex(String workspaceId) {
        return exchangeRequired(webClient.post().uri(builder -> builder
                .path("/workspaces")
                .pathSegment(workspaceId, "rag", "index", "rebuild")
                .build()), RagIndexStatusResponse.class, 202, workspaceId,
                RagIndexStatusResponse::workspaceId);
    }

    public Mono<ResponseEntity<RagQueryResponse>> queryRag(
            String workspaceId,
            RagQueryRequest request
    ) {
        return exchangeRequired(webClient.post().uri(builder -> builder
                        .path("/workspaces")
                        .pathSegment(workspaceId, "rag", "query")
                        .build())
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(request), RagQueryResponse.class, null, workspaceId,
                RagQueryResponse::workspaceId);
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

    private <T> Mono<ResponseEntity<T>> exchangeRequired(
            WebClient.RequestHeadersSpec<?> request,
            Class<T> bodyType,
            Integer requiredStatus,
            String expectedWorkspaceId,
            Function<T, String> workspaceId
    ) {
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
                    if (requiredStatus != null && status != requiredStatus) {
                        return response.releaseBody().then(Mono.error(
                                new PythonAgentGatewayException(
                                        "Python Agent RAG 响应状态不符合合同",
                                        new IllegalStateException("unexpected status")
                                )
                        ));
                    }
                    return response.bodyToMono(bodyType)
                            .map(body -> {
                                if (!expectedWorkspaceId.equals(workspaceId.apply(body))) {
                                    throw new PythonAgentGatewayException(
                                            "Python Agent RAG 响应 Workspace 不匹配",
                                            new IllegalStateException("workspace mismatch")
                                    );
                                }
                                return ResponseEntity.status(status).body(body);
                            })
                            .switchIfEmpty(Mono.error(new PythonAgentGatewayException(
                                    "Python Agent 返回空 RAG 响应",
                                    new IllegalStateException("empty response")
                            )));
                })
                .onErrorMap(
                        exception -> !(exception instanceof PythonAgentGatewayException),
                        exception -> new PythonAgentGatewayException(
                                "读取 Python Agent RAG 响应失败",
                                exception
                        )
                );
    }
}
