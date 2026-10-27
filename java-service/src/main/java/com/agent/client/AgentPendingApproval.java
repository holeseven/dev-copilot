package com.agent.client;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 一个由 Python checkpoint 恢复出的安全审批描述。
 *
 * @author lxy
 */
public record AgentPendingApproval(
        @JsonProperty("approval_id") String approvalId,
        String action,
        String message,
        AgentApprovalPayload payload
) {
}
