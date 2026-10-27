package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 查询一次审批执行结果的安全作用域。
 *
 * @author lxy
 */
public record AgentApprovalOutcomeRequest(
        @JsonProperty("session_id") @NotBlank @Size(max = 128) String sessionId,
        @JsonProperty("workspace_id") @NotBlank @Size(max = 128) String workspaceId,
        @JsonProperty("approval_id") @NotBlank @Size(max = 256) String approvalId
) {
    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown approval outcome request field: " + field);
    }
}
