from __future__ import annotations

import json
import re

from pydantic import ValidationError

from zglab_rag.generation.contracts import GeneratedAnswer
from zglab_rag.generation.errors import InvalidStructuredOutput

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _candidate_payloads(text: str) -> list[str]:
    candidates = [text.strip()]
    fenced = _CODE_FENCE.findall(text)
    candidates.extend(block.strip() for block in fenced if block.strip())
    match = _JSON_OBJECT.search(text)
    if match is not None:
        candidates.append(match.group(0))
    return candidates


def parse_structured_answer(text: str) -> GeneratedAnswer:
    """Parse provider text into the structured answer schema.

    Tolerates stray code fences or surrounding prose but never invents fields;
    any parse or schema failure raises InvalidStructuredOutput.
    """
    stripped = text.strip()
    if not stripped:
        raise InvalidStructuredOutput("provider returned an empty response")
    payload = None
    for candidate in _candidate_payloads(stripped):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        break
    if payload is None:
        raise InvalidStructuredOutput("provider response does not contain a JSON object")
    if not isinstance(payload, dict):
        raise InvalidStructuredOutput("provider JSON response is not an object")
    try:
        return GeneratedAnswer.model_validate(payload)
    except ValidationError as exc:
        raise InvalidStructuredOutput(f"provider JSON violates the answer schema: {exc}") from exc
