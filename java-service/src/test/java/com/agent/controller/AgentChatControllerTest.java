package com.agent.controller;

import com.agent.client.AgentChatRequest;
import com.agent.client.AgentApprovalPayload;
import com.agent.client.AgentPendingApproval;
import com.agent.client.AgentPendingApprovalRequest;
import com.agent.client.AgentPendingApprovalResponse;
import com.agent.client.AgentPendingApprovalStatus;
import com.agent.client.AgentResumeRequest;
import com.agent.client.PythonAgentClient;
import com.agent.client.PythonAgentGatewayException;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.validation.beanvalidation.LocalValidatorFactoryBean;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

/**
 * @author lxy
 */
class AgentChatControllerTest {

    @Test
    void exposesVersionedSseEndpoint() throws Exception {
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.just(
                ServerSentEvent.<String>builder("{\"status\":\"completed\"}")
                        .event("done")
                        .build()
        ));
        MockMvc mvc = standaloneSetup(new AgentChatController(client))
                .setControllerAdvice(new ApiExceptionHandler())
                .build();

        MvcResult pending = mvc.perform(post("/api/v1/agent/chat")
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.TEXT_EVENT_STREAM)
                        .content("""
                                {"message":"hello","session_id":"session-1","workspace_id":"workspace-1"}
                                """))
                .andExpect(request().asyncStarted())
                .andReturn();

        mvc.perform(asyncDispatch(pending))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
                .andExpect(content().string("event:done\ndata:{\"status\":\"completed\"}\n\n"));
    }

    @Test
    void exposesVersionedResumeSseEndpoint() throws Exception {
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.just(
                ServerSentEvent.<String>builder("{\"status\":\"completed\"}").event("done").build()
        ));
        MockMvc mvc = standaloneSetup(new AgentChatController(client)).build();

        MvcResult pending = mvc.perform(post("/api/v1/agent/chat/resume")
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.TEXT_EVENT_STREAM)
                        .content("""
                                {"session_id":"session-1","workspace_id":"workspace-1","approval_id":"approval-1","approved":false,"reason":"not safe"}
                                """))
                .andExpect(request().asyncStarted())
                .andReturn();

        mvc.perform(asyncDispatch(pending))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM));
        assertThat(client.resumeCalls).isEqualTo(1);
        assertThat(client.resumeRequest).isEqualTo(
                new AgentResumeRequest("session-1", "workspace-1", "approval-1", false, "not safe"));
    }

    @Test
    void exposesVersionedPendingApprovalEndpoint() throws Exception {
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.empty());
        client.pendingResult = new AgentPendingApprovalResponse(
                "workspace-1", "session-1", AgentPendingApprovalStatus.AWAITING_APPROVAL, List.of(
                new AgentPendingApproval(
                        "approval-1",
                        "apply_workspace_change",
                        "请确认修改",
                        new AgentApprovalPayload(
                                "apply_workspace_change", "replace", "workspace-1", "service",
                                "service/app.py", "diff", "before", "after", "patch", "python_pytest")
                )
        ));
        MockMvc mvc = standaloneSetup(new AgentChatController(client))
                .setControllerAdvice(new ApiExceptionHandler())
                .build();

        MvcResult pending = mvc.perform(post("/api/v1/agent/chat/pending")
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.APPLICATION_JSON)
                        .content("""
                                {"session_id":"session-1","workspace_id":"workspace-1"}
                                """))
                .andExpect(request().asyncStarted())
                .andReturn();

        mvc.perform(asyncDispatch(pending))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.workspace_id").value("workspace-1"))
                .andExpect(jsonPath("$.session_id").value("session-1"))
                .andExpect(jsonPath("$.status").value("awaiting_approval"))
                .andExpect(jsonPath("$.approvals[0].approval_id").value("approval-1"))
                .andExpect(jsonPath("$.approvals[0].payload.virtual_path").value("service/app.py"))
                .andExpect(jsonPath("$.approvals[0].payload.validation_profile").value("python_pytest"));
        assertThat(client.pendingCalls).isEqualTo(1);
        assertThat(client.pendingRequest).isEqualTo(
                new AgentPendingApprovalRequest("session-1", "workspace-1"));
    }

    @Test
    void rejectsInvalidPendingApprovalRequestBeforeCallingEngine() throws Exception {
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.empty());
        LocalValidatorFactoryBean validator = new LocalValidatorFactoryBean();
        validator.afterPropertiesSet();
        MockMvc mvc = standaloneSetup(new AgentChatController(client))
                .setControllerAdvice(new ApiExceptionHandler())
                .setValidator(validator)
                .build();

        mvc.perform(post("/api/v1/agent/chat/pending")
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.APPLICATION_JSON)
                        .content("""
                                {"session_id":" ","workspace_id":""}
                                """))
                .andExpect(status().isBadRequest());

        assertThat(client.pendingCalls).isZero();
    }

    @Test
    void resumeUsesTheSameSafeFailureSemantics() {
        ServerSentEvent<String> message = ServerSentEvent.<String>builder("{\"token\":\"continuing\"}")
                .event("message").build();
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.concat(
                Flux.just(message), Flux.error(new IllegalStateException("secret"))
        ));

        List<ServerSentEvent<String>> events = new AgentChatController(client)
                .resume(new AgentResumeRequest("session-1", "workspace-1", "approval-1", true, null))
                .collectList().block();

        assertThat(events).extracting(ServerSentEvent::event)
                .containsExactly("message", "error", "done");
        assertThat(events.get(1).data()).doesNotContain("secret");
        assertThat(events.get(2).data()).isEqualTo("{\"status\":\"failed\"}");
    }

    @Test
    void rejectsBlankRequiredFieldsBeforeCallingEngine() throws Exception {
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.error(
                new AssertionError("engine must not be called")));
        LocalValidatorFactoryBean validator = new LocalValidatorFactoryBean();
        validator.afterPropertiesSet();
        MockMvc mvc = standaloneSetup(new AgentChatController(client))
                .setControllerAdvice(new ApiExceptionHandler())
                .setValidator(validator)
                .build();

        mvc.perform(post("/api/v1/agent/chat")
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.TEXT_EVENT_STREAM)
                        .content("""
                                {"message":" ","session_id":"","workspace_id":""}
                                """))
                .andExpect(status().isBadRequest());

        assertThat(client.calls).isZero();
    }

    @Test
    void forwardsEventsAndConvertsAnErrorAfterStreamingStarted() {
        ServerSentEvent<String> message = ServerSentEvent.<String>builder()
                .event("message")
                .data("{\"token\":\"hi\"}")
                .build();
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.concat(
                Flux.just(message),
                Flux.error(new IllegalStateException("stream dropped"))
        ));

        List<ServerSentEvent<String>> events = new AgentChatController(client)
                .chat(validRequest())
                .collectList()
                .block();

        assertThat(events).extracting(ServerSentEvent::event)
                .containsExactly("message", "error", "done");
        assertThat(events.get(1).data()).contains("Agent 流中断");
        assertThat(events.get(2).data()).isEqualTo("{\"status\":\"failed\"}");
    }

    @Test
    void keepsAnErrorBeforeTheFirstEventForHttpExceptionMapping() {
        PythonAgentGatewayException failure = new PythonAgentGatewayException(
                "连接 Python Agent 失败", new IllegalStateException("offline"));
        AgentChatController controller = new AgentChatController(
                new StubPythonAgentClient(Flux.error(failure)));

        assertThatThrownBy(() -> controller.chat(validRequest()).blockLast())
                .isSameAs(failure);
    }

    @Test
    void mapsAnErrorBeforeTheFirstEventThroughMvcWithoutLeakingDetails() throws Exception {
        StubPythonAgentClient client = new StubPythonAgentClient(Flux.error(
                new PythonAgentGatewayException(
                        "secret upstream response body", new IllegalStateException("offline"))));
        MockMvc mvc = standaloneSetup(new AgentChatController(client))
                .setControllerAdvice(new ApiExceptionHandler())
                .build();

        MvcResult pending = mvc.perform(post("/api/v1/agent/chat")
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.TEXT_EVENT_STREAM)
                        .content("""
                                {"message":"hello","session_id":"session-1","workspace_id":"workspace-1"}
                                """))
                .andExpect(request().asyncStarted())
                .andReturn();

        mvc.perform(asyncDispatch(pending))
                .andExpect(status().isBadGateway())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON))
                .andExpect(jsonPath("$.detail").value("小瑶暂时无法连接 Agent 引擎，请稍后重试"))
                .andExpect(content().string(org.hamcrest.Matchers.not(
                        org.hamcrest.Matchers.containsString("secret upstream response body"))));
    }

    @Test
    void cancellationPropagatesBeforeAndAfterTheFirstEvent() {
        AtomicBoolean beforeFirstCancelled = new AtomicBoolean();
        AtomicBoolean afterFirstCancelled = new AtomicBoolean();
        Flux<ServerSentEvent<String>> never = Flux.<ServerSentEvent<String>>never()
                .doOnCancel(() -> beforeFirstCancelled.set(true));
        var beforeFirst = new AgentChatController(new StubPythonAgentClient(never))
                .chat(validRequest())
                .subscribe();

        beforeFirst.dispose();

        ServerSentEvent<String> message = ServerSentEvent.<String>builder("{\"token\":\"hi\"}")
                .event("message")
                .build();
        Flux<ServerSentEvent<String>> firstThenNever = Flux.concat(
                Flux.just(message),
                Flux.<ServerSentEvent<String>>never()
        ).doOnCancel(() -> afterFirstCancelled.set(true));

        new AgentChatController(new StubPythonAgentClient(firstThenNever))
                .resume(new AgentResumeRequest("session-1", "workspace-1", "approval-1", true, null))
                .take(1)
                .blockLast();

        assertThat(beforeFirstCancelled).isTrue();
        assertThat(afterFirstCancelled).isTrue();
    }

    @Test
    void mapsUpstreamClientErrorsAndConnectivityFailures() {
        ApiExceptionHandler handler = new ApiExceptionHandler();

        var rejected = handler.handleGatewayFailure(new PythonAgentGatewayException(
                "workspace missing", 422));
        var unavailable = handler.handleGatewayFailure(new PythonAgentGatewayException(
                "secret upstream failure", new IllegalStateException()));

        assertThat(rejected.getStatusCode().value()).isEqualTo(422);
        assertThat(rejected.getBody().getDetail()).contains("请求参数或 Workspace 无效");
        assertThat(rejected.getBody().getDetail()).doesNotContain("workspace missing");
        assertThat(unavailable.getStatusCode().value()).isEqualTo(502);
        assertThat(unavailable.getBody().getDetail()).contains("小瑶暂时无法连接 Agent 引擎");
        assertThat(unavailable.getBody().getDetail()).doesNotContain("secret upstream failure");
    }

    private AgentChatRequest validRequest() {
        return new AgentChatRequest("hello", "session-1", "workspace-1");
    }

    private static final class StubPythonAgentClient extends PythonAgentClient {
        private final Flux<ServerSentEvent<String>> result;
        private int calls;
        private int resumeCalls;
        private AgentResumeRequest resumeRequest;
        private int pendingCalls;
        private AgentPendingApprovalRequest pendingRequest;
        private AgentPendingApprovalResponse pendingResult = new AgentPendingApprovalResponse(
                "workspace-1", "session-1", AgentPendingApprovalStatus.IDLE, List.of());

        private StubPythonAgentClient(Flux<ServerSentEvent<String>> result) {
            super(WebClient.builder(), "http://127.0.0.1:1");
            this.result = result;
        }

        @Override
        public Flux<ServerSentEvent<String>> streamChat(AgentChatRequest request) {
            calls++;
            return result;
        }

        @Override
        public Flux<ServerSentEvent<String>> streamResume(AgentResumeRequest request) {
            resumeCalls++;
            resumeRequest = request;
            return result;
        }

        @Override
        public Mono<AgentPendingApprovalResponse> pendingApprovals(AgentPendingApprovalRequest request) {
            pendingCalls++;
            pendingRequest = request;
            return Mono.just(pendingResult);
        }
    }
}
