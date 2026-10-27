package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * RAG 检索响应的安全字段集合。
 *
 * @author lxy
 */
public record RagQueryResponse(
        @JsonProperty("workspace_id") String workspaceId,
        @JsonProperty("index_generation") String indexGeneration,
        List<RagCitation> citations
) {
    public RagQueryResponse {
        if (workspaceId == null || workspaceId.isBlank()) {
            throw new IllegalArgumentException("workspace_id must not be blank");
        }
        if (indexGeneration == null || indexGeneration.isBlank()) {
            throw new IllegalArgumentException("index_generation must not be blank");
        }
        citations = List.copyOf(citations);
    }

    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown RAG query response field: " + field);
    }
}
