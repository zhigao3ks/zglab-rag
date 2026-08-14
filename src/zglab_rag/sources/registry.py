from pathlib import Path

import yaml
from pydantic import ValidationError

from zglab_rag.domain.models import SourceDefinition, SourceKind, SourceRegistryConfig, Visibility
from zglab_rag.sources.errors import SourceConfigurationError, SourceNotRegisteredError


class SourceRegistry:
    def __init__(self, config: SourceRegistryConfig) -> None:
        source_ids = [source.id for source in config.sources]
        if len(source_ids) != len(set(source_ids)):
            raise SourceConfigurationError("Source registry contains duplicate source IDs")
        self._config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SourceRegistry":
        config_path = Path(path)
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config = SourceRegistryConfig.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise SourceConfigurationError(
                f"Unable to load source registry '{config_path}': {exc}"
            ) from exc
        return cls(config)

    def all(self, *, enabled_only: bool = True) -> list[SourceDefinition]:
        if not enabled_only:
            return list(self._config.sources)
        return [source for source in self._config.sources if source.enabled]

    def public(self) -> list[SourceDefinition]:
        return [
            source
            for source in self.all(enabled_only=True)
            if source.visibility == Visibility.PUBLIC
        ]

    def get(self, source_id: str) -> SourceDefinition:
        for source in self._config.sources:
            if source.id == source_id:
                return source
        raise KeyError(f"Unknown source: {source_id}")

    def get_enabled(self, source_id: str) -> SourceDefinition:
        try:
            source = self.get(source_id)
        except KeyError as exc:
            raise SourceNotRegisteredError(f"Source is not registered: {source_id}") from exc
        if not source.enabled:
            raise SourceNotRegisteredError(f"Source is registered but disabled: {source_id}")
        return source

    def local_for_path(
        self,
        path: str | Path,
        *,
        project_root: str | Path = ".",
    ) -> SourceDefinition:
        root = Path(project_root).resolve()
        requested = Path(path)
        requested = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
        for source in self.all(enabled_only=True):
            if source.kind != SourceKind.LOCAL or not source.path:
                continue
            if (root / source.path).resolve() == requested:
                return source
        raise KeyError(f"Path is not an enabled registered local source: {path}")
