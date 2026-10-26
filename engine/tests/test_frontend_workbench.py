from fastapi.testclient import TestClient

from engine.main import create_app


def test_homepage_exposes_workspace_workbench_and_scoped_chat_request():
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="workspace-select"' in html
    assert 'id="workspace-id"' in html
    assert 'id="workspace-name"' in html
    assert 'id="repository-name"' in html
    assert 'id="repository-path"' in html
    assert 'id="scan-btn"' in html
    assert 'id="workspace-guide"' in html
    assert "workspace_id: currentWorkspaceId" in html
    assert "fetch('/tools?workspace_id='" in html
    assert "async function consumeEventStream" in html
    assert "await consumeEventStream(response" in html
    assert html.index("if (!response.ok)") < html.index("response.body.getReader()")
    assert "currentEvent === 'message' && data.token" in html
    assert "if (!receivedMessageToken)" in html
