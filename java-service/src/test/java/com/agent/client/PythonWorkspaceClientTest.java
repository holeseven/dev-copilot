package com.agent.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
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

    @FunctionalInterface
    private interface ExchangeHandler {
        void handle(HttpExchange exchange) throws IOException;
    }
}
