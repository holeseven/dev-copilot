package com.agent.client;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;

/**
 * Python Agent 聊天请求。
 *
 * @author lxy
 */
public record AgentChatRequest(
        @NotBlank String message,
        @JsonProperty("session_id") @NotBlank String sessionId,
        @JsonProperty("workspace_id") @NotBlank String workspaceId
) {
}
