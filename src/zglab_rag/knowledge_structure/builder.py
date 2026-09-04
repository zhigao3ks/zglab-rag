from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from zglab_rag.storage.schema import RETRIEVAL_STRUCTURE_VERSION

_WORD = re.compile(r"[a-z0-9][a-z0-9.+#_-]{1,31}|[\u3400-\u9fff]{2,}")
_STOPWORDS = {"the", "and", "with", "from", "that", "this", "是", "的", "和", "与", "了"}


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def section_id_for(document_id: str, section_path: list[str]) -> str:
    return "sec_" + _hash({"document_id": document_id, "section_path": section_path})


class CuratedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^(person|technology|topic):[a-z0-9][a-z0-9._-]*$")
    type: Literal["PERSON", "TECHNOLOGY", "TOPIC"]
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class CuratedRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    relation: Literal["RELATED_TO", "USES", "DEVELOPED", "WORKED_ON"]
    target: str
    source_id: str | None = None
    document_id: str | None = None
    section_id: str | None = None
    chunk_id: str | None = None

    @model_validator(mode="after")
    def require_provenance(self) -> CuratedRelation:
        if not any((self.source_id, self.document_id, self.section_id, self.chunk_id)):
            raise ValueError("curated relation requires indexed provenance")
        return self


class KnowledgeGraphCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    entities: list[CuratedEntity] = Field(default_factory=list)
    relations: list[CuratedRelation] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> KnowledgeGraphCatalog:
        candidate = Path(path)
        if not candidate.is_file():
            return cls()
        return cls.model_validate(yaml.safe_load(candidate.read_text(encoding="utf-8")) or {})


