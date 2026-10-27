package com.agent.client;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Agent 会话的待审批状态。
 *
 * @author lxy
 */
public enum AgentPendingApprovalStatus {
    IDLE("idle"),
    AWAITING_APPROVAL("awaiting_approval");

    private final String value;

    AgentPendingApprovalStatus(String value) {
        this.value = value;
    }

    @JsonCreator
    public static AgentPendingApprovalStatus fromValue(String value) {
        for (AgentPendingApprovalStatus status : values()) {
            if (status.value.equals(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("unsupported pending approval status");
    }

    @JsonValue
    public String value() {
        return value;
    }
}
