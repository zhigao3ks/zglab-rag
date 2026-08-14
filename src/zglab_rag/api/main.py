from fastapi import FastAPI

from zglab_rag import __version__
from zglab_rag.config import get_settings
from zglab_rag.sources.registry import SourceRegistry

app = FastAPI(
    title="ZGLab Personal Knowledge Assistant",
    version=__version__,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/sources")
def list_public_sources() -> list[dict[str, object]]:
    settings = get_settings()
    registry = SourceRegistry.from_yaml(settings.sources_config)
    return [
        {
            "id": source.id,
            "kind": source.kind,
            "scope": source.scope,
            "priority": source.priority,
        }
        for source in registry.public()
    ]
