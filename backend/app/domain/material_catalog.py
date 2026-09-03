"""Dict-compatible material catalog: curated seed overlaid with DB additions.

``knowledge.RAW_MATERIALS`` used to be a plain module-level dict literal. Eight
modules bind it at *import* time and ~20 call sites read, iterate, or membership
-test it, so the DB-backed catalog has to be a drop-in ``MutableMapping`` rather
than an accessor function.

Four behaviours are load-bearing and are preserved deliberately:

1. **Live references.** ``services/compounds.enrich_materials()`` backfills
   ``smiles``/``molar_mass`` by mutating the nested spec dicts in place. So
   ``__getitem__``/``items()``/``values()`` hand out the *same* dict objects the
   seed holds — never copies — or the enrichment would evaporate.
2. **Iteration order.** Five call sites depend on it: ``colbert_store`` slices
   ``[:40]``, and ``compounds``/``chemical_lookup``/``chemtools``/
   ``resolve_material_name`` all scan first-match-wins. Seed entries therefore
   keep their declaration order and stay in front; DB-only rows append after.
3. **Curated values win.** DB rows only fill fields that are blank in the seed,
   mirroring ``product_store._apply_mention``.
4. **Degrade to seed.** Store unavailable or the feature flag off → the seed
   dict itself is returned, so behaviour is byte-identical to the old literal.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator, MutableMapping

from loguru import logger


def _is_blank(value) -> bool:
    return value is None or value == "" or value == []


class MaterialCatalog(MutableMapping[str, dict]):
    """Seed materials + persisted additions, exposed as a plain mapping."""

    def __init__(self, seed: dict[str, dict]) -> None:
        self._seed = seed
        self._merged: dict[str, dict] | None = None
        self._generation = -1
        self._lock = threading.RLock()

    # ── snapshot management ────────────────────────────────────────────────

    def _store_or_none(self):
        """The material store, or None when disabled/unavailable (degrade path)."""
        try:
            from ..config import get_settings

            if not get_settings().material_store_enabled:
                return None
            from ..db.material_store import get_material_store

            return get_material_store()
        except Exception as exc:  # pragma: no cover - defensive, DB optional
            logger.debug("material catalog: store unavailable ({}); using seed", exc)
            return None

    def _snapshot(self) -> dict[str, dict]:
        """Merged view, rebuilt only when the store reports a new generation.

        ``entity_linker`` iterates the whole catalog once per KB chunk, so this
        must not hit the database on the read path.
        """
        store = self._store_or_none()
        if store is None:
            return self._seed
        with self._lock:
            generation = getattr(store, "generation", 0)
            if self._merged is not None and generation == self._generation:
                return self._merged
            # Copy each spec: the snapshot must be writable (enrich_materials
            # backfills in place) without those writes leaking back into the
            # module literal, which is process-global and would otherwise make
            # every mutation permanent and un-refreshable.
            merged = {name: dict(spec) for name, spec in self._seed.items()}
            try:
                for row in store.list_all():
                    spec = store.row_to_spec(row)
                    existing = merged.get(row.name)
                    if existing is None:
                        merged[row.name] = spec
                        continue
                    # Seed entry already present: fill only what the seed leaves
                    # blank, in place, so the caller keeps holding one live dict
                    # per material. Curated chemistry therefore always wins,
                    # while fields the seed never carries at all — availability,
                    # supplier, functional_class, hansen_*, substitute_group … —
                    # land naturally.
                    for field, value in spec.items():
                        if _is_blank(value):
                            continue
                        if _is_blank(existing.get(field)):
                            existing[field] = value
            except Exception as exc:
                logger.debug("material catalog: overlay failed ({}); using seed", exc)
                return self._seed
            self._merged = merged
            self._generation = generation
            return merged

    def refresh(self) -> None:
        """Drop the cached snapshot; the next read rebuilds it."""
        with self._lock:
            self._merged = None
            self._generation = -1

    # ── Mapping protocol ───────────────────────────────────────────────────

    def __getitem__(self, key: str) -> dict:
        return self._snapshot()[key]

    def __setitem__(self, key: str, value: dict) -> None:
        snapshot = self._snapshot()
        snapshot[key] = value
        if snapshot is not self._seed:
            self._seed.setdefault(key, value)

    def __delitem__(self, key: str) -> None:
        snapshot = self._snapshot()
        del snapshot[key]
        self._seed.pop(key, None)

    def __iter__(self) -> Iterator[str]:
        return iter(self._snapshot())

    def __len__(self) -> int:
        return len(self._snapshot())

    def __contains__(self, key: object) -> bool:
        return key in self._snapshot()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MaterialCatalog({len(self)} materials)"

    # ── persistence ────────────────────────────────────────────────────────

    def seed_specs(self) -> dict[str, dict]:
        """The curated literal, untouched by any overlay (used to seed the DB)."""
        return self._seed

    def persist(self, name: str) -> bool:
        """Write one material back to the store. No-op when the store is off."""
        store = self._store_or_none()
        if store is None:
            return False
        spec = self._snapshot().get(name)
        if spec is None:
            return False
        try:
            store.upsert(name, spec)
            return True
        except Exception as exc:
            logger.debug("material catalog: persist({}) failed: {}", name, exc)
            return False

    def persist_all(self, *, only_missing: bool = True) -> int:
        """Persist the current view. Used after PubChem enrichment so backfilled
        SMILES survive a restart instead of being recomputed every boot."""
        store = self._store_or_none()
        if store is None:
            return 0
        written = 0
        for name, spec in self._snapshot().items():
            if only_missing and not spec.get("smiles"):
                continue
            try:
                store.upsert(name, spec)
                written += 1
            except Exception as exc:
                logger.debug("material catalog: persist_all({}) failed: {}", name, exc)
        if written:
            self.refresh()
        return written


def candidates_by_role(catalog: MutableMapping[str, dict], role: str) -> list[str]:
    """Material names carrying ``role``, in catalog order (seed first)."""
    return [name for name, spec in catalog.items() if spec.get("role") == role]
