package com.agent.client;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * @author lxy
 */
class AgentChatRequestTest {

    private final Validator validator = Validation.buildDefaultValidatorFactory().getValidator();

    @Test
    void rejectsBlankRequiredFields() {
        AgentChatRequest request = new AgentChatRequest(" ", "", null);

        Set<ConstraintViolation<AgentChatRequest>> violations = validator.validate(request);

        assertThat(violations)
                .extracting(violation -> violation.getPropertyPath().toString())
                .containsExactlyInAnyOrder("message", "sessionId", "workspaceId");
    }
}
