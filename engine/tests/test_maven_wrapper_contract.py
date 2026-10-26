# @author lxy
"""Static contract for the Java service's official Maven Wrapper."""

import re
from pathlib import Path
from urllib.parse import urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
JAVA_SERVICE = REPOSITORY_ROOT / "java-service"
WRAPPER_DIRECTORY = JAVA_SERVICE / ".mvn" / "wrapper"


def _wrapper_properties() -> dict[str, str]:
    properties = {}
    for raw_line in (WRAPPER_DIRECTORY / "maven-wrapper.properties").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            properties[key] = value
    return properties


def test_official_only_script_wrapper_files_are_present() -> None:
    posix_script = JAVA_SERVICE / "mvnw"
    windows_script = JAVA_SERVICE / "mvnw.cmd"

    assert posix_script.is_file()
    assert windows_script.is_file()
    assert (WRAPPER_DIRECTORY / "maven-wrapper.properties").is_file()
    assert not (WRAPPER_DIRECTORY / "maven-wrapper.jar").exists()
    assert not (WRAPPER_DIRECTORY / "MavenWrapperDownloader.java").exists()

    assert posix_script.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    for script in (posix_script, windows_script):
        assert "Apache Maven Wrapper startup batch script, version 3.3.4" in (
            script.read_text(encoding="utf-8")
        )


def test_wrapper_pins_verified_maven_distribution() -> None:
    properties = _wrapper_properties()

    assert properties["wrapperVersion"] == "3.3.4"
    assert properties["distributionType"] == "only-script"
    assert properties["distributionUrl"] == (
        "https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/"
        "3.9.16/apache-maven-3.9.16-bin.zip"
    )
    assert urlparse(properties["distributionUrl"]).scheme == "https"
    assert re.fullmatch(r"[0-9a-f]{64}", properties["distributionSha256Sum"])


def test_windows_wrapper_handles_a_regular_existing_maven_home() -> None:
    script = (JAVA_SERVICE / "mvnw.cmd").read_text(encoding="utf-8")

    assert "$mavenM2Target = (Get-Item $MAVEN_M2_PATH).Target" in script
    assert "if (!$mavenM2Target)" in script
    assert "(Get-Item $MAVEN_M2_PATH).Target[0]" not in script
