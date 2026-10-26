from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from engine.core.workspace.models import DependencyRelation, GitInfo, Repository, RepositoryScan, ScanEvidence


_MIDDLEWARE = {
    "kafka": "Kafka",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "postgres": "PostgreSQL",
    "rabbitmq": "RabbitMQ",
    "redis": "Redis",
}


class WorkspaceScanner:
    """Collect deterministic repository facts from supported project files."""

    def scan(self, repository: Repository) -> RepositoryScan:
        root = Path(repository.path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"repository directory does not exist: {root}")

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

        git = self._scan_git(root, record)
        self._scan_package_json(root, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_pom(root, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_gradle(root, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_pyproject(root, repository.name, languages, build_systems, services, middleware, dependencies, record)
        self._scan_application(root, services, middleware, record)
        self._scan_compose(root, services, middleware, dependencies, record)

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

    def _scan_git(self, root: Path, record) -> GitInfo:
        config_file = root / ".git" / "config"
        head_file = root / ".git" / "HEAD"
        branch = ""
        if head_file.is_file():
            head = head_file.read_text(encoding="utf-8", errors="replace").strip()
            if head.startswith("ref: refs/heads/"):
                branch = head.removeprefix("ref: refs/heads/")
                record(".git/HEAD", "branch")
        if not config_file.is_file():
            return GitInfo(branch=branch)
        config = ConfigParser()
        config.read(config_file, encoding="utf-8")
        remote = config.get('remote "origin"', "url", fallback="")
        if remote:
            record(".git/config", "remote.origin.url")
        return GitInfo(branch=branch, remote=remote)

    def _scan_package_json(self, root, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        path = root / "package.json"
        if not path.is_file():
            return
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        languages.add("JavaScript")
        build_systems.add("npm")
        service = package.get("name") or fallback
        services.add(service)
        record("package.json", "name")
        for dependency_name in sorted({**package.get("dependencies", {}), **package.get("devDependencies", {})}):
            item = record("package.json", f"dependency:{dependency_name}")
            dependencies.append(DependencyRelation(source=service, target=dependency_name, evidence=item))
            self._add_middleware(dependency_name, middleware)

    def _scan_pom(self, root, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        path = root / "pom.xml"
        if not path.is_file():
            return
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return
        languages.add("Java")
        build_systems.add("Maven")
        project = tree.getroot()
        artifact = self._find_xml_text(project, "artifactId") or fallback
        services.add(artifact)
        record("pom.xml", "artifactId")
        for dependency in project.findall(".//{*}dependency"):
            group = self._find_xml_text(dependency, "groupId")
            name = self._find_xml_text(dependency, "artifactId")
            if not name:
                continue
            target = f"{group}:{name}" if group else name
            item = record("pom.xml", f"dependency:{target}")
            dependencies.append(DependencyRelation(source=artifact, target=target, evidence=item))
            self._add_middleware(target, middleware)

    def _scan_gradle(self, root, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        path = next((candidate for candidate in (root / "build.gradle", root / "build.gradle.kts") if candidate.is_file()), None)
        if path is None:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        source = path.name
        build_systems.add("Gradle")
        if re.search(r"\b(java|org\.springframework\.boot)\b", text):
            languages.add("Java")
        if "kotlin" in text.lower():
            languages.add("Kotlin")
        service = fallback
        services.add(service)
        record(source, "build script")
        for target in sorted(set(re.findall(r"[\"']([\w.-]+:[\w.-]+(?::[^\"']+)*)[\"']", text))):
            item = record(source, f"dependency:{target}")
            dependencies.append(DependencyRelation(source=service, target=target, evidence=item))
            self._add_middleware(target, middleware)

    def _scan_pyproject(self, root, fallback, languages, build_systems, services, middleware, dependencies, record) -> None:
        path = root / "pyproject.toml"
        if not path.is_file():
            return
        try:
            config = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            return
        languages.add("Python")
        build_systems.add("pyproject")
        project = config.get("project", {})
        service = project.get("name") or fallback
        services.add(service)
        record("pyproject.toml", "project.name")
        for dependency in project.get("dependencies", []):
            target = re.split(r"[<>=!~\[ ;]", dependency, maxsplit=1)[0]
            if not target:
                continue
            item = record("pyproject.toml", f"dependency:{target}")
            dependencies.append(DependencyRelation(source=service, target=target, evidence=item))
            self._add_middleware(target, middleware)

    def _scan_application(self, root, services, middleware, record) -> None:
        path = next((candidate for candidate in (root / "application.yml", root / "application.yaml") if candidate.is_file()), None)
        if path is None:
            return
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return
        source = path.name
        spring = config.get("spring", {}) if isinstance(config, dict) else {}
        application = spring.get("application", {}) if isinstance(spring, dict) else {}
        name = application.get("name") if isinstance(application, dict) else None
        if name:
            services.add(str(name))
            record(source, "spring.application.name")
        for key in self._flatten_keys(config):
            self._add_middleware(key, middleware)
        if middleware:
            record(source, "configuration")

    def _scan_compose(self, root, services, middleware, dependencies, record) -> None:
        path = next((candidate for candidate in (root / "docker-compose.yml", root / "docker-compose.yaml") if candidate.is_file()), None)
        if path is None:
            return
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return
        service_config = config.get("services", {}) if isinstance(config, dict) else {}
        if not isinstance(service_config, dict):
            return
        for name in sorted(service_config):
            services.add(name)
            record(path.name, f"service:{name}")
            definition = service_config[name] or {}
            if not isinstance(definition, dict):
                continue
            self._add_middleware(str(definition.get("image", "")), middleware)
            depends_on = definition.get("depends_on", [])
            targets = depends_on.keys() if isinstance(depends_on, dict) else depends_on
            for target in sorted(str(target) for target in targets):
                item = record(path.name, f"depends_on:{name}->{target}")
                dependencies.append(DependencyRelation(source=name, target=target, evidence=item))

    @staticmethod
    def _find_xml_text(element: ET.Element, name: str) -> str:
        child = element.find(f"{{*}}{name}")
        return child.text.strip() if child is not None and child.text else ""

    @staticmethod
    def _flatten_keys(value: Any, prefix: str = "") -> Iterable[str]:
        if isinstance(value, dict):
            for key, nested in value.items():
                full_key = f"{prefix}.{key}" if prefix else str(key)
                yield full_key
                yield from WorkspaceScanner._flatten_keys(nested, full_key)

    @staticmethod
    def _add_middleware(value: str, middleware: set[str]) -> None:
        lowered = value.lower()
        for keyword, label in _MIDDLEWARE.items():
            if keyword in lowered:
                middleware.add(label)
