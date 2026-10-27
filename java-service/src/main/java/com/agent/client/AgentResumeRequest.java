package com.agent.client;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * Python Agent 审批恢复请求。
 *
 * @author lxy
 */
public record AgentResumeRequest(
        @JsonProperty("session_id") @NotBlank @Size(max = 128) String sessionId,
        @JsonProperty("workspace_id") @NotBlank @Size(max = 128) String workspaceId,
        @JsonProperty("approval_id") @NotBlank @Size(max = 256) String approvalId,
        boolean approved,
        @Size(max = 1000) String reason
) {
}
