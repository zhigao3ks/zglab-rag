from pathlib import Path

from zglab_rag.domain.models import SourceKind, Visibility
from zglab_rag.sources.registry import SourceRegistry

CONFIG_PATH = Path("config/sources.yaml")


def test_source_registry_loads() -> None:
    registry = SourceRegistry.from_yaml(CONFIG_PATH)
    assert registry.all()


def test_v0_registry_contains_only_public_sources() -> None:
    registry = SourceRegistry.from_yaml(CONFIG_PATH)
    assert all(source.visibility == Visibility.PUBLIC for source in registry.all())


def test_identity_profile_has_highest_priority() -> None:
    registry = SourceRegistry.from_yaml(CONFIG_PATH)
    profile = registry.get("identity-profile")
    assert profile.priority == 100


def test_public_project_source_expansion_is_explicit_and_safe() -> None:
    registry = SourceRegistry.from_yaml(CONFIG_PATH)
    sources = {source.id: source for source in registry.all()}

    assert len(sources) == 11
    assert sum(source.kind == SourceKind.GIT for source in sources.values()) == 8
    assert sum(source.kind == SourceKind.LOCAL for source in sources.values()) == 3
    assert {"tingwu-min-demo", "infore-sight-sanita-TEST"}.isdisjoint(sources)

    expected_git_sources = {
        "zglab-rag": ("zhigao3ks/zglab-rag", "Zg443/zglab-rag", 95),
        "agentic": ("zhigao3ks/Agentic", "Zg443/Agentic", 85),
        "medical-multi-agent-system": (
            "zhigao3ks/medical-multi-agent-system",
            "Zg443/medical-multi-agent-system",
            80,
        ),
    }
    for source_id, (canonical, acquisition, priority) in expected_git_sources.items():
        source = sources[source_id]
        assert source.kind == SourceKind.GIT
        assert source.repository == canonical
        assert source.priority == priority
        assert source.acquisition is not None
        assert source.acquisition.provider == "gitee"
        assert source.acquisition.repository == acquisition

    zglab_rag = sources["zglab-rag"]
    assert "docs/evaluations/**" not in zglab_rag.include
    assert "knowledge/projects/**" not in zglab_rag.include
    assert "README.md" not in sources["medical-multi-agent-system"].include


def test_deidentified_project_documents_are_public_local_sources() -> None:
    registry = SourceRegistry.from_yaml(CONFIG_PATH)
    for source_id in ("ai-meeting-assistant", "ai-contract-review"):
        source = registry.get(source_id)
        assert source.kind == SourceKind.LOCAL
        assert source.visibility == Visibility.PUBLIC
        assert source.path is not None
        assert Path(source.path).is_file()
        assert source.include == [source.path]
