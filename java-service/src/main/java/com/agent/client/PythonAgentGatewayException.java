package com.agent.client;

/**
 * Python Agent 不可达或返回非成功响应时的网关异常。
 *
 * @author lxy
 */
public class PythonAgentGatewayException extends RuntimeException {

    private final Integer upstreamStatus;

    public PythonAgentGatewayException(String message, Integer upstreamStatus) {
        super(message);
        this.upstreamStatus = upstreamStatus;
    }

    public PythonAgentGatewayException(String message, Throwable cause) {
        super(message, cause);
        this.upstreamStatus = null;
    }

    public Integer getUpstreamStatus() {
        return upstreamStatus;
    }
}
