package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Objects;
import java.util.regex.Pattern;

/**
 * RAG 索引状态响应的安全字段集合。
 *
 * @author lxy
 */
public record RagIndexStatusResponse(
        @JsonProperty(value = "workspace_id", required = true) String workspaceId,
        @JsonProperty(value = "state", required = true) RagIndexState state,
        @JsonProperty(value = "index_generation", required = true) String indexGeneration,
        @JsonProperty(value = "job_id", required = true) String jobId,
        @JsonProperty(value = "repository_count", required = true) Integer repositoryCount,
        @JsonProperty(value = "file_count", required = true) Integer fileCount,
        @JsonProperty(value = "chunk_count", required = true) Integer chunkCount,
        @JsonProperty(value = "updated_at", required = true) String updatedAt,
        @JsonProperty(value = "error_code", required = true) String errorCode
) {
    private static final Pattern SAFE_ERROR_CODE = Pattern.compile("[a-z][a-z0-9_]{0,63}");

    public RagIndexStatusResponse {
        if (workspaceId == null || workspaceId.isBlank()) {
            throw new IllegalArgumentException("workspace_id must not be blank");
        }
        Objects.requireNonNull(state, "state must not be null");
        Objects.requireNonNull(repositoryCount, "repository_count must not be null");
        Objects.requireNonNull(fileCount, "file_count must not be null");
        Objects.requireNonNull(chunkCount, "chunk_count must not be null");
        if (repositoryCount < 0 || fileCount < 0 || chunkCount < 0) {
            throw new IllegalArgumentException("RAG index counts must not be negative");
        }
        if ((state == RagIndexState.READY || state == RagIndexState.STALE)
                && !hasText(indexGeneration)) {
            throw new IllegalArgumentException("Ready RAG index must have index_generation");
        }
        if ((state == RagIndexState.QUEUED || state == RagIndexState.BUILDING)
                && !hasText(jobId)) {
            throw new IllegalArgumentException("Active RAG index job must have job_id");
        }
        if (state == RagIndexState.FAILED
                && (!hasText(errorCode) || !SAFE_ERROR_CODE.matcher(errorCode).matches())) {
            throw new IllegalArgumentException("Failed RAG index must have a safe error_code");
        }
        if (hasText(errorCode) && !SAFE_ERROR_CODE.matcher(errorCode).matches()) {
            throw new IllegalArgumentException("RAG index error_code is unsafe");
        }
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }

    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown RAG index status field: " + field);
    }
}
