package com.agent.controller;

import com.agent.client.PythonWorkspaceClient;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

/**
 * 浏览器访问 Workspace 的统一 Java API 入口。
 *
 * @author lxy
 */
@RestController
@RequestMapping("/api/v1/workspaces")
public class WorkspaceController {

    private final PythonWorkspaceClient pythonWorkspaceClient;

    public WorkspaceController(PythonWorkspaceClient pythonWorkspaceClient) {
        this.pythonWorkspaceClient = pythonWorkspaceClient;
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Mono<ResponseEntity<JsonNode>> listWorkspaces() {
        return pythonWorkspaceClient.listWorkspaces();
    }

    @PostMapping(
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public Mono<ResponseEntity<JsonNode>> createWorkspace(@RequestBody JsonNode workspace) {
        return pythonWorkspaceClient.createWorkspace(workspace);
    }

    @GetMapping(value = "/{workspaceId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public Mono<ResponseEntity<JsonNode>> getWorkspace(@PathVariable String workspaceId) {
        return pythonWorkspaceClient.getWorkspace(workspaceId);
    }

    @DeleteMapping("/{workspaceId}")
    public Mono<ResponseEntity<JsonNode>> deleteWorkspace(@PathVariable String workspaceId) {
        return pythonWorkspaceClient.deleteWorkspace(workspaceId);
    }

    @PostMapping(
            value = "/{workspaceId}/repositories",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public Mono<ResponseEntity<JsonNode>> addRepository(
            @PathVariable String workspaceId,
            @RequestBody JsonNode repository
    ) {
        return pythonWorkspaceClient.addRepository(workspaceId, repository);
    }

    @PostMapping(value = "/{workspaceId}/scan", produces = MediaType.APPLICATION_JSON_VALUE)
    public Mono<ResponseEntity<JsonNode>> scanWorkspace(@PathVariable String workspaceId) {
        return pythonWorkspaceClient.scanWorkspace(workspaceId);
    }

    @PostMapping(value = "/{workspaceId}/activate", produces = MediaType.APPLICATION_JSON_VALUE)
    public Mono<ResponseEntity<JsonNode>> activateWorkspace(@PathVariable String workspaceId) {
        return pythonWorkspaceClient.activateWorkspace(workspaceId);
    }
}
