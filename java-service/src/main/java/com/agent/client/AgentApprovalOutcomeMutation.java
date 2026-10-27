package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Objects;

/**
 * 可公开的审批写入结果；不包含宿主路径或源文件内容。
 *
 * @author lxy
 */
public record AgentApprovalOutcomeMutation(
        @JsonProperty(value = "proposal_id", required = true) String proposalId,
        @JsonProperty(value = "workspace_id", required = true) String workspaceId,
        @JsonProperty(value = "virtual_path", required = true) String virtualPath,
        @JsonProperty(value = "operation", required = true) String operation,
        @JsonProperty(value = "before_sha256", required = true) String beforeSha256,
        @JsonProperty(value = "after_sha256", required = true) String afterSha256
) {
    public AgentApprovalOutcomeMutation {
        Objects.requireNonNull(proposalId, "proposal_id must not be null");
        Objects.requireNonNull(workspaceId, "workspace_id must not be null");
        Objects.requireNonNull(virtualPath, "virtual_path must not be null");
        Objects.requireNonNull(operation, "operation must not be null");
        Objects.requireNonNull(afterSha256, "after_sha256 must not be null");
        if (!operation.equals("create") && !operation.equals("replace")) {
            throw new IllegalArgumentException("unsupported approval mutation operation");
        }
        if (!safeVirtualPath(virtualPath)) {
            throw new IllegalArgumentException("unsafe approval mutation path");
        }
        if (!sha256(afterSha256) || (beforeSha256 != null && !sha256(beforeSha256))) {
            throw new IllegalArgumentException("invalid approval mutation hash");
        }
        if ((operation.equals("create") && beforeSha256 != null)
                || (operation.equals("replace") && beforeSha256 == null)) {
            throw new IllegalArgumentException("before hash does not match mutation operation");
        }
    }

    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown approval mutation field: " + field);
    }

    private static boolean sha256(String value) {
        return value.matches("(?i)[a-f0-9]{64}");
    }

    private static boolean safeVirtualPath(String value) {
        if (value.isBlank() || value.length() > 1024 || value.startsWith("/")
                || value.contains("\\") || value.matches("^[A-Za-z][A-Za-z0-9+.-]*:.*")
                || value.chars().anyMatch(character -> character < 32 || character == 127)) {
            return false;
        }
        for (String part : value.split("/", -1)) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                return false;
            }
        }
        return true;
    }
}
