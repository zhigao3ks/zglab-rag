from pathlib import Path

from zglab_rag.domain.models import Visibility
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
