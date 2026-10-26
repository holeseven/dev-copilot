package com.agent.controller;

import com.agent.mapper.AgentSessionMapper;
import com.agent.model.AgentSessionDO;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * AgentDataController 的纯单元测试，不启动 Spring Context 或外部基础设施。
 *
 * @author lxy
 */
class AgentDataControllerTest {

    private AgentDataController controller;
    private SessionMapperStub sessionMapperStub;

    @BeforeEach
    void setUp() {
        controller = new AgentDataController();
        sessionMapperStub = new SessionMapperStub();
        AgentSessionMapper sessionMapper = sessionMapperStub.createProxy();
        ReflectionTestUtils.setField(controller, "sessionMapper", sessionMapper);
    }

    @Test
    void getSessionReturnsNotFoundWhenSessionDoesNotExist() {
        ResponseEntity<AgentSessionDO> response = controller.getSession("missing-session");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(response.getBody()).isNull();
        assertThat(sessionMapperStub.selectOneCalls).isEqualTo(1);
    }

    @Test
    void createSessionInitializesAuditFieldsAndInsertsTheSameObject() {
        AgentSessionDO session = new AgentSessionDO();
        session.setSessionId("session-1");
        session.setUserId("lxy");
        LocalDateTime before = LocalDateTime.now();

        ResponseEntity<AgentSessionDO> response = controller.createSession(session);

        LocalDateTime after = LocalDateTime.now();
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isSameAs(session);
        assertThat(session.getDeleted()).isZero();
        assertThat(session.getGmtCreate()).isBetween(before, after);
        assertThat(session.getGmtModified()).isBetween(before, after);
        assertThat(sessionMapperStub.insertedSession).isSameAs(session);
    }

    private static final class SessionMapperStub implements InvocationHandler {

        private int selectOneCalls;
        private AgentSessionDO insertedSession;

        private AgentSessionMapper createProxy() {
            return (AgentSessionMapper) Proxy.newProxyInstance(
                    AgentSessionMapper.class.getClassLoader(),
                    new Class<?>[]{AgentSessionMapper.class},
                    this
            );
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) {
            return switch (method.getName()) {
                case "selectOne" -> {
                    selectOneCalls++;
                    yield null;
                }
                case "insert" -> {
                    insertedSession = (AgentSessionDO) args[0];
                    yield 1;
                }
                case "toString" -> "SessionMapperStub";
                case "hashCode" -> System.identityHashCode(proxy);
                case "equals" -> proxy == args[0];
                default -> throw new UnsupportedOperationException(
                        "Unexpected mapper method: " + method.getName()
                );
            };
        }
    }
}
