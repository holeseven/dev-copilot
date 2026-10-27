package com.agent.client;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 可安全展示给审批人的代码变更字段。
 *
 * @author lxy
 */
public record AgentApprovalPayload(
        @JsonProperty("tool_name") String toolName,
        String operation,
        @JsonProperty("workspace_id") String workspaceId,
        String repository,
        @JsonProperty("virtual_path") String virtualPath,
        String diff,
        @JsonProperty("before_sha256") String beforeSha256,
        @JsonProperty("after_sha256") String afterSha256,
        @JsonProperty("patch_sha256") String patchSha256,
        @JsonProperty("validation_profile") String validationProfile
) {
}
