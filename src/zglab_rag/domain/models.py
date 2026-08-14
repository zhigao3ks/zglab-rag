from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


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
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


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
