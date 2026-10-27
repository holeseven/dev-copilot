package com.agent.client;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * 审批结果公开状态白名单。
 *
 * @author lxy
 */
public enum AgentApprovalOutcomeStatus {
    APPLYING("applying", false),
    REJECTED("rejected", true),
    FAILED("failed", true),
    RECOVERY_REQUIRED("recovery_required", true),
    APPLIED_RECOVERED("applied_recovered", true),
    APPLIED_AND_TESTS_PASSED("applied_and_tests_passed", true),
    APPLIED_TESTS_FAILED("applied_tests_failed", true),
    APPLIED_TESTS_TIMED_OUT("applied_tests_timed_out", true),
    APPLIED_TEST_TERMINATION_FAILED("applied_test_termination_failed", true),
    APPLIED_TEST_ERROR("applied_test_error", true);

    private final String value;
    private final boolean terminal;

    AgentApprovalOutcomeStatus(String value, boolean terminal) {
        this.value = value;
        this.terminal = terminal;
    }

    @JsonCreator
    public static AgentApprovalOutcomeStatus fromValue(String value) {
        for (AgentApprovalOutcomeStatus status : values()) {
            if (status.value.equals(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("Unknown approval outcome status");
    }

    @JsonValue
    public String value() {
        return value;
    }

    public boolean isTerminal() {
        return terminal;
    }

    public boolean isApplied() {
        return value.startsWith("applied_");
    }

    public boolean requiresValidation() {
        return isApplied() && this != APPLIED_RECOVERED;
    }

    public String expectedValidationStatus() {
        return switch (this) {
            case APPLIED_AND_TESTS_PASSED -> "passed";
            case APPLIED_TESTS_FAILED -> "failed";
            case APPLIED_TESTS_TIMED_OUT -> "timed_out";
            case APPLIED_TEST_TERMINATION_FAILED -> "termination_failed";
            case APPLIED_TEST_ERROR -> "error";
            default -> null;
        };
    }
}
