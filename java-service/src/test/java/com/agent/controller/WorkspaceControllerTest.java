package com.agent.controller;

import com.agent.client.PythonAgentGatewayException;
import com.agent.client.PythonWorkspaceClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.util.ArrayList;
import java.util.List;

import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.containsString;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

/**
 * @author lxy
 */
class WorkspaceControllerTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void exposesAllWorkspaceRoutesAndForwardsJson() throws Exception {
        StubPythonWorkspaceClient client = new StubPythonWorkspaceClient();
        JsonNode workspace = objectMapper.readTree("{\"id\":\"alpha\",\"name\":\"Alpha\"}");
        JsonNode repository = objectMapper.readTree(
                "{\"name\":\"service\",\"local_path\":\"C:/code/service\"}");
        JsonNode list = objectMapper.readTree("[{\"id\":\"alpha\"}]");
        client.listResult = Mono.just(ResponseEntity.ok(list));
        client.createResult = Mono.just(ResponseEntity.status(201).body(workspace));
        client.getResult = Mono.just(ResponseEntity.ok(workspace));
        client.deleteResult = Mono.just(ResponseEntity.noContent().build());
        client.repositoryResult = Mono.just(ResponseEntity.status(201).body(workspace));
        client.scanResult = Mono.just(ResponseEntity.ok(objectMapper.readTree("[]")));
        client.activateResult = Mono.just(ResponseEntity.ok(workspace));
        MockMvc mvc = mvc(client);

        performAsync(mvc, get("/api/v1/workspaces"))
                .andExpect(status().isOk())
                .andExpect(content().json(list.toString()));
        performAsync(mvc, post("/api/v1/workspaces")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(workspace.toString()))
                .andExpect(status().isCreated())
                .andExpect(content().json(workspace.toString()));
        performAsync(mvc, get("/api/v1/workspaces/alpha"))
                .andExpect(status().isOk());
        performAsync(mvc, delete("/api/v1/workspaces/alpha"))
                .andExpect(status().isNoContent())
                .andExpect(content().string(""));
        performAsync(mvc, post("/api/v1/workspaces/alpha/repositories")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(repository.toString()))
                .andExpect(status().isCreated());
        performAsync(mvc, post("/api/v1/workspaces/alpha/scan"))
                .andExpect(status().isOk());
        performAsync(mvc, post("/api/v1/workspaces/alpha/activate"))
                .andExpect(status().isOk());

        assertThat(client.calls).containsExactly(
                "list", "create", "get:alpha", "delete:alpha",
                "repository:alpha", "scan:alpha", "activate:alpha"
        );
        assertThat(client.createBody).isEqualTo(workspace);
        assertThat(client.repositoryBody).isEqualTo(repository);
    }

    @Test
    void mapsWorkspaceClientErrorsWithoutLeakingUpstreamBody() throws Exception {
        StubPythonWorkspaceClient client = new StubPythonWorkspaceClient();
        client.getResult = Mono.error(
                new PythonAgentGatewayException("secret upstream body", 404));
        MockMvc mvc = mvc(client);

        performAsync(mvc, get("/api/v1/workspaces/missing"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status").value(404))
                .andExpect(content().string(not(containsString("secret upstream body"))));
    }

    private MockMvc mvc(PythonWorkspaceClient client) {
        return standaloneSetup(new WorkspaceController(client))
                .setControllerAdvice(new ApiExceptionHandler())
                .build();
    }

    private org.springframework.test.web.servlet.ResultActions performAsync(
            MockMvc mvc,
            org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder requestBuilder
    ) throws Exception {
        MvcResult pending = mvc.perform(requestBuilder)
                .andExpect(request().asyncStarted())
                .andReturn();
        return mvc.perform(asyncDispatch(pending));
    }

    private static final class StubPythonWorkspaceClient extends PythonWorkspaceClient {
        private final List<String> calls = new ArrayList<>();
        private Mono<ResponseEntity<JsonNode>> listResult;
        private Mono<ResponseEntity<JsonNode>> createResult;
        private Mono<ResponseEntity<JsonNode>> getResult;
        private Mono<ResponseEntity<JsonNode>> deleteResult;
        private Mono<ResponseEntity<JsonNode>> repositoryResult;
        private Mono<ResponseEntity<JsonNode>> scanResult;
        private Mono<ResponseEntity<JsonNode>> activateResult;
        private JsonNode createBody;
        private JsonNode repositoryBody;

        private StubPythonWorkspaceClient() {
            super(WebClient.builder().build());
        }

        @Override
        public Mono<ResponseEntity<JsonNode>> listWorkspaces() {
            calls.add("list");
            return listResult;
        }

        @Override
        public Mono<ResponseEntity<JsonNode>> createWorkspace(JsonNode workspace) {
            calls.add("create");
            createBody = workspace;
            return createResult;
        }

        @Override
        public Mono<ResponseEntity<JsonNode>> getWorkspace(String workspaceId) {
            calls.add("get:" + workspaceId);
            return getResult;
        }

        @Override
        public Mono<ResponseEntity<JsonNode>> deleteWorkspace(String workspaceId) {
            calls.add("delete:" + workspaceId);
            return deleteResult;
        }

        @Override
        public Mono<ResponseEntity<JsonNode>> addRepository(
                String workspaceId,
                JsonNode repository
        ) {
            calls.add("repository:" + workspaceId);
            repositoryBody = repository;
            return repositoryResult;
        }

        @Override
        public Mono<ResponseEntity<JsonNode>> scanWorkspace(String workspaceId) {
            calls.add("scan:" + workspaceId);
            return scanResult;
        }

        @Override
        public Mono<ResponseEntity<JsonNode>> activateWorkspace(String workspaceId) {
            calls.add("activate:" + workspaceId);
            return activateResult;
        }
    }
}
