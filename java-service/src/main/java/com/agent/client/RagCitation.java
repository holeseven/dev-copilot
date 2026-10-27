package com.agent.client;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.regex.Pattern;

/**
 * 可安全返回给浏览器的 RAG 引用。
 *
 * @author lxy
 */
public record RagCitation(
        @JsonProperty("citation_id") String citationId,
        int rank,
        String repository,
        @JsonProperty("virtual_path") String virtualPath,
        @JsonProperty("start_line") int startLine,
        @JsonProperty("end_line") int endLine,
        String excerpt,
        @JsonProperty("content_sha256") String contentSha256
) {
    private static final int MAX_EXCERPT_LENGTH = 2000;
    private static final Pattern SAFE_REPOSITORY = Pattern.compile(
            "[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
    );
    private static final Pattern SHA256 = Pattern.compile("[0-9a-fA-F]{64}");
    private static final Pattern SAFE_CITATION_ID = Pattern.compile("[A-Za-z0-9_-]{1,64}");
    private static final Pattern URI_SCHEME = Pattern.compile("^[A-Za-z][A-Za-z0-9+.-]*:.*");

    public RagCitation {
        requireText(citationId, "citation_id");
        requireText(repository, "repository");
        requireText(virtualPath, "virtual_path");
        requireText(excerpt, "excerpt");
        requireText(contentSha256, "content_sha256");
        if (rank < 1 || startLine < 1 || endLine < startLine) {
            throw new IllegalArgumentException("Invalid RAG citation position");
        }
        if (!SAFE_CITATION_ID.matcher(citationId).matches()) {
            throw new IllegalArgumentException("Invalid RAG citation citation_id");
        }
        if (!SAFE_REPOSITORY.matcher(repository).matches()
                || repository.equals(".") || repository.equals("..")) {
            throw new IllegalArgumentException("Invalid RAG citation repository");
        }
        if (!isSafePosixRelativePath(repository, virtualPath)) {
            throw new IllegalArgumentException("Invalid RAG citation virtual_path");
        }
        if (!SHA256.matcher(contentSha256).matches()) {
            throw new IllegalArgumentException("Invalid RAG citation content_sha256");
        }
        if (excerpt.length() > MAX_EXCERPT_LENGTH) {
            throw new IllegalArgumentException("RAG citation excerpt is too long");
        }
    }

    private static void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }

    private static boolean isSafePosixRelativePath(String repository, String value) {
        if (value.startsWith("/") || value.contains("\\")
                || URI_SCHEME.matcher(value).matches() || hasUnsafeUnicode(value)) {
            return false;
        }
        String[] segments = value.split("/", -1);
        if (segments.length < 2 || !segments[0].equals(repository)) {
            return false;
        }
        for (String segment : segments) {
            if (segment.isEmpty() || segment.equals(".") || segment.equals("..")) {
                return false;
            }
            for (int index = 0; index < segment.length(); index++) {
                if (Character.isISOControl(segment.charAt(index))) {
                    return false;
                }
            }
        }
        return true;
    }

    private static boolean hasUnsafeUnicode(String value) {
        return value.matches(".*[\\u202A-\\u202E\\u2066-\\u2069].*");
    }

    @JsonAnySetter
    public void rejectUnknownField(String field, Object value) {
        throw new IllegalArgumentException("Unknown RAG citation field: " + field);
    }
}
