package com.agent.client;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * RAG 索引生命周期状态。
 *
 * @author lxy
 */
public enum RagIndexState {
    NOT_INDEXED("not_indexed"),
    QUEUED("queued"),
    BUILDING("building"),
    READY("ready"),
    STALE("stale"),
    FAILED("failed");

    private final String value;

    RagIndexState(String value) {
        this.value = value;
    }

    @JsonCreator
    public static RagIndexState fromValue(String value) {
        for (RagIndexState state : values()) {
            if (state.value.equals(value)) {
                return state;
            }
        }
        throw new IllegalArgumentException("Unknown RAG index state");
    }

    @JsonValue
    public String value() {
        return value;
    }
}
