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
                .containsExactly("message", "tool", "done");
        assertThat(events).extracting(ServerSentEvent::data)
                .containsExactly(
                        "{\"token\":\"你好\"}",
                        "{\"status\":\"end\",\"tool_name\":\"read_file\"}",
                        "{\"status\":\"completed\"}"
                );
    }

    @Test
    void mapsUpstreamHttpErrorToGatewayException() throws Exception {
        startServer(exchange -> send(exchange, 422, MediaType.APPLICATION_JSON_VALUE,
                "{\"detail\":\"workspace missing\"}"));
        PythonAgentClient client = new PythonAgentClient(WebClient.builder(), baseUrl());

        assertThatThrownBy(() -> client.streamChat(validRequest()).blockLast(Duration.ofSeconds(3)))
                .isInstanceOf(PythonAgentGatewayException.class)
                .hasMessageContaining("422")
                .hasMessageContaining("workspace missing");
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

    private void startServer(ExchangeHandler handler) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/chat", exchange -> {
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
