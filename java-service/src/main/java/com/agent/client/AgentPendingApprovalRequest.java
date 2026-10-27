package com.agent.client;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * Python Agent 待审批查询请求。
 *
 * @author lxy
 */
public record AgentPendingApprovalRequest(
        @JsonProperty("session_id") @NotBlank @Size(max = 128) String sessionId,
        @JsonProperty("workspace_id") @NotBlank @Size(max = 128) String workspaceId
) {
}
