package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * RAG 检索请求。
 *
 * @author lxy
 */
public record RagQueryRequest(
        @NotBlank @Size(max = 4000) String query,
        @JsonProperty("top_k") @Min(1) @Max(20) int topK
) {
    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown RAG query field: " + field);
    }
}
