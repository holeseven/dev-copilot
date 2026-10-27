package com.agent.client;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Python Agent 待审批查询响应。
 *
 * @author lxy
 */
public record AgentPendingApprovalResponse(
        @JsonProperty("workspace_id") String workspaceId,
        @JsonProperty("session_id") String sessionId,
        AgentPendingApprovalStatus status,
        List<AgentPendingApproval> approvals
) {
}