def _keywords(values: list[str], *, limit: int = 24) -> list[str]:
    tokens: Counter[str] = Counter()
    for value in values:
        normalized = normalize_name(value)
        for token in _WORD.findall(normalized):
            if token not in _STOPWORDS:
                tokens[token] += 1
            if any("\u3400" <= char <= "\u9fff" for char in token):
                tokens.update(token[index : index + 2] for index in range(len(token) - 1))
    return [
        token
        for token, _ in sorted(tokens.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _insert_node(
    connection: sqlite3.Connection,
    node_id: str,
    node_type: str,
    name: str,
    metadata: dict,
    aliases: list[str] | None = None,
) -> None:
    normalized = normalize_name(name)
    connection.execute(
        "INSERT INTO graph_nodes VALUES(?,?,?,?,?)",
        (
            node_id,
            node_type,
            name,
            normalized,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    for alias in sorted({normalized, *(normalize_name(value) for value in (aliases or []))}):
        if alias:
            connection.execute(
                "INSERT OR IGNORE INTO graph_aliases(normalized_alias,node_id) VALUES(?,?)",
                (alias, node_id),
            )


def _insert_edge(
    connection: sqlite3.Connection,
    source: str,
    relation: str,
    target: str,
    provenance_kind: str,
    *,
    source_id: str | None = None,
    document_id: str | None = None,
    section_id: str | None = None,
    chunk_id: str | None = None,
) -> None:
    values = {
        "source": source,
        "relation": relation,
        "target": target,
        "kind": provenance_kind,
        "source_id": source_id,
        "document_id": document_id,
        "section_id": section_id,
        "chunk_id": chunk_id,
    }
    connection.execute(
        "INSERT OR IGNORE INTO graph_edges VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "edge_" + _hash(values),
            source,
            relation,
            target,
            provenance_kind,
            source_id,
            document_id,
            section_id,
            chunk_id,
            "{}",
        ),
    )


def _validate_curated_provenance(
    connection: sqlite3.Connection, relation: CuratedRelation
) -> tuple[str | None, str | None, str | None, str | None] | None:
    clauses = ["c.visibility='public'"]
    parameters: list[str] = []
    if relation.chunk_id:
        clauses.append("c.chunk_id=?")
        parameters.append(relation.chunk_id)
    if relation.document_id:
        clauses.append("c.document_id=?")
        parameters.append(relation.document_id)
    if relation.section_id:
        clauses.append(
            "EXISTS(SELECT 1 FROM sections s WHERE s.section_id=? "
            "AND s.document_id=c.document_id AND s.section_path_json=c.section_path_json)"
        )
        parameters.append(relation.section_id)
    if relation.source_id:
        clauses.append("c.source_id=?")
        parameters.append(relation.source_id)
    row = connection.execute(
        f"SELECT c.source_id,c.document_id,c.chunk_id FROM chunks c "
        f"WHERE {' AND '.join(clauses)} ORDER BY c.chunk_index,c.chunk_id LIMIT 1",
        parameters,
    ).fetchone()
    if row is None:
        return None
    return row["source_id"], row["document_id"], relation.section_id, row["chunk_id"]


def rebuild_knowledge_structure(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    catalog_path: str | Path = Path("config/knowledge-graph.yaml"),
) -> str:
    """Rebuild all PUBLIC derived structures inside the caller's transaction."""
    for table in (
        "fts_documents",
        "fts_sections",
        "graph_edges",
        "graph_aliases",
        "graph_nodes",
        "sections",
        "document_profiles",
    ):
        connection.execute(f"DELETE FROM {table}")

    catalog = KnowledgeGraphCatalog.from_yaml(catalog_path)
    for entity in sorted(catalog.entities, key=lambda item: item.id):
        _insert_node(
            connection, entity.id, entity.type, entity.name, {"curated": True}, entity.aliases
        )

    documents = connection.execute(
        "SELECT * FROM documents WHERE visibility='public' ORDER BY source_id,document_id"
    ).fetchall()
    section_rows: list[tuple] = []
    for document in documents:
        chunks = connection.execute(
            "SELECT * FROM chunks WHERE document_id=? AND visibility='public' "
            "ORDER BY chunk_index,chunk_id",
            (document["document_id"],),
        ).fetchall()
        paths: dict[str, list[sqlite3.Row]] = {}
        outlines: list[list[str]] = []
        for chunk in chunks:
            path = json.loads(chunk["section_path_json"])
            canonical = chunk["section_path_json"]
            paths.setdefault(canonical, []).append(chunk)
            if path and path not in outlines and len(outlines) < 40:
                outlines.append(path)
        metadata = json.loads(document["metadata_json"])
        project = str(metadata.get("project") or document["source_id"])
        first_content = next(
            (row["content"].strip() for row in chunks if row["content"].strip()), ""
        )
        summary = " | ".join(
            value
            for value in (
                document["title"],
                project,
                " > ".join(outlines[0]) if outlines else "",
                first_content[:700],
            )
            if value
        )[:1200]
        keywords = _keywords(
            [document["title"], project, *(heading for path in outlines for heading in path)]
        )
        profile_values = {
            "document_id": document["document_id"],
            "source_id": document["source_id"],
            "project": project,
            "title": document["title"],
            "summary": summary,
            "outline": outlines,
            "keywords": keywords,
        }
        connection.execute(
            "INSERT INTO document_profiles VALUES(?,?,?,?,?,?,?,?,?)",
            (
                document["document_id"],
                document["source_id"],
                project,
                document["title"],
                summary,
                json.dumps(outlines, ensure_ascii=False),
                json.dumps(keywords, ensure_ascii=False),
                _hash(profile_values),
                document["updated_at"],
            ),
        )
        connection.execute(
            "INSERT INTO fts_documents(document_id,title,project,locator,summary,outline,keywords) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                document["document_id"],
                document["title"],
                project,
                document["source_path"],
                summary,
                " ".join(" > ".join(path) for path in outlines),
                " ".join(keywords),
            ),
        )
        project_node = "project:" + document["source_id"]
        document_node = "document:" + document["document_id"]
        if connection.execute(
            "SELECT 1 FROM graph_nodes WHERE node_id=?", (project_node,)
        ).fetchone() is None:
            _insert_node(
                connection,
                project_node,
                "PROJECT",
                project,
                {"source_id": document["source_id"]},
                [document["source_id"]],
            )
        _insert_node(
            connection,
            document_node,
            "DOCUMENT",
            document["title"],
            {"document_id": document["document_id"], "source_path": document["source_path"]},
        )
        _insert_edge(
            connection,
            project_node,
            "CONTAINS",
            document_node,
            "STRUCTURAL",
            source_id=document["source_id"],
            document_id=document["document_id"],
        )
        for canonical_path, members in sorted(paths.items()):
            path = json.loads(canonical_path)
            section_id = section_id_for(document["document_id"], path)
            title = path[-1] if path else document["title"]
            representative = members[0]["content"].strip()[:600]
            profile_text = " | ".join(
                value for value in (title, " > ".join(path), representative) if value
            )[:900]
            section_rows.append(
                (
                    section_id,
                    document["document_id"],
                    document["source_id"],
                    canonical_path,
                    title,
                    len(members),
                    min(row["chunk_index"] for row in members),
                    max(row["chunk_index"] for row in members),
                    profile_text,
                )
            )
            connection.execute("INSERT INTO sections VALUES(?,?,?,?,?,?,?,?,?)", section_rows[-1])
            connection.execute(
                "INSERT INTO fts_sections(section_id,document_id,section_title,section_path,"
                "profile_text) VALUES(?,?,?,?,?)",
                (section_id, document["document_id"], title, " > ".join(path), profile_text),
            )
            section_node = "section:" + section_id
            _insert_node(
                connection,
                section_node,
                "SECTION",
                title,
                {"document_id": document["document_id"], "section_id": section_id},
            )
            _insert_edge(
                connection,
                document_node,
                "CONTAINS",
                section_node,
                "STRUCTURAL",
                source_id=document["source_id"],
                document_id=document["document_id"],
                section_id=section_id,
                chunk_id=members[0]["chunk_id"],
            )

    entity_aliases = connection.execute(
        "SELECT a.normalized_alias,a.node_id FROM graph_aliases a "
        "JOIN graph_nodes n ON n.node_id=a.node_id "
        "WHERE n.node_type IN ('PERSON','TECHNOLOGY','TOPIC') "
        "ORDER BY length(a.normalized_alias) DESC,a.normalized_alias,a.node_id"
    ).fetchall()
    for section in section_rows:
        section_id, document_id, source_id, canonical_path = section[:4]
        chunks = connection.execute(
            "SELECT chunk_id,content FROM chunks WHERE document_id=? "
            "AND section_path_json=? AND visibility='public' ORDER BY chunk_index,chunk_id",
            (document_id, canonical_path),
        ).fetchall()
        for alias_row in entity_aliases:
            match = next(
                (
                    row
                    for row in chunks
                    if alias_row["normalized_alias"] in normalize_name(row["content"])
                ),
                None,
            )
            if match is not None:
                _insert_edge(
                    connection,
                    "section:" + section_id,
                    "MENTIONS",
                    alias_row["node_id"],
                    "TEXT_MENTION",
                    source_id=source_id,
                    document_id=document_id,
                    section_id=section_id,
                    chunk_id=match["chunk_id"],
                )

    for relation in catalog.relations:
        if not all(
            connection.execute("SELECT 1 FROM graph_nodes WHERE node_id=?", (node,)).fetchone()
            for node in (relation.source, relation.target)
        ):
            continue
        provenance = _validate_curated_provenance(connection, relation)
        if provenance is not None:
            _insert_edge(
                connection,
                relation.source,
                relation.relation,
                relation.target,
                "CURATED",
                source_id=provenance[0],
                document_id=provenance[1],
                section_id=provenance[2],
                chunk_id=provenance[3],
            )

    snapshot = _hash(
        {
            "run_id": run_id,
            "version": RETRIEVAL_STRUCTURE_VERSION,
            "documents": [row["document_id"] for row in documents],
            "catalog": catalog.model_dump(mode="json"),
            "profiles": [
                row[0]
                for row in connection.execute(
                    "SELECT profile_hash FROM document_profiles ORDER BY document_id"
                )
            ],
        }
    )
    for key, value in (
        ("retrieval_structure_version", str(RETRIEVAL_STRUCTURE_VERSION)),
        ("retrieval_structure_snapshot", snapshot),
        ("retrieval_structure_run_id", run_id),
        ("knowledge_graph_catalog_version", str(catalog.version)),
    ):
        connection.execute(
            "INSERT INTO index_metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    return snapshot
