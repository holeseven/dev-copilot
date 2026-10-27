package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Objects;

/**
 * Python Agent 返回的严格、脱敏审批结果。
 *
 * @author lxy
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record AgentApprovalOutcomeResponse(
        @JsonProperty(value = "workspace_id", required = true) String workspaceId,
        @JsonProperty(value = "session_id", required = true) String sessionId,
        @JsonProperty(value = "approval_id", required = true) String approvalId,
        @JsonProperty(value = "status", required = true) AgentApprovalOutcomeStatus status,
        @JsonProperty(value = "terminal", required = true) Boolean terminal,
        AgentApprovalOutcomeMutation mutation,
        AgentApprovalOutcomeValidation validation
) {
    public AgentApprovalOutcomeResponse {
        Objects.requireNonNull(workspaceId, "workspace_id must not be null");
        Objects.requireNonNull(sessionId, "session_id must not be null");
        Objects.requireNonNull(approvalId, "approval_id must not be null");
        Objects.requireNonNull(status, "status must not be null");
        Objects.requireNonNull(terminal, "terminal must not be null");
        if (terminal != status.isTerminal()) {
            throw new IllegalArgumentException("terminal does not match approval outcome status");
        }
        if (status.isApplied() != (mutation != null)) {
            throw new IllegalArgumentException("mutation does not match approval outcome status");
        }
        if (mutation != null && (!approvalId.equals(mutation.proposalId())
                || !workspaceId.equals(mutation.workspaceId()))) {
            throw new IllegalArgumentException("mutation scope does not match approval outcome");
        }
        if (status.requiresValidation() != (validation != null)) {
            throw new IllegalArgumentException("validation does not match approval outcome status");
        }
        if (validation != null
                && !status.expectedValidationStatus().equals(validation.status())) {
            throw new IllegalArgumentException("validation status does not match approval outcome status");
        }
    }

    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown approval outcome response field: " + field);
    }
}
