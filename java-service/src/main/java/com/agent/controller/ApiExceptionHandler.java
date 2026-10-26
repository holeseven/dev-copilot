package com.agent.controller;

import com.agent.client.PythonAgentGatewayException;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Java Agent 网关的统一 HTTP 错误映射。
 *
 * @author lxy
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(PythonAgentGatewayException.class)
    public ResponseEntity<ProblemDetail> handleGatewayFailure(PythonAgentGatewayException exception) {
        HttpStatusCode status = gatewayStatus(exception.getUpstreamStatus());
        String detail = safeDetail(status.value());
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, detail);
        problem.setTitle(status.value() == HttpStatus.BAD_GATEWAY.value()
                ? "Python Agent unavailable"
                : "Python Agent rejected the request");
        return ResponseEntity.status(status).body(problem);
    }

    private HttpStatusCode gatewayStatus(Integer upstreamStatus) {
        if (upstreamStatus != null && upstreamStatus >= 400 && upstreamStatus < 500) {
            return HttpStatusCode.valueOf(upstreamStatus);
        }
        return HttpStatus.BAD_GATEWAY;
    }

    private String safeDetail(int status) {
        return switch (status) {
            case 404 -> "请求的 Workspace 不存在";
            case 409 -> "Workspace 当前不可用";
            case 422 -> "请求参数或 Workspace 无效";
            case 502 -> "小瑶暂时无法连接 Agent 引擎，请稍后重试";
            default -> "Python Agent 拒绝了请求";
        };
    }
}
