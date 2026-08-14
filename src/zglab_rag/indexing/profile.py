from pathlib import Path

from zglab_rag.embeddings.config import EmbeddingModelConfig, EmbeddingModelRegistry
from zglab_rag.evaluation.composition import TextComposition
from zglab_rag.indexing.models import EmbeddingProfile
from zglab_rag.storage.schema import VECTOR_DIMENSION

ACTIVE_MODEL_ID = "bge-small-zh-v1.5"
ACTIVE_COMPOSITION = TextComposition.CONTEXTUAL


def load_active_embedding_profile(
    models_config: str | Path = "config/embedding-models.yaml",
) -> tuple[EmbeddingProfile, EmbeddingModelConfig]:
    model = EmbeddingModelRegistry.from_yaml(models_config).get_enabled(ACTIVE_MODEL_ID)
    profile = EmbeddingProfile.create(
        model,
        dimension=VECTOR_DIMENSION,
        composition=ACTIVE_COMPOSITION,
    )
    return profile, model
