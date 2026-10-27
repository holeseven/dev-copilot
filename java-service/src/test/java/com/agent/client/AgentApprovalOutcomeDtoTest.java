package com.agent.client;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * @author lxy
 */
class AgentApprovalOutcomeDtoTest {

    private final ObjectMapper lenientMapper = new ObjectMapper()
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);

    @Test
    void parsesTheStrictSnakeCaseOutcomeContract() throws Exception {
        AgentApprovalOutcomeResponse response = lenientMapper.readValue("""
                {
                  "workspace_id":"workspace-1",
                  "session_id":"session-1",
                  "approval_id":"approval-1",
                  "status":"applied_tests_failed",
                  "terminal":true,
                  "mutation":{
                    "proposal_id":"approval-1",
                    "workspace_id":"workspace-1",
                    "virtual_path":"service/app.py",
                    "operation":"replace",
                    "before_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "after_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                  },
                  "validation":{
                    "status":"failed",
                    "exit_code":1,
                    "truncated":false,
                    "profile":"python_pytest"
                  }
                }
                """, AgentApprovalOutcomeResponse.class);

        assertThat(response.status()).isEqualTo(AgentApprovalOutcomeStatus.APPLIED_TESTS_FAILED);
        assertThat(response.terminal()).isTrue();
        assertThat(response.mutation().virtualPath()).isEqualTo("service/app.py");
        assertThat(response.validation().exitCode()).isEqualTo(1);
        assertThat(lenientMapper.writeValueAsString(response)).doesNotContain("host_path");
    }

    @Test
    void rejectsUnknownFieldsEvenWhenTheGlobalMapperIsLenient() {
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a", "status":"applying","terminal":false,"internal_thread_id":"secret"}
                """, AgentApprovalOutcomeResponse.class);
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a", "status":"failed","terminal":true,"mutation":{"proposal_id":"a","workspace_id":"w","virtual_path":"app.py","operation":"replace","before_sha256":"b","after_sha256":"c","host_path":"C:/secret"}}
                """, AgentApprovalOutcomeResponse.class);
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a", "status":"failed","terminal":true,"validation":{"status":"error","exit_code":null,"stdout":null,"stderr":null,"truncated":false,"profile":"python_pytest","command":"secret"}}
                """, AgentApprovalOutcomeResponse.class);
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a","status":"applied_and_tests_passed","terminal":true}
                """, AgentApprovalOutcomeResponse.class);
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a","status":"applying","terminal":false,"mutation":{"proposal_id":"a","workspace_id":"w","virtual_path":"../escape.py","operation":"replace","before_sha256":null,"after_sha256":"bad"}}
                """, AgentApprovalOutcomeResponse.class);
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a","status":"applied_recovered","terminal":true,"mutation":{"proposal_id":"different","workspace_id":"w","virtual_path":"src/app.py","operation":"create","before_sha256":null,"after_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
                """, AgentApprovalOutcomeResponse.class);
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a","extra":"secret"}
                """, AgentApprovalOutcomeRequest.class);
    }

    @Test
    void rejectsUnknownStatusAndInconsistentTerminalFlag() {
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a","status":"secret","terminal":true}
                """, AgentApprovalOutcomeResponse.class);
        assertRejected("""
                {"workspace_id":"w","session_id":"s","approval_id":"a","status":"applying","terminal":true}
                """, AgentApprovalOutcomeResponse.class);
    }

    @Test
    void parsesRecoveryRequiredAsANonAppliedTerminalOutcome() throws Exception {
        AgentApprovalOutcomeResponse response = lenientMapper.readValue("""
                {"workspace_id":"w","session_id":"s","approval_id":"a",
                 "status":"recovery_required","terminal":true}
                """, AgentApprovalOutcomeResponse.class);

        assertThat(response.status()).isEqualTo(AgentApprovalOutcomeStatus.RECOVERY_REQUIRED);
        assertThat(response.terminal()).isTrue();
        assertThat(response.mutation()).isNull();
        assertThat(response.validation()).isNull();
    }

    private <T> void assertRejected(String json, Class<T> type) {
        assertThatThrownBy(() -> lenientMapper.readValue(json, type))
                .isInstanceOf(Exception.class);
    }
}
