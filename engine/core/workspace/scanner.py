from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from engine.core.workspace.models import (
    DependencyRelation,
    GitInfo,
    Repository,
    RepositoryScan,
    ScanEvidence,
)
from engine.core.workspace.path_guard import (
    GuardedRepository,
    WorkspacePathError,
    WorkspacePathGuard,
)


_MIDDLEWARE = {
    "kafka": "Kafka",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "postgres": "PostgreSQL",
    "rabbitmq": "RabbitMQ",
    "redis": "Redis",
}


class WorkspaceScanner:
    """Collect deterministic facts through a guarded repository capability."""

    def __init__(self, path_guard: WorkspacePathGuard):
        self.path_guard = path_guard

    def scan(self, repository: Repository) -> RepositoryScan:
        guarded = self.path_guard.bind(repository)
        languages: set[str] = set()
        build_systems: set[str] = set()
        services: set[str] = set()
        middleware: set[str] = set()
        dependencies: List[DependencyRelation] = []
        evidence: Dict[tuple[str, str], ScanEvidence] = {}

        def record(source: str, detail: str) -> ScanEvidence:
            item = ScanEvidence(source=source, detail=detail)
            evidence[(source, detail)] = item
            return item

        git = self._scan_git(guarded, record)
        self._scan_package_json(guarded, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_pom(guarded, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_gradle(guarded, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_pyproject(guarded, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_application(guarded, services, middleware, record)
        self._scan_compose(guarded, services, middleware, dependencies, record)

        return RepositoryScan(
            repository=repository.name,
            git=git,
            languages=sorted(languages),
            build_systems=sorted(build_systems),
            services=sorted(services),
            middleware=sorted(middleware),
            dependencies=sorted(
                dependencies,
                key=lambda item: (item.source, item.target, item.evidence.source, item.evidence.detail),
            ),
            evidence=sorted(evidence.values(), key=lambda item: (item.source, item.detail)),
        )

    def _scan_git(self, guarded: GuardedRepository, record) -> GitInfo:
        branch = ""
        head = self._read_optional(guarded, (".git/HEAD",))
        if head is not None:
            value = head[1].strip()
            if value.startswith("ref: refs/heads/"):
                branch = value.removeprefix("ref: refs/heads/")
                record(".git/HEAD", "branch")
        config_source = self._read_optional(guarded, (".git/config",))
        if config_source is None:
            return GitInfo(branch=branch)
        config = ConfigParser()
        config.read_string(config_source[1])
        remote = config.get('remote "origin"', "url", fallback="")
        if remote:
            record(".git/config", "remote.origin.url")
        return GitInfo(branch=branch, remote=remote)

    def _scan_package_json(self, guarded, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        source = self._read_optional(guarded, ("package.json",))
        if source is None:
            return
        try:
            package = json.loads(source[1])
        except json.JSONDecodeError:
            return
        languages.add("JavaScript")
        build_systems.add("npm")
        service = package.get("name") or fallback
        services.add(service)
        record(source[0], "name")
        for dependency_name in sorted({**package.get("dependencies", {}), **package.get("devDependencies", {})}):
            item = record(source[0], f"dependency:{dependency_name}")
            dependencies.append(DependencyRelation(source=service, target=dependency_name, evidence=item))
            self._add_middleware(dependency_name, middleware)

    def _scan_pom(self, guarded, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        source = self._read_optional(guarded, ("pom.xml",))
        if source is None:
            return
        try:
            project = ET.fromstring(source[1])
        except ET.ParseError:
            return
        languages.add("Java")
        build_systems.add("Maven")
        artifact = self._find_xml_text(project, "artifactId") or fallback
        services.add(artifact)
        record(source[0], "artifactId")
        for dependency in project.findall(".//{*}dependency"):
            group = self._find_xml_text(dependency, "groupId")
            name = self._find_xml_text(dependency, "artifactId")
            if not name:
                continue
            target = f"{group}:{name}" if group else name
            item = record(source[0], f"dependency:{target}")
            dependencies.append(DependencyRelation(source=artifact, target=target, evidence=item))
            self._add_middleware(target, middleware)

    def _scan_gradle(self, guarded, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        source = self._read_optional(guarded, ("build.gradle", "build.gradle.kts"))
        if source is None:
            return
        text = source[1]
        build_systems.add("Gradle")
        if re.search(r"\b(java|org\.springframework\.boot)\b", text):
            languages.add("Java")
        if "kotlin" in text.lower():
            languages.add("Kotlin")
        services.add(fallback)
        record(source[0], "build script")
        for target in sorted(set(re.findall(r"[\"']([\w.-]+:[\w.-]+(?::[^\"']+)*)[\"']", text))):
            item = record(source[0], f"dependency:{target}")
            dependencies.append(DependencyRelation(source=fallback, target=target, evidence=item))
            self._add_middleware(target, middleware)

    def _scan_pyproject(self, guarded, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        source = self._read_optional(guarded, ("pyproject.toml",))
        if source is None:
            return
        try:
            config = tomllib.loads(source[1])
        except tomllib.TOMLDecodeError:
            return
        languages.add("Python")
        build_systems.add("pyproject")
        project = config.get("project", {})
        service = project.get("name") or fallback
        services.add(service)
        record(source[0], "project.name")
        for dependency in project.get("dependencies", []):
            target = re.split(r"[<>=!~\[ ;]", dependency, maxsplit=1)[0]
            if not target:
                continue
            item = record(source[0], f"dependency:{target}")
            dependencies.append(DependencyRelation(source=service, target=target, evidence=item))
            self._add_middleware(target, middleware)

    def _scan_application(self, guarded, services, middleware, record) -> None:
        source = self._read_optional(guarded, ("application.yml", "application.yaml"))
        if source is None:
            return
        try:
            config = yaml.safe_load(source[1]) or {}
        except yaml.YAMLError:
            return
        spring = config.get("spring", {}) if isinstance(config, dict) else {}
        application = spring.get("application", {}) if isinstance(spring, dict) else {}
        name = application.get("name") if isinstance(application, dict) else None
        if name:
            services.add(str(name))
            record(source[0], "spring.application.name")
        for key, value in self._flatten_items(config):
            self._add_middleware(key, middleware)
            if isinstance(value, str):
                self._add_middleware(value, middleware)
            if self._matches_middleware(key) or (isinstance(value, str) and self._matches_middleware(value)):
                record(source[0], key)

    def _scan_compose(self, guarded, services, middleware, dependencies, record) -> None:
        source = self._read_optional(guarded, ("docker-compose.yml", "docker-compose.yaml"))
        if source is None:
            return
        try:
            config = yaml.safe_load(source[1]) or {}
        except yaml.YAMLError:
            return
        service_config = config.get("services", {}) if isinstance(config, dict) else {}
        if not isinstance(service_config, dict):
            return
        for name in sorted(service_config):
            services.add(name)
            record(source[0], f"service:{name}")
            definition = service_config[name] or {}
            if not isinstance(definition, dict):
                continue
            self._add_middleware(str(definition.get("image", "")), middleware)
            depends_on = definition.get("depends_on", [])
            targets = depends_on.keys() if isinstance(depends_on, dict) else depends_on
            for target in sorted(str(target) for target in targets):
                item = record(source[0], f"depends_on:{name}->{target}")
                dependencies.append(DependencyRelation(source=name, target=target, evidence=item))

    @staticmethod
    def _read_optional(
        guarded: GuardedRepository, candidates: Sequence[str]
    ) -> Optional[tuple[str, str]]:
        for candidate in candidates:
            path = guarded.resolve(candidate, kind="file", must_exist=False)
            try:
                path.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise WorkspacePathError(
                    f"cannot inspect repository candidate: {candidate}"
                ) from exc
            return candidate, guarded.read_text(candidate)
        return None

    @staticmethod
    def _find_xml_text(element: ET.Element, name: str) -> str:
        child = element.find(f"{{*}}{name}")
        return child.text.strip() if child is not None and child.text else ""

    @staticmethod
    def _flatten_items(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
        if isinstance(value, dict):
            for key, nested in value.items():
                full_key = f"{prefix}.{key}" if prefix else str(key)
                yield full_key, nested
                yield from WorkspaceScanner._flatten_items(nested, full_key)

    @staticmethod
    def _add_middleware(value: str, middleware: set[str]) -> None:
        for keyword, label in _MIDDLEWARE.items():
            if keyword in value.lower():
                middleware.add(label)

    @staticmethod
    def _matches_middleware(value: str) -> bool:
        lowered = value.lower()
        return any(keyword in lowered for keyword in _MIDDLEWARE)
