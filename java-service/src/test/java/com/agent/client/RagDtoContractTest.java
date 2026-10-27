package com.agent.client;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * @author lxy
 */
class RagDtoContractTest {

    private final ObjectMapper lenientMapper = new ObjectMapper()
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);

    @Test
    void allRagDtosRejectUnknownJsonEvenWithLenientGlobalMapper() {
        assertThatThrownBy(() -> lenientMapper.readValue("""
                {"query":"q","top_k":1,"secret":"x"}
                """, RagQueryRequest.class)).isInstanceOf(Exception.class);
        assertThatThrownBy(() -> lenientMapper.readValue("""
                {"workspace_id":"alpha","state":"ready","index_generation":"rag-1",
                 "job_id":null,"repository_count":1,"file_count":1,"chunk_count":1,
                 "updated_at":null,"error_code":null,"secret":"x"}
                """, RagIndexStatusResponse.class)).isInstanceOf(Exception.class);
        assertThatThrownBy(() -> lenientMapper.readValue("""
                {"workspace_id":"alpha","index_generation":"rag-1","citations":[],"secret":"x"}
                """, RagQueryResponse.class)).isInstanceOf(Exception.class);
        assertThatThrownBy(() -> lenientMapper.readValue("""
                {"citation_id":"cite-1","rank":1,"repository":"service",
                 "virtual_path":"service/docs/a.md","start_line":1,"end_line":2,
                 "excerpt":"safe","content_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "secret":"x"}
                """, RagCitation.class)).isInstanceOf(Exception.class);
    }

    @Test
    void queryResponseRejectsEveryMissingRequiredProperty() throws Exception {
        var valid = (com.fasterxml.jackson.databind.node.ObjectNode) lenientMapper.readTree("""
                {"workspace_id":"alpha","index_generation":"rag-1","citations":[]}
                """);
        for (String field : java.util.List.of("workspace_id", "index_generation", "citations")) {
            var missing = valid.deepCopy();
            missing.remove(field);
            assertThatThrownBy(() -> lenientMapper.treeToValue(missing, RagQueryResponse.class))
                    .as("missing %s", field)
                    .isInstanceOf(Exception.class);
        }
    }
}
