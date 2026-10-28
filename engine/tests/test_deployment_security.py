# @author lxy
"""Static security contracts for the local container deployment."""

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_compose() -> dict:
    return yaml.safe_load((REPOSITORY_ROOT / "docker-compose.yml").read_text("utf-8"))


def load_write_override() -> dict:
    return yaml.safe_load(
        (REPOSITORY_ROOT / "docker-compose.write.yml").read_text("utf-8")
    )


def test_datastores_are_private_on_an_internal_backend_network() -> None:
    compose = load_compose()
    services = compose["services"]

    assert compose["networks"]["backend"]["internal"] is True
    for service_name in ("redis", "mysql"):
        service = services[service_name]
        assert "ports" not in service
        assert service["networks"] == ["backend"]


def test_java_is_the_only_public_application_api() -> None:
    services = load_compose()["services"]

    assert "ports" not in services["engine"]
    assert set(services["engine"]["networks"]) == {"backend", "egress"}
    assert services["java-service"]["ports"] == ["127.0.0.1:8081:8081"]
    assert set(services["java-service"]["networks"]) == {"backend", "frontend"}
    assert services["java-service"]["environment"][
        "DEV_COPILOT_ENGINE_BASE_URL"
    ] == "http://engine:8000"
    assert services["java-service"]["depends_on"]["engine"] == {
        "condition": "service_healthy"
    }
    assert services["web"]["ports"] == ["127.0.0.1:3000:8080"]
    assert services["web"]["networks"] == ["frontend"]
    assert "frontend" not in services["engine"]["networks"]
    assert services["web"]["environment"]["JAVA_BASE_URL"] == (
        "http://java-service:8081"
    )
    assert services["web"]["depends_on"]["java-service"] == {
        "condition": "service_healthy"
    }
    assert services["engine"]["healthcheck"]["test"][0:3] == [
        "CMD",
        "python",
        "-c",
    ]
    assert "127.0.0.1:8000/health" in services["engine"]["healthcheck"]["test"][3]
    java_healthcheck = services["java-service"]["healthcheck"]
    assert java_healthcheck["test"] == [
        "CMD",
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "http://127.0.0.1:8081/actuator/health",
    ]
    assert java_healthcheck["start_period"] == "20s"

    java_dockerfile = (REPOSITORY_ROOT / "java-service/Dockerfile").read_text("utf-8")
    assert "apt-get install -y --no-install-recommends curl" in java_dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in java_dockerfile

    pom = (REPOSITORY_ROOT / "java-service/pom.xml").read_text("utf-8")
    assert "spring-boot-starter-actuator" in pom
    application = yaml.safe_load(
        (REPOSITORY_ROOT / "java-service/src/main/resources/application.yml").read_text(
            "utf-8"
        )
    )
    assert application["management"]["endpoints"]["web"]["exposure"]["include"] == (
        "health"
    )
    assert application["management"]["endpoint"]["health"]["show-details"] == (
        "never"
    )


def test_mysql_credentials_must_be_injected_and_java_never_defaults_to_root() -> None:
    compose = load_compose()
    mysql_environment = compose["services"]["mysql"]["environment"]
    java_environment = compose["services"]["java-service"]["environment"]
    application = yaml.safe_load(
        (REPOSITORY_ROOT / "java-service/src/main/resources/application.yml").read_text(
            "utf-8"
        )
    )

    assert mysql_environment["MYSQL_ROOT_PASSWORD"] == (
        "${DEV_COPILOT_MYSQL_ROOT_PASSWORD:?required}"
    )
    assert mysql_environment["MYSQL_PASSWORD"] == "${DEV_COPILOT_DB_PASSWORD:?required}"
    assert mysql_environment["MYSQL_USER"] == "${DEV_COPILOT_DB_USERNAME:-dev_copilot}"
    assert java_environment["SPRING_DATASOURCE_USERNAME"] == (
        "${DEV_COPILOT_DB_USERNAME:-dev_copilot}"
    )
    assert java_environment["SPRING_DATASOURCE_PASSWORD"] == (
        "${DEV_COPILOT_DB_PASSWORD:?required}"
    )
    assert application["spring"]["datasource"]["username"] == (
        "${DEV_COPILOT_DB_USERNAME:dev_copilot}"
    )
    assert application["spring"]["datasource"]["password"] == (
        "${DEV_COPILOT_DB_PASSWORD}"
    )
    assert "root/root" not in (REPOSITORY_ROOT / "docker-compose.yml").read_text("utf-8")


def test_engine_mounts_only_the_selected_workspace_and_persists_metadata() -> None:
    engine = load_compose()["services"]["engine"]
    volumes = engine["volumes"]

    assert {
        "type": "bind",
        "source": "${DEV_COPILOT_ALLOWED_ROOT:?required}",
        "target": "/workspace",
        "read_only": True,
    } in volumes
    assert {
        "type": "volume",
        "source": "engine-metadata",
        "target": "/data/dev-copilot",
    } in volumes
    assert engine["environment"]["WORKSPACE_ALLOWED_ROOTS"] == '["/workspace"]'
    assert engine["environment"]["WORKSPACE_METADATA_DIR"] == "/data/dev-copilot"
    assert engine["environment"]["WORKSPACE_WRITE_ENABLED"] == "false"


