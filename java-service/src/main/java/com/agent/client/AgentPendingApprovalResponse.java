package com.agent.client;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;

/**
 * Python Agent 待审批查询响应。
 *
 * @author lxy
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record AgentPendingApprovalResponse(
        @JsonProperty("workspace_id") String workspaceId,
        @JsonProperty("session_id") String sessionId,
        AgentPendingApprovalStatus status,
        List<AgentPendingApproval> approvals,
        List<AgentApprovalOutcomeResponse> outcomes
) {
    public AgentPendingApprovalResponse(
            String workspaceId,
            String sessionId,
            AgentPendingApprovalStatus status,
            List<AgentPendingApproval> approvals
    ) {
        this(workspaceId, sessionId, status, approvals, null);
    }
}
