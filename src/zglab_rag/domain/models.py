from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceKind(StrEnum):
    LOCAL = "local"
    GIT = "git"
    WEB = "web"
    GENERATED = "generated"


class Scope(StrEnum):
    IDENTITY = "identity"
    PROJECT = "project"
    KNOWLEDGE = "knowledge"
    EXPERIENCE = "experience"
    DYNAMIC = "dynamic"


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class SourceDefinition(BaseModel):
    id: str
    enabled: bool = True
    kind: SourceKind
    scope: Scope
    visibility: Visibility
    priority: int = Field(default=50, ge=0, le=100)

    path: str | None = None
    repository: str | None = None
    ref: str | None = None
    local_path: str | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_location(self) -> SourceDefinition:
        self._validate_patterns("include", self.include)
        self._validate_patterns("exclude", self.exclude)

        if self.kind == SourceKind.GIT:
            if not self.repository:
                raise ValueError("git source requires 'repository'")
            if not self.local_path:
                raise ValueError("git source requires 'local_path'")
            if PurePosixPath(self.local_path).is_absolute():
                raise ValueError("git source 'local_path' must be relative to the project root")
            if not self.include:
                raise ValueError("git source requires a non-empty include allowlist")
            repository_wide_patterns = {"*", "**", "**/*", "**/*.md", "**/*.markdown"}
            if any(
                pattern.replace("\\", "/").removeprefix("./") in repository_wide_patterns
                for pattern in self.include
            ):
                raise ValueError("git source include may not use a repository-wide catch-all")
        return self

    @staticmethod
    def _validate_patterns(field_name: str, patterns: list[str]) -> None:
        for pattern in patterns:
            normalized = pattern.replace("\\", "/")
            if not normalized or normalized.startswith("/"):
                raise ValueError(f"source {field_name} patterns must be non-empty relative paths")
            if ".." in PurePosixPath(normalized).parts:
                raise ValueError(
                    f"source {field_name} patterns may not traverse parent directories"
                )


class SourceRegistryConfig(BaseModel):
    version: int = 1
    sources: list[SourceDefinition]


class RawDocument(BaseModel):
    """Unparsed content and provenance returned by a source adapter."""

    source_id: str
    source_kind: SourceKind
    scope: Scope
    visibility: Visibility
    priority: int
    source_path: str
    raw_content: str

    source_url: str | None = None
    revision: str | None = None


class KnowledgeDocument(BaseModel):
    document_id: str
    source_id: str
    source_kind: SourceKind
    scope: Scope
    visibility: Visibility
    priority: int
    path: str
    title: str
    content: str
    content_hash: str

    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    project: str | None = None
    language: str = "mixed"
    source_url: str | None = None
    source_revision: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class KnowledgeChunk(BaseModel):
    chunk_id: str
    document_id: str
    source_id: str
    scope: Scope
    visibility: Visibility
    priority: int
    title: str
    section_path: list[str] = Field(default_factory=list)
    chunk_index: int
    content: str
    content_hash: str
    char_count: int
    source_path: str

    token_count: int | None = None
    project: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_url: str | None = None
    revision: str | None = None


class RetrievedChunk(BaseModel):
    chunk: KnowledgeChunk
    score: float
    channel: Literal["vector", "lexical", "hybrid", "rerank"]