def test_writable_development_override_is_explicit_and_scoped_to_engine() -> None:
    override = load_write_override()

    assert set(override) == {"services"}
    assert set(override["services"]) == {"engine"}
    engine = override["services"]["engine"]
    assert engine["build"] == {"target": "write-runtime"}
    assert engine["environment"] == {"WORKSPACE_WRITE_ENABLED": "true"}
    assert engine["volumes"] == [
        {
            "type": "bind",
            "source": "${DEV_COPILOT_ALLOWED_ROOT:?required}",
            "target": "/workspace",
            "read_only": False,
        }
    ]
    assert "ports" not in engine
    assert "privileged" not in engine


def test_write_runtime_provides_fixed_multilanguage_test_tools() -> None:
    compose_engine = load_compose()["services"]["engine"]
    assert compose_engine["build"]["target"] == "runtime"

    dockerfile = (REPOSITORY_ROOT / "engine/Dockerfile").read_text("utf-8")
    assert "FROM node:20.19-bookworm-slim AS node-toolchain" in dockerfile
    assert "FROM python:3.12-slim-bookworm AS runtime" in dockerfile
    assert "FROM runtime AS write-runtime" in dockerfile
    assert "openjdk-17-jdk-headless" in dockerfile
    assert "maven" in dockerfile
    assert "COPY --from=node-toolchain /usr/local/bin/node" in dockerfile
    assert "uv sync --frozen --extra dev" in dockerfile
    assert dockerfile.rstrip().endswith("FROM runtime AS default")


def test_every_supported_engine_setting_is_explicitly_forwarded() -> None:
    compose_environment = load_compose()["services"]["engine"]["environment"]
    example_lines = (REPOSITORY_ROOT / ".env.example").read_text("utf-8").splitlines()
    example_settings = {
        line.split("=", 1)[0].strip()
        for line in example_lines
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    supported_settings = {
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_MODEL_L2",
        "LLM_TEMPERATURE",
        "LLM_TIMEOUT",
        "REDIS_MAX_CONNECTIONS",
        "DYNAMIC_CONFIG_KEY",
        "CONFIG_REFRESH_INTERVAL",
        "APP_NAME",
        "LOG_LEVEL",
    }

    assert supported_settings <= example_settings
    assert supported_settings <= compose_environment.keys()
    for setting in supported_settings:
        assert f"${{{setting}" in str(compose_environment[setting])
    assert compose_environment["LLM_API_KEY"] == "${LLM_API_KEY:?required}"


def test_database_name_matches_the_fixed_schema_contract() -> None:
    compose = load_compose()
    mysql_environment = compose["services"]["mysql"]["environment"]
    java_url = compose["services"]["java-service"]["environment"][
        "SPRING_DATASOURCE_URL"
    ]
    schema = (
        REPOSITORY_ROOT / "java-service/src/main/resources/db/schema.sql"
    ).read_text("utf-8")
    example = (REPOSITORY_ROOT / ".env.example").read_text("utf-8")

    assert "CREATE DATABASE IF NOT EXISTS agent_db" in schema
    assert "USE agent_db;" in schema
    assert mysql_environment["MYSQL_DATABASE"] == "agent_db"
    assert "mysql:3306/agent_db?" in java_url
    assert "DEV_COPILOT_DB_NAME" not in java_url
    assert "DEV_COPILOT_DB_NAME" not in example


def test_application_containers_are_read_only_and_images_run_as_non_root() -> None:
    compose = load_compose()
    for service_name in ("engine", "java-service", "web"):
        service = compose["services"][service_name]
        assert service["read_only"] is True
        assert service["tmpfs"] == ["/tmp"]

    for dockerfile in ("engine/Dockerfile", "java-service/Dockerfile", "web/Dockerfile"):
        text = (REPOSITORY_ROOT / dockerfile).read_text("utf-8")
        user_lines = [line.strip() for line in text.splitlines() if line.startswith("USER ")]
        assert user_lines
        assert user_lines[-1].casefold() not in {"user root", "user 0"}


def test_healthchecks_do_not_embed_password_literals() -> None:
    compose = load_compose()
    mysql_healthcheck = compose["services"]["mysql"]["healthcheck"]["test"]
    serialized = " ".join(mysql_healthcheck)

    assert mysql_healthcheck[0] == "CMD-SHELL"
    assert "$${MYSQL_ROOT_PASSWORD}" in serialized
    assert "-proot" not in serialized


def test_example_environment_contains_no_secret_defaults() -> None:
    lines = (REPOSITORY_ROOT / ".env.example").read_text("utf-8").splitlines()
    values = {
        key.strip(): value.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }

    assert values["DEV_COPILOT_MYSQL_ROOT_PASSWORD"] == ""
    assert values["DEV_COPILOT_DB_PASSWORD"] == ""
    assert values["LLM_API_KEY"] == ""
    assert values["DEV_COPILOT_DB_USERNAME"] == "dev_copilot"
    assert "root/root" not in "\n".join(lines).casefold()
