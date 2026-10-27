package com.agent.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.HttpStatus;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.catchThrowableOfType;

/**
 * @author lxy
 */
class PythonWorkspaceClientTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private HttpServer server;

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void listsAndCreatesWorkspacesWithTransparentJson() throws Exception {
        AtomicReference<String> method = new AtomicReference<>();
        AtomicReference<JsonNode> requestBody = new AtomicReference<>();
        startServer(exchange -> {
            method.set(exchange.getRequestMethod());
            if ("GET".equals(exchange.getRequestMethod())) {
                send(exchange, 200, "[{\"id\":\"alpha\",\"name\":\"Alpha\"}]");
                return;
            }
            requestBody.set(objectMapper.readTree(exchange.getRequestBody()));
            send(exchange, 201, "{\"id\":\"beta\",\"name\":\"Beta\"}");
        });
        PythonWorkspaceClient client = client();

        ResponseEntity<JsonNode> listed = client.listWorkspaces()
                .block(Duration.ofSeconds(3));
        JsonNode createPayload = objectMapper.readTree(
                "{\"id\":\"beta\",\"name\":\"Beta\"}");
        ResponseEntity<JsonNode> created = client.createWorkspace(createPayload)
                .block(Duration.ofSeconds(3));

        assertThat(listed).isNotNull();
        assertThat(listed.getBody()).isEqualTo(objectMapper.readTree(
                "[{\"id\":\"alpha\",\"name\":\"Alpha\"}]"));
        assertThat(method.get()).isEqualTo("POST");
        assertThat(requestBody.get()).isEqualTo(createPayload);
        assertThat(created).isNotNull();
        assertThat(created.getStatusCode().value()).isEqualTo(201);
        assertThat(created.getBody()).isEqualTo(createPayload);
    }

    @Test
    void safelyEncodesWorkspacePathSegment() throws Exception {
        AtomicReference<String> rawPath = new AtomicReference<>();
        startServer(exchange -> {
            rawPath.set(exchange.getRequestURI().getRawPath());
            send(exchange, 200, "{\"id\":\"team/blue space\"}");
        });

        ResponseEntity<JsonNode> response = client().getWorkspace("team/blue space")
                .block(Duration.ofSeconds(3));

        assertThat(response).isNotNull();
        assertThat(rawPath.get()).isEqualTo("/workspaces/team%2Fblue%20space");
    }

    @Test
    void preservesNoContentWithoutInventingABody() throws Exception {
        AtomicReference<String> method = new AtomicReference<>();
        startServer(exchange -> {
            method.set(exchange.getRequestMethod());
            exchange.sendResponseHeaders(204, -1);
        });

        ResponseEntity<JsonNode> response = client().deleteWorkspace("alpha")
                .block(Duration.ofSeconds(3));

        assertThat(method.get()).isEqualTo("DELETE");
        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(204);
        assertThat(response.getBody()).isNull();
    }

    @Test
    void mapsFourHundredResponsesWithoutLeakingTheResponseBody() throws Exception {
        startServer(exchange -> send(exchange, 404,
                "{\"detail\":\"secret absolute path C:/private/repository\"}"));

        PythonAgentGatewayException failure = catchThrowableOfType(
                () -> client().getWorkspace("missing").block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class
        );

        assertThat(failure).isNotNull();
        assertThat(failure.getUpstreamStatus()).isEqualTo(404);
        assertThat(failure.getMessage()).contains("404");
        assertThat(failure.getMessage()).doesNotContain("secret absolute path");
        assertThat(failure.getMessage()).doesNotContain("C:/private/repository");
    }

    @Test
    void forwardsRepositoryScanAndActivateRoutes() throws Exception {
        List<String> requests = new ArrayList<>();
        startServer(exchange -> {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            requests.add(exchange.getRequestMethod() + " "
                    + exchange.getRequestURI().getRawPath() + " " + body);
            String rawPath = exchange.getRequestURI().getRawPath();
            int status = rawPath.endsWith("/repositories") ? 201 : 200;
            String response = rawPath.endsWith("/scan") ? "[]" : "{\"id\":\"alpha\"}";
            send(exchange, status, response);
        });
        PythonWorkspaceClient client = client();
        JsonNode repository = objectMapper.readTree(
                "{\"name\":\"service\",\"local_path\":\"C:/code/service\"}");

        ResponseEntity<JsonNode> added = client.addRepository("alpha", repository)
                .block(Duration.ofSeconds(3));
        client.scanWorkspace("alpha").block(Duration.ofSeconds(3));
        client.activateWorkspace("alpha").block(Duration.ofSeconds(3));

        assertThat(added).isNotNull();
        assertThat(added.getStatusCode().value()).isEqualTo(201);
        assertThat(requests).containsExactly(
                "POST /workspaces/alpha/repositories " + repository,
                "POST /workspaces/alpha/scan ",
                "POST /workspaces/alpha/activate "
        );
    }

    @Test
    void forwardsTypedRagRoutesAndPreservesAcceptedStatus() throws Exception {
        List<String> requests = new ArrayList<>();
        startServer(exchange -> {
            String path = exchange.getRequestURI().getRawPath();
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            requests.add(exchange.getRequestMethod() + " " + path + " " + body);
            if (path.endsWith("/index/rebuild")) {
                send(exchange, 202, """
                        {"workspace_id":"team/blue space","state":"queued","index_generation":null,
                         "job_id":"job-1","repository_count":2,"file_count":0,"chunk_count":0,
                         "updated_at":null,"error_code":null}
                        """);
            } else if (path.endsWith("/query")) {
                send(exchange, 200, """
                        {"workspace_id":"team/blue space","index_generation":"rag-1",
                         "citations":[{"citation_id":"cite-1","rank":1,"repository":"service",
                         "virtual_path":"service/docs/domain.md","start_line":4,"end_line":8,
                         "excerpt":"safe excerpt","content_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}
                        """);
            } else {
                send(exchange, 200, """
                        {"workspace_id":"team/blue space","state":"ready","index_generation":"rag-1",
                         "job_id":null,"repository_count":2,"file_count":10,"chunk_count":40,
                         "updated_at":"2026-10-27T09:00:00Z","error_code":null}
                        """);
            }
        });
        PythonWorkspaceClient client = client();

        ResponseEntity<RagIndexStatusResponse> status = client
                .ragIndexStatus("team/blue space").block(Duration.ofSeconds(3));
        ResponseEntity<RagIndexStatusResponse> rebuild = client
                .rebuildRagIndex("team/blue space").block(Duration.ofSeconds(3));
        ResponseEntity<RagQueryResponse> query = client.queryRag(
                "team/blue space", new RagQueryRequest("订单状态", 5))
                .block(Duration.ofSeconds(3));

        assertThat(status).isNotNull();
        assertThat(status.getBody().state()).isEqualTo(RagIndexState.READY);
        assertThat(rebuild).isNotNull();
        assertThat(rebuild.getStatusCode().value()).isEqualTo(202);
        assertThat(rebuild.getBody().state()).isEqualTo(RagIndexState.QUEUED);
        assertThat(query).isNotNull();
        assertThat(query.getBody().citations()).containsExactly(new RagCitation(
                "cite-1", 1, "service", "service/docs/domain.md", 4, 8,
                "safe excerpt", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
        assertThat(objectMapper.valueToTree(query.getBody()).toString())
                .doesNotContain("absolute_path", "provider_secret", "C:/secret");
        assertThat(requests).hasSize(3);
        assertThat(requests.subList(0, 2)).containsExactly(
                "GET /workspaces/team%2Fblue%20space/rag/index ",
                "POST /workspaces/team%2Fblue%20space/rag/index/rebuild "
        );
        String queryPrefix = "POST /workspaces/team%2Fblue%20space/rag/query ";
        assertThat(requests.get(2)).startsWith(queryPrefix);
        assertThat(objectMapper.readTree(requests.get(2).substring(queryPrefix.length())))
                .isEqualTo(objectMapper.readTree("{\"query\":\"订单状态\",\"top_k\":5}"));
    }

    @Test
    void ragResponsesFailClosedForEmptyBodyUnknownStateAndUpstreamErrors() throws Exception {
        startServer(exchange -> {
            String path = exchange.getRequestURI().getRawPath();
            if (path.endsWith("/query")) {
                send(exchange, 422, "{\"detail\":\"C:/secret/index\"}");
            } else if (path.endsWith("/rebuild")) {
                exchange.getResponseHeaders().set(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE);
                exchange.sendResponseHeaders(200, -1);
            } else {
                send(exchange, 200, "{\"workspace_id\":\"alpha\",\"state\":\"unknown\"}");
            }
        });
        PythonWorkspaceClient client = client();

        PythonAgentGatewayException unknown = catchThrowableOfType(
                () -> client.ragIndexStatus("alpha").block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);
        PythonAgentGatewayException empty = catchThrowableOfType(
                () -> client.rebuildRagIndex("alpha").block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);
        PythonAgentGatewayException rejected = catchThrowableOfType(
                () -> client.queryRag("alpha", new RagQueryRequest("q", 1))
                        .block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);

        assertThat(unknown).isNotNull();
        assertThat(empty).isNotNull();
        assertThat(rejected.getUpstreamStatus()).isEqualTo(422);
        assertThat(rejected.getMessage()).doesNotContain("C:/secret/index");
    }

    @Test
    void allRagRoutesRejectMismatchedWorkspaceIdentity() throws Exception {
        startServer(exchange -> {
            String path = exchange.getRequestURI().getRawPath();
            if (path.endsWith("/query")) {
                send(exchange, 200, """
                        {"workspace_id":"beta","index_generation":"rag-1","citations":[]}
                        """);
            } else if (path.endsWith("/rebuild")) {
                send(exchange, 202, """
                        {"workspace_id":"beta","state":"queued","index_generation":null,
                         "job_id":"job-1","repository_count":1,"file_count":0,"chunk_count":0,
                         "updated_at":null,"error_code":null}
                        """);
            } else {
                send(exchange, 200, """
                        {"workspace_id":"beta","state":"ready","index_generation":"rag-1",
                         "job_id":null,"repository_count":1,"file_count":4,"chunk_count":8,
                         "updated_at":"2026-10-27T09:00:00Z","error_code":null}
                        """);
            }
        });
        PythonWorkspaceClient client = client();

        PythonAgentGatewayException statusMismatch = catchThrowableOfType(
                () -> client.ragIndexStatus("alpha").block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);
        PythonAgentGatewayException rebuildMismatch = catchThrowableOfType(
                () -> client.rebuildRagIndex("alpha").block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);
        PythonAgentGatewayException queryMismatch = catchThrowableOfType(
                () -> client.queryRag("alpha", new RagQueryRequest("query", 5))
                        .block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);

        assertThat(statusMismatch).isNotNull();
        assertThat(statusMismatch.getUpstreamStatus()).isNull();
        assertThat(rebuildMismatch).isNotNull();
        assertThat(rebuildMismatch.getUpstreamStatus()).isNull();
        assertThat(queryMismatch).isNotNull();
        assertThat(queryMismatch.getUpstreamStatus()).isNull();
    }

    @Test
    void indexStatusRejectsMissingFieldsAndInvalidStateCombinations() throws Exception {
        ObjectNode complete = (ObjectNode) objectMapper.readTree("""
                {"workspace_id":"alpha","state":"not_indexed","index_generation":null,
                 "job_id":null,"repository_count":1,"file_count":0,"chunk_count":0,
                 "updated_at":null,"error_code":null}
                """);
        List<String> responses = new ArrayList<>();
        for (String field : List.of(
                "workspace_id", "state", "index_generation", "job_id", "repository_count",
                "file_count", "chunk_count", "updated_at", "error_code")) {
            ObjectNode missing = complete.deepCopy();
            missing.remove(field);
            responses.add(objectMapper.writeValueAsString(missing));
        }
        responses.addAll(List.of(
                """
                {"workspace_id":"alpha","state":"ready","index_generation":null,
                 "job_id":null,"repository_count":1,"file_count":4,"chunk_count":8,
                 "updated_at":"2026-10-27T09:00:00Z","error_code":null}
                """,
                """
                {"workspace_id":"alpha","state":"stale","index_generation":null,
                 "job_id":null,"repository_count":1,"file_count":4,"chunk_count":8,
                 "updated_at":"2026-10-27T09:00:00Z","error_code":null}
                """,
                """
                {"workspace_id":"alpha","state":"queued","index_generation":null,
                 "job_id":null,"repository_count":1,"file_count":0,"chunk_count":0,
                 "updated_at":null,"error_code":null}
                """,
                """
                {"workspace_id":"alpha","state":"building","index_generation":null,
                 "job_id":null,"repository_count":1,"file_count":0,"chunk_count":0,
                 "updated_at":null,"error_code":null}
                """,
                """
                {"workspace_id":"alpha","state":"failed","index_generation":null,
                 "job_id":null,"repository_count":1,"file_count":0,"chunk_count":0,
                 "updated_at":null,"error_code":null}
                """,
                """
                {"workspace_id":"alpha","state":"failed","index_generation":null,
                 "job_id":null,"repository_count":1,"file_count":0,"chunk_count":0,
                 "updated_at":null,"error_code":"C:/secret/failure"}
                """
        ));
        AtomicInteger call = new AtomicInteger();
        startServer(exchange -> send(exchange, 200, responses.get(call.getAndIncrement())));
        PythonWorkspaceClient client = client();

        for (int index = 0; index < responses.size(); index++) {
            PythonAgentGatewayException failure = catchThrowableOfType(
                    () -> client.ragIndexStatus("alpha").block(Duration.ofSeconds(3)),
                    PythonAgentGatewayException.class);
            assertThat(failure).as("invalid status response %s", index).isNotNull();
            assertThat(failure.getUpstreamStatus()).isNull();
        }
    }

    @Test
    void ragQueryRejectsUnsafeCitationFields() throws Exception {
        String validHash = "a".repeat(64);
        String longExcerpt = "x".repeat(2001);
        String[] citations = {
                citationJson("team/backend", "backend/src/App.java", "excerpt", validHash),
                citationJson("backend", "/etc/secret/App.java", "excerpt", validHash),
                citationJson("backend", "C:/secret/App.java", "excerpt", validHash),
                citationJson("backend", "src\\App.java", "excerpt", validHash),
                citationJson("backend", "../secret/App.java", "excerpt", validHash),
                citationJson("backend", "file:secret/App.java", "excerpt", validHash),
                citationJson("backend", "backend/src/\u202Eleak.java", "excerpt", validHash),
                citationJson("backend", "other/src/App.java", "excerpt", validHash),
                citationJson("backend", "backend/src/App.java", "excerpt", "abc"),
                citationJson("backend", "backend/src/App.java", longExcerpt, validHash),
                citationJsonWithId("bad/id", "backend", "backend/src/App.java", validHash),
                citationJsonWithId("x".repeat(65), "backend", "backend/src/App.java", validHash)
        };
        AtomicInteger call = new AtomicInteger();
        startServer(exchange -> send(exchange, 200,
                "{\"workspace_id\":\"alpha\",\"index_generation\":\"rag-1\",\"citations\":["
                        + citations[call.getAndIncrement()] + "]}"));
        PythonWorkspaceClient client = client();

        for (int index = 0; index < citations.length; index++) {
            PythonAgentGatewayException failure = catchThrowableOfType(
                    () -> client.queryRag("alpha", new RagQueryRequest("query", 5))
                            .block(Duration.ofSeconds(3)),
                    PythonAgentGatewayException.class);
            assertThat(failure).as("unsafe citation %s", index).isNotNull();
            assertThat(failure.getUpstreamStatus()).isNull();
        }
    }

    @Test
    void rebuildRejectsSuccessfulStatusOtherThanAccepted() throws Exception {
        startServer(exchange -> send(exchange, 200, """
                {"workspace_id":"alpha","state":"queued","index_generation":null,
                 "job_id":"job-1","repository_count":1,"file_count":0,"chunk_count":0,
                 "updated_at":null,"error_code":null}
                """));

        PythonAgentGatewayException failure = catchThrowableOfType(
                () -> client().rebuildRagIndex("alpha").block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);

        assertThat(failure).isNotNull();
        assertThat(failure.getUpstreamStatus()).isNull();
    }

    @Test
    void ragQueryRejectsResponseBodyBeyondWebClientMemoryLimit() throws Exception {
        startServer(exchange -> send(exchange, 200,
                "{\"workspace_id\":\"alpha\",\"index_generation\":\"rag-1\",\"citations\":[],"
                        + "\"padding\":\"" + "x".repeat(300_000) + "\"}"));

        PythonAgentGatewayException failure = catchThrowableOfType(
                () -> client().queryRag("alpha", new RagQueryRequest("query", 1))
                        .block(Duration.ofSeconds(3)),
                PythonAgentGatewayException.class);

        assertThat(failure).isNotNull();
        assertThat(failure.getUpstreamStatus()).isNull();
    }

    @Test
    void ragQueryCancellationPropagatesToTheEngineResponse() {
        AtomicBoolean cancelled = new AtomicBoolean();
        WebClient webClient = WebClient.builder()
                .exchangeFunction(request -> reactor.core.publisher.Mono.just(
                        ClientResponse.create(HttpStatus.OK)
                                .header(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                                .body(Flux.<org.springframework.core.io.buffer.DataBuffer>never()
                                        .doOnCancel(() -> cancelled.set(true)))
                                .build()
                ))
                .build();
        PythonWorkspaceClient client = new PythonWorkspaceClient(webClient);

        var subscription = client.queryRag("alpha", new RagQueryRequest("query", 5)).subscribe();
        subscription.dispose();

        assertThat(cancelled).isTrue();
    }

    @Test
    void cancellationPropagatesToTheEngineResponse() {
        AtomicBoolean cancelled = new AtomicBoolean();
        WebClient webClient = WebClient.builder()
                .exchangeFunction(request -> reactor.core.publisher.Mono.just(
                        ClientResponse.create(HttpStatus.OK)
                                .header(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                                .body(Flux.<org.springframework.core.io.buffer.DataBuffer>never()
                                        .doOnCancel(() -> cancelled.set(true)))
                                .build()
                ))
                .build();
        PythonWorkspaceClient client = new PythonWorkspaceClient(webClient);

        var subscription = client.listWorkspaces().subscribe();
        subscription.dispose();

        assertThat(cancelled).isTrue();
    }

    private PythonWorkspaceClient client() {
        return new PythonWorkspaceClient(WebClient.builder(), baseUrl());
    }

    private void startServer(ExchangeHandler handler) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/workspaces", exchange -> {
            try {
                handler.handle(exchange);
            } finally {
                exchange.close();
            }
        });
        server.start();
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private void send(HttpExchange exchange, int status, String content) throws IOException {
        byte[] bytes = content.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE);
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }

    private String citationJson(
            String repository,
            String virtualPath,
            String excerpt,
            String contentSha256
    ) throws Exception {
        return objectMapper.writeValueAsString(java.util.Map.of(
                "citation_id", "cite-1",
                "rank", 1,
                "repository", repository,
                "virtual_path", virtualPath,
                "start_line", 1,
                "end_line", 2,
                "excerpt", excerpt,
                "content_sha256", contentSha256
        ));
    }

    private String citationJsonWithId(
            String citationId,
            String repository,
            String virtualPath,
            String contentSha256
    ) throws Exception {
        return objectMapper.writeValueAsString(java.util.Map.of(
                "citation_id", citationId,
                "rank", 1,
                "repository", repository,
                "virtual_path", virtualPath,
                "start_line", 1,
                "end_line", 2,
                "excerpt", "excerpt",
                "content_sha256", contentSha256
        ));
    }

    @FunctionalInterface
    private interface ExchangeHandler {
        void handle(HttpExchange exchange) throws IOException;
    }
}
