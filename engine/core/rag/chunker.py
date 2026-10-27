# @author lxy
from __future__ import annotations

import ast
import hashlib
import re

from engine.core.rag.models import SourceChunk


MAX_CHUNK_LINES = 120
CHUNK_OVERLAP_LINES = 20


def chunk_source(repository: str, path: str, text: str) -> list[SourceChunk]:
    suffix = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    if suffix == "py":
        spans = _python_spans(text)
    elif suffix == "java":
        spans = _java_spans(text)
    elif suffix in {"md", "rst"}:
        spans = _document_spans(text)
    else:
        spans = []
    if not spans:
        spans = [(1, max(1, len(text.splitlines())), "", "text")]
    return _materialize(repository, path, text, spans)


def _python_spans(text: str) -> list[tuple[int, int, str, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            spans.append((node.lineno, node.end_lineno or node.lineno, node.name, kind))
    return sorted(set(spans))


_JAVA_DECLARATION = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|abstract|strictfp)\s+)*"
    r"(?:class|interface|enum|record)\s+(?P<class>[A-Za-z_$][\w$]*)"
    r"|^\s*(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default|\s)*"
    r"(?:[\w$<>\[\],.?]+\s+)?(?P<method>[A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:throws[^\{]+)?\{"
)


def _java_spans(text: str) -> list[tuple[int, int, str, str]]:
    lines = text.splitlines()
    spans = []
    for index, line in enumerate(lines):
        match = _JAVA_DECLARATION.search(line)
        if not match:
            continue
        symbol = match.group("class") or match.group("method")
        kind = "class" if match.group("class") else "method"
        depth = 0
        started = False
        end = index
        for cursor in range(index, len(lines)):
            depth += lines[cursor].count("{") - lines[cursor].count("}")
            started = started or "{" in lines[cursor]
            end = cursor
            if started and depth <= 0:
                break
        spans.append((index + 1, end + 1, symbol, kind))
    return sorted(set(spans))


def _document_spans(text: str) -> list[tuple[int, int, str, str]]:
    lines = text.splitlines()
    headings = [
        (index + 1, match.group(1).strip())
        for index, line in enumerate(lines)
        if (match := re.match(r"^#{1,6}\s+(.+?)\s*$", line))
    ]
    return [
        (start, (headings[index + 1][0] - 1) if index + 1 < len(headings) else len(lines), title, "section")
        for index, (start, title) in enumerate(headings)
    ]


def _materialize(repository, path, text, spans) -> list[SourceChunk]:
    lines = text.splitlines()
    chunks = []
    ordinal = 0
    for start, end, symbol, kind in spans:
        cursor = start
        while cursor <= end:
            part_end = min(end, cursor + MAX_CHUNK_LINES - 1)
            content = "\n".join(lines[cursor - 1:part_end]).strip()
            if content:
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                chunks.append(SourceChunk(repository, path, ordinal, cursor, part_end, symbol, kind, content, digest))
                ordinal += 1
            if part_end == end:
                break
            cursor = part_end - CHUNK_OVERLAP_LINES + 1
    return chunks
