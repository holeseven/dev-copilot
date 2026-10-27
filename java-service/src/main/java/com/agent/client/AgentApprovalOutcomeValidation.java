package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Objects;

/**
 * 可公开的固定测试配置执行结果。
 *
 * @author lxy
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record AgentApprovalOutcomeValidation(
        @JsonProperty(value = "status", required = true) String status,
        @JsonProperty("exit_code") Integer exitCode,
        @JsonProperty(value = "truncated", required = true) Boolean truncated,
        @JsonProperty(value = "profile", required = true) String profile
) {
    public AgentApprovalOutcomeValidation {
        Objects.requireNonNull(status, "status must not be null");
        Objects.requireNonNull(truncated, "truncated must not be null");
        Objects.requireNonNull(profile, "profile must not be null");
        if (!java.util.Set.of(
                "passed", "failed", "timed_out", "termination_failed", "error"
        ).contains(status)) {
            throw new IllegalArgumentException("unknown approval validation status");
        }
        if (profile.isBlank() || profile.length() > 128) {
            throw new IllegalArgumentException("invalid approval validation profile");
        }
        if ((status.equals("passed") && !Integer.valueOf(0).equals(exitCode))
                || (status.equals("failed") && exitCode == null)
                || (!status.equals("passed") && !status.equals("failed") && exitCode != null)) {
            throw new IllegalArgumentException("exit code does not match validation status");
        }
    }

    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown approval validation field: " + field);
    }
}
