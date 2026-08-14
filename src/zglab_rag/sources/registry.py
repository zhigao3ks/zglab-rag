from pathlib import Path

import yaml

from zglab_rag.domain.models import SourceDefinition, SourceRegistryConfig, Visibility


class SourceRegistry:
    def __init__(self, config: SourceRegistryConfig) -> None:
        self._config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SourceRegistry":
        config_path = Path(path)
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return cls(SourceRegistryConfig.model_validate(raw))

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
