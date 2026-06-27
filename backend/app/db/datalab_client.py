"""Shared Datalab Headless ELN HTTP contract helpers (campaign + experiment stores)."""
from __future__ import annotations

from typing import Any, Callable

from ..domain.schemas import DatalabDeleteResponse, DatalabItemEnvelope, DatalabSampleResponse


class DatalabStoreError(ValueError):
    """Raised when Datalab API responses fail Pydantic contract validation."""


def validate_blocks(item_data: dict[str, Any], required_blocks: tuple[str, ...]) -> None:
    blocks = item_data.get("blocks_obj")
    if not isinstance(blocks, dict):
        raise DatalabStoreError("Datalab item_data.blocks_obj missing or invalid")
    for key in required_blocks:
        block = blocks.get(key)
        if not isinstance(block, dict) or "data" not in block:
            raise DatalabStoreError(f"Datalab block {key!r} missing or invalid")


def parse_create_sample_response(body: dict[str, Any], expected_item_id: str) -> DatalabSampleResponse:
    entry_raw = body.get("sample_list_entry") if isinstance(body.get("sample_list_entry"), dict) else body
    sample = DatalabSampleResponse.model_validate(entry_raw)
    if sample.item_id != expected_item_id:
        raise DatalabStoreError(
            f"Datalab item_id mismatch: expected {expected_item_id!r}, got {sample.item_id!r}"
        )
    return sample


def parse_item_envelope(
    body: dict[str, Any],
    *,
    required_blocks: tuple[str, ...] | None = None,
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if isinstance(body.get("item_data"), dict):
        envelope = DatalabItemEnvelope.model_validate(body)
        item_data = envelope.item_data
    elif isinstance(body.get("blocks_obj"), dict):
        item_data = body
    else:
        raise DatalabStoreError("Datalab get-item-data response missing item_data")
    if validate is not None:
        validate(item_data)
    elif required_blocks:
        validate_blocks(item_data, required_blocks)
    return item_data


def parse_delete_response(body: dict[str, Any], item_id: str) -> None:
    parsed = DatalabDeleteResponse.model_validate(body)
    if parsed.status != "success":
        raise DatalabStoreError(f"Datalab delete-sample failed for {item_id}: status={parsed.status}")
