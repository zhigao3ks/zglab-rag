from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from numpy.typing import NDArray


class RerankerProvider(Protocol):
    model_name: str
    backend: str
    device: str
    batch_size: int

    def score(self, query: str, passages: Sequence[str]) -> NDArray: ...
