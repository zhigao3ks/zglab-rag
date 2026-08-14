from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import yaml

from zglab_rag.domain.models import (
    KnowledgeDocument,
    RawDocument,
    Scope,
    Visibility,
)
from zglab_rag.ingestion.errors import (
    EmptyDocumentError,
    InvalidMetadataError,
    MalformedFrontmatterError,
    MissingMetadataError,
)

_HEADING_PATTERN = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_FENCE_PATTERN = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """Return parsed YAML frontmatter and Markdown body."""

    normalized = markdown.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        return {}, normalized

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\n") == "---"),
        None,
    )
    if closing_index is None:
        raise MalformedFrontmatterError("YAML frontmatter is missing its closing '---' delimiter")

    yaml_text = "".join(lines[1:closing_index])
    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise MalformedFrontmatterError(f"Malformed YAML frontmatter: {exc}") from exc

    if loaded is None:
        metadata: dict[str, Any] = {}
    elif isinstance(loaded, Mapping):
        metadata = dict(loaded)
    else:
        raise MalformedFrontmatterError("YAML frontmatter must be a mapping of metadata fields")

    return metadata, "".join(lines[closing_index + 1 :])


def _first_heading(markdown: str) -> str | None:
    active_fence: str | None = None
    for line in markdown.splitlines():
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if active_fence is None:
                active_fence = marker[0]
            elif marker[0] == active_fence:
                active_fence = None
            continue
        if active_fence is None and (heading := _HEADING_PATTERN.match(line)):
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
            if title:
                return title
    return None


def _optional_string(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, bool) and isinstance(value, (str, int, float, date, datetime)):
        text = str(value).strip()
        return text or None
    raise InvalidMetadataError(f"Frontmatter field '{key}' must be a string-compatible value")


def _tags(metadata: Mapping[str, Any]) -> list[str]:
    value = metadata.get("tags", [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(tag, str) for tag in value):
        raise InvalidMetadataError("Frontmatter field 'tags' must be a list of strings")
    return [tag.strip() for tag in value if tag.strip()]


def _scope(metadata: Mapping[str, Any], default: Scope) -> Scope:
    value = metadata.get("scope", default)
    try:
        return Scope(value)
    except ValueError as exc:
        raise InvalidMetadataError(f"Invalid frontmatter scope: {value!r}") from exc


def _visibility(metadata: Mapping[str, Any], source_visibility: Visibility) -> Visibility:
    value = metadata.get("visibility", source_visibility)
    try:
        document_visibility = Visibility(value)
    except ValueError as exc:
        raise InvalidMetadataError(f"Invalid frontmatter visibility: {value!r}") from exc

    if source_visibility == Visibility.PRIVATE or document_visibility == Visibility.PRIVATE:
        return Visibility.PRIVATE
    return Visibility.PUBLIC


def _priority(metadata: Mapping[str, Any], default: int) -> int:
    value = metadata.get("priority", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise InvalidMetadataError("Frontmatter field 'priority' must be an integer from 0 to 100")
    return value


class MarkdownDocumentParser:
    """Parse YAML frontmatter and normalize a raw Markdown source document."""

    def parse(self, raw_document: RawDocument) -> KnowledgeDocument:
        metadata, body = split_frontmatter(raw_document.raw_content)
        content = body.strip()
        if not content:
            raise EmptyDocumentError(
                f"Markdown document '{raw_document.source_path}' has an empty body"
            )

        title = _optional_string(metadata, "title") or _first_heading(content)
        if title is None:
            raise MissingMetadataError(
                f"Markdown document '{raw_document.source_path}' is missing required metadata "
                "'title' and has no heading to use as a fallback"
            )

        revision = (
            raw_document.revision
            or _optional_string(metadata, "revision")
            or _optional_string(metadata, "source_revision")
        )
        return KnowledgeDocument(
            document_id=f"{raw_document.source_id}:{raw_document.source_path}",
            source_id=raw_document.source_id,
            source_kind=raw_document.source_kind,
            scope=_scope(metadata, raw_document.scope),
            visibility=_visibility(metadata, raw_document.visibility),
            priority=_priority(metadata, raw_document.priority),
            path=raw_document.source_path,
            title=title,
            content=content,
            content_hash=sha256_text(raw_document.raw_content),
            summary=_optional_string(metadata, "summary"),
            tags=_tags(metadata),
            project=_optional_string(metadata, "project"),
            language=_optional_string(metadata, "language") or "mixed",
            source_url=raw_document.source_url or _optional_string(metadata, "source_url"),
            source_revision=revision,
            created_at=_optional_string(metadata, "created_at"),
            updated_at=_optional_string(metadata, "updated_at"),
        )
