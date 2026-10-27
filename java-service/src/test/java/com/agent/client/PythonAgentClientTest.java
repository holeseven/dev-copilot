package com.agent.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.buffer.DefaultDataBufferFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * @author lxy
 */
class PythonAgentClientTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private HttpServer server;

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void postsChatRequestAndPreservesSseEventsDataAndOrder() throws Exception {
        AtomicReference<String> method = new AtomicReference<>();
        AtomicReference<String> path = new AtomicReference<>();
        AtomicReference<String> accept = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();
        startServer(exchange -> {
            method.set(exchange.getRequestMethod());
            path.set(exchange.getRequestURI().getPath());
            accept.set(exchange.getRequestHeaders().getFirst(HttpHeaders.ACCEPT));
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            send(exchange, 200, MediaType.TEXT_EVENT_STREAM_VALUE, """
                    event: message
                    data: {"token":"你好"}

                    event: tool
                    data: {"status":"end","tool_name":"read_file"}

                    event: citation
                    data: {"citation_id":"cite-1","virtual_path":"docs/domain.md"}

                    event: done
                    data: {"status":"completed"}

                    """);
        });
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        List<ServerSentEvent<String>> events = client.streamChat(
                        new AgentChatRequest("检查项目", "session-1", "workspace-1"))
                .collectList()
                .block(Duration.ofSeconds(3));

        assertThat(method.get()).isEqualTo("POST");
        assertThat(path.get()).isEqualTo("/chat");
        assertThat(accept.get()).contains(MediaType.TEXT_EVENT_STREAM_VALUE);
        JsonNode requestJson = objectMapper.readTree(body.get());
        assertThat(requestJson.get("message").asText()).isEqualTo("检查项目");
        assertThat(requestJson.get("session_id").asText()).isEqualTo("session-1");
        assertThat(requestJson.get("workspace_id").asText()).isEqualTo("workspace-1");
        assertThat(events).extracting(ServerSentEvent::event)
                .containsExactly("message", "tool", "citation", "done");
        assertThat(events).extracting(ServerSentEvent::data)
                .containsExactly(
                        "{\"token\":\"你好\"}",
                        "{\"status\":\"end\",\"tool_name\":\"read_file\"}",
                        "{\"citation_id\":\"cite-1\",\"virtual_path\":\"docs/domain.md\"}",
                        "{\"status\":\"completed\"}"
                );
    }

    @Test
    void postsResumeRequestWithOnlyTheApprovalContractFields() throws Exception {
        AtomicReference<String> path = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();
        startServer("/chat/resume", exchange -> {
            path.set(exchange.getRequestURI().getPath());
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            send(exchange, 200, MediaType.TEXT_EVENT_STREAM_VALUE, """
                    event: done
                    data: {"status":"completed"}

                    """);
        });
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        List<ServerSentEvent<String>> events = client.streamResume(
                        new AgentResumeRequest("session-1", "workspace-1", "approval-1", true, "reviewed"))
                .collectList()
                .block(Duration.ofSeconds(3));

        assertThat(path.get()).isEqualTo("/chat/resume");
        assertThat(objectMapper.readTree(body.get())).isEqualTo(objectMapper.readTree("""
                {"session_id":"session-1","workspace_id":"workspace-1","approval_id":"approval-1","approved":true,"reason":"reviewed"}
                """));
        assertThat(events).extracting(ServerSentEvent::event).containsExactly("done");
    }

    @Test
    void postsPendingRequestAndDecodesOnlyTheTypedApprovalContract() throws Exception {
        AtomicReference<String> method = new AtomicReference<>();
        AtomicReference<String> path = new AtomicReference<>();
        AtomicReference<String> accept = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();
        startServer("/chat/pending", exchange -> {
            method.set(exchange.getRequestMethod());
            path.set(exchange.getRequestURI().getPath());
            accept.set(exchange.getRequestHeaders().getFirst(HttpHeaders.ACCEPT));
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            send(exchange, 200, MediaType.APPLICATION_JSON_VALUE, """
                    {"workspace_id":"workspace-1","session_id":"session-1","status":"awaiting_approval","approvals":[{"approval_id":"approval-1","action":"apply_workspace_change","message":"请确认修改","payload":{"tool_name":"apply_workspace_change","operation":"replace","workspace_id":"workspace-1","repository":"service","virtual_path":"service/app.py","diff":"--- a/app.py\\n+++ b/app.py","before_sha256":"before","after_sha256":"after","patch_sha256":"patch","validation_profile":"python_pytest"}}],"outcomes":[{"workspace_id":"workspace-1","session_id":"session-1","approval_id":"approval-2","status":"applying","terminal":false}]}
                    """);
        });
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        AgentPendingApprovalResponse response = client.pendingApprovals(
                        new AgentPendingApprovalRequest("session-1", "workspace-1"))
                .block(Duration.ofSeconds(3));

        assertThat(method.get()).isEqualTo("POST");
        assertThat(path.get()).isEqualTo("/chat/pending");
        assertThat(accept.get()).contains(MediaType.APPLICATION_JSON_VALUE);
        assertThat(objectMapper.readTree(body.get())).isEqualTo(objectMapper.readTree("""
                {"session_id":"session-1","workspace_id":"workspace-1"}
                """));
        assertThat(response).isEqualTo(new AgentPendingApprovalResponse(
                "workspace-1",
                "session-1",
                AgentPendingApprovalStatus.AWAITING_APPROVAL,
                List.of(
                new AgentPendingApproval(
                        "approval-1",
                        "apply_workspace_change",
                        "请确认修改",
                        new AgentApprovalPayload(
                                "apply_workspace_change",
                                "replace",
                                "workspace-1",
                                "service",
                                "service/app.py",
                                "--- a/app.py\n+++ b/app.py",
                                "before",
                                "after",
                                "patch",
                                "python_pytest"
                        )
                )
        ), response.outcomes()));
        assertThat(response.outcomes()).singleElement().satisfies(outcome -> {
            assertThat(outcome.approvalId()).isEqualTo("approval-2");
            assertThat(outcome.status()).isEqualTo(AgentApprovalOutcomeStatus.APPLYING);
        });
    }

    @Test
    void rejectsPendingResponseWithMismatchedOuterScope() throws Exception {
        startServer("/chat/pending", exchange -> send(
                exchange,
                200,
                MediaType.APPLICATION_JSON_VALUE,
                """
                {"workspace_id":"other","session_id":"session-1","status":"idle","approvals":[]}
                """
        ));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.pendingApprovals(validPendingRequest())
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("作用域不匹配");
    }

    @Test
    void rejectsPendingResponseWithMismatchedNestedScopes() throws Exception {
        startServer("/chat/pending", exchange -> send(
                exchange,
                200,
                MediaType.APPLICATION_JSON_VALUE,
                """
                {"workspace_id":"workspace-1","session_id":"session-1","status":"awaiting_approval",
                 "approvals":[{"approval_id":"approval-1","action":"apply_workspace_change","message":"review",
                   "payload":{"workspace_id":"other"}}],
                 "outcomes":[{"workspace_id":"workspace-1","session_id":"other","approval_id":"approval-2",
                   "status":"applying","terminal":false}]}
                """
        ));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.pendingApprovals(validPendingRequest())
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("作用域不匹配");
    }

    @Test
    void pendingApprovalErrorDoesNotExposeUpstreamBody() throws Exception {
        startServer("/chat/pending", exchange -> send(exchange, 422, MediaType.APPLICATION_JSON_VALUE,
                "{\"detail\":\"secret checkpoint detail\"}"));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.pendingApprovals(
                        new AgentPendingApprovalRequest("session-1", "workspace-1"))
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("422")
                .hasMessageNotContaining("secret checkpoint detail");
    }

    @Test
    void rejectsAnEmptySuccessfulPendingApprovalResponse() {
        WebClient webClient = WebClient.builder()
                .exchangeFunction(request -> Mono.just(ClientResponse.create(HttpStatus.OK)
                        .header(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                        .build()))
                .build();
        PythonAgentClient client = new PythonAgentClient(webClient);

        assertThatThrownBy(() -> client.pendingApprovals(validPendingRequest())
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("空待审批响应");
    }

    @Test
    void pendingApprovalCancellationCancelsUpstreamResponseBody() {
        AtomicBoolean cancelled = new AtomicBoolean();
        Flux<org.springframework.core.io.buffer.DataBuffer> responseBody =
                Flux.<org.springframework.core.io.buffer.DataBuffer>never()
                        .doOnCancel(() -> cancelled.set(true));
        WebClient webClient = WebClient.builder()
                .exchangeFunction(request -> Mono.just(ClientResponse.create(HttpStatus.OK)
                        .header(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                        .body(responseBody)
                        .build()))
                .build();
        PythonAgentClient client = new PythonAgentClient(webClient);

        var subscription = client.pendingApprovals(validPendingRequest()).subscribe();
        subscription.dispose();

        assertThat(cancelled).isTrue();
    }

    @Test
    void postsOutcomeRequestAndDecodesOnlyTheStrictTypedContract() throws Exception {
        AtomicReference<String> method = new AtomicReference<>();
        AtomicReference<String> path = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();
        startServer("/chat/outcome", exchange -> {
            method.set(exchange.getRequestMethod());
            path.set(exchange.getRequestURI().getPath());
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            send(exchange, 200, MediaType.APPLICATION_JSON_VALUE, """
                    {"workspace_id":"workspace-1","session_id":"session-1","approval_id":"approval-1","status":"applied_and_tests_passed","terminal":true,"mutation":{"proposal_id":"approval-1","workspace_id":"workspace-1","virtual_path":"service/app.py","operation":"replace","before_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","after_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"validation":{"status":"passed","exit_code":0,"truncated":false,"profile":"python_pytest"}}
                    """);
        });
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        AgentApprovalOutcomeResponse response = client.approvalOutcome(validOutcomeRequest())
                .block(Duration.ofSeconds(3));

        assertThat(method.get()).isEqualTo("POST");
        assertThat(path.get()).isEqualTo("/chat/outcome");
        assertThat(objectMapper.readTree(body.get())).isEqualTo(objectMapper.readTree("""
                {"session_id":"session-1","workspace_id":"workspace-1","approval_id":"approval-1"}
                """));
        assertThat(response.status()).isEqualTo(AgentApprovalOutcomeStatus.APPLIED_AND_TESTS_PASSED);
        assertThat(response.validation().status()).isEqualTo("passed");
        assertThat(objectMapper.writeValueAsString(response)).doesNotContain("stdout");
    }

    @Test
    void outcomeRejectsUnknownInternalFieldsWithoutLeakingThem() throws Exception {
        startServer("/chat/outcome", exchange -> send(exchange, 200, MediaType.APPLICATION_JSON_VALUE, """
                {"workspace_id":"workspace-1","session_id":"session-1","approval_id":"approval-1","status":"failed","terminal":true,"mutation":{"proposal_id":"approval-1","workspace_id":"workspace-1","virtual_path":"service/app.py","operation":"replace","before_sha256":"before","after_sha256":"after","host_path":"C:/secret/source.py"}}
                """));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.approvalOutcome(validOutcomeRequest())
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessage("读取 Python Agent 审批结果失败")
                .hasMessageNotContaining("host_path")
                .hasMessageNotContaining("C:/secret/source.py");
    }

    @Test
    void outcomeHttpErrorsDoNotExposeUpstreamBody() throws Exception {
        startServer("/chat/outcome", exchange -> send(exchange, 409, MediaType.APPLICATION_JSON_VALUE,
                "{\"detail\":\"secret approval state\"}"));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.approvalOutcome(validOutcomeRequest())
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("409")
                .hasMessageNotContaining("secret approval state");
    }

    @Test
    void outcomeCancellationCancelsUpstreamResponseBody() {
        AtomicBoolean cancelled = new AtomicBoolean();
        Flux<org.springframework.core.io.buffer.DataBuffer> responseBody =
                Flux.<org.springframework.core.io.buffer.DataBuffer>never()
                        .doOnCancel(() -> cancelled.set(true));
        WebClient webClient = WebClient.builder()
                .exchangeFunction(request -> Mono.just(ClientResponse.create(HttpStatus.OK)
                        .header(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                        .body(responseBody)
                        .build()))
                .build();
        PythonAgentClient client = new PythonAgentClient(webClient);

        var subscription = client.approvalOutcome(validOutcomeRequest()).subscribe();
        subscription.dispose();

        assertThat(cancelled).isTrue();
    }

    @Test
    void ignoresUnknownPendingApprovalFieldsInsteadOfForwardingThem() throws Exception {
        startServer("/chat/pending", exchange -> send(exchange, 200, MediaType.APPLICATION_JSON_VALUE, """
                {"workspace_id":"workspace-1","session_id":"session-1","status":"awaiting_approval","internal_thread_id":"must-not-forward","approvals":[{"approval_id":"approval-1","action":"write_file","message":"review","details":"internal","payload":{"workspace_id":"workspace-1","virtual_path":"app.py","new_content":"secret source"}}]}
                """));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        AgentPendingApprovalResponse response = client.pendingApprovals(validPendingRequest())
                .block(Duration.ofSeconds(3));
        JsonNode forwardedJson = objectMapper.valueToTree(response);

        assertThat(forwardedJson.toString())
                .doesNotContain("internal_thread_id")
                .doesNotContain("must-not-forward")
                .doesNotContain("details")
                .doesNotContain("new_content")
                .doesNotContain("secret source");
        assertThat(response.approvals().get(0).payload().virtualPath()).isEqualTo("app.py");
    }

    @Test
    void mapsUnknownPendingStatusWithoutLeakingThePayload() throws Exception {
        startServer("/chat/pending", exchange -> send(exchange, 200, MediaType.APPLICATION_JSON_VALUE, """
                {"workspace_id":"workspace-1","session_id":"session-1","status":"secret-invalid-status","approvals":[]}
                """));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.pendingApprovals(validPendingRequest())
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessage("读取 Python Agent 待审批响应失败")
                .hasMessageNotContaining("secret-invalid-status");
    }

    @Test
    void mapsMalformedPendingJsonWithoutLeakingThePayload() throws Exception {
        startServer("/chat/pending", exchange -> send(exchange, 200, MediaType.APPLICATION_JSON_VALUE,
                "{\"secret-checkpoint-content\":"));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.pendingApprovals(validPendingRequest())
                .block(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessage("读取 Python Agent 待审批响应失败")
                .hasMessageNotContaining("secret-checkpoint-content");
    }

    @Test
    void mapsUpstreamHttpErrorToGatewayException() throws Exception {
        startServer(exchange -> send(exchange, 422, MediaType.APPLICATION_JSON_VALUE,
                "{\"detail\":\"workspace missing\"}"));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.streamChat(validRequest()).blockLast(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("422")
                .hasMessageNotContaining("workspace missing");
    }

    @Test
    void mapsConnectionFailureToGatewayException() throws IOException {
        int unavailablePort;
        try (ServerSocket socket = new ServerSocket(0)) {
            unavailablePort = socket.getLocalPort();
        }
        PythonAgentClient client = new PythonAgentClient(
                WebClient.builder(), "http://127.0.0.1:" + unavailablePort);

        assertThatThrownBy(() -> client.streamChat(validRequest()).blockLast(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("连接 Python Agent 失败");
    }

    @Test
    void downstreamCancellationCancelsUpstreamResponseBody() {
        AtomicBoolean cancelled = new AtomicBoolean();
        DefaultDataBufferFactory buffers = new DefaultDataBufferFactory();
        Flux<org.springframework.core.io.buffer.DataBuffer> responseBody = Flux.concat(
                Flux.just(buffers.wrap("event: message\ndata: {\"token\":\"first\"}\n\n"
                        .getBytes(StandardCharsets.UTF_8))),
                Flux.<org.springframework.core.io.buffer.DataBuffer>never()
        ).doOnCancel(() -> cancelled.set(true));
        WebClient webClient = WebClient.builder()
                .exchangeFunction(request -> Mono.just(ClientResponse.create(HttpStatus.OK)
                        .header(HttpHeaders.CONTENT_TYPE, MediaType.TEXT_EVENT_STREAM_VALUE)
                        .body(responseBody)
                        .build()))
                .build();
        PythonAgentClient client = new PythonAgentClient(webClient);

        ServerSentEvent<String> first = client.streamChat(validRequest())
                .take(1)
                .blockLast(Duration.ofSeconds(3));

        assertThat(first).isNotNull();
        assertThat(first.event()).isEqualTo("message");
        assertThat(cancelled).isTrue();
    }

    private AgentChatRequest validRequest() {
        return new AgentChatRequest("hello", "session-1", "workspace-1");
    }

    private AgentPendingApprovalRequest validPendingRequest() {
        return new AgentPendingApprovalRequest("session-1", "workspace-1");
    }

    private AgentApprovalOutcomeRequest validOutcomeRequest() {
        return new AgentApprovalOutcomeRequest("session-1", "workspace-1", "approval-1");
    }

    private void startServer(ExchangeHandler handler) throws IOException {
        startServer("/chat", handler);
    }

    private void startServer(String path, ExchangeHandler handler) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext(path, exchange -> {
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

    private void send(HttpExchange exchange, int status, String contentType, String content) throws IOException {
        byte[] bytes = content.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set(HttpHeaders.CONTENT_TYPE, contentType);
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }

    @FunctionalInterface
    private interface ExchangeHandler {
        void handle(HttpExchange exchange) throws IOException;
    }
}
