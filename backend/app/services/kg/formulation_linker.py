"""Link experiment formulations to knowledge graph entities."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from ...db.models import KGEntity, KGFormulationLink
from ...db.session import get_db_session
from .entity_resolver import resolve_query

logger = logging.getLogger(__name__)


def _infer_role(name: str) -> str:
    """子串 → Role 推断。规则表见 ``resources/rules/linker_roles.toml``
    (R1, 2026-09-04: 自 _ROLE_HINTS 硬编码迁移; FORMUMIND_RULES_DIR 可
    覆盖, 缺失回退内置默认)。遍历顺序即优先级(TOML 保序)。"""
    from ..rule_loader import load_rules

    name_lower = name.lower()
    hints = load_rules("linker_roles")["role_hints"]
    for role, role_hints in hints.items():
        for hint in role_hints:
            if hint in name_lower:
                return role
    return "unknown"

def link_experiment_to_kg(experiment_id: int, factors: dict[str, Any], domain: str, project_id: str) -> int:
    """Parse formulation factors and link each ingredient to KG entities."""
    count = 0
    with get_db_session() as session:
        session.query(KGFormulationLink).filter(
            KGFormulationLink.experiment_id == experiment_id
        ).delete(synchronize_session=False)

        for ingredient_name, weight_pct in factors.items():
            if not isinstance(weight_pct, (int, float)) or weight_pct <= 0:
                continue

            resolved = resolve_query(ingredient_name)
            entity_id = None

            if resolved.chemicals:
                entity_id = resolved.chemicals[0].id
            elif resolved.trade_products:
                entity_id = resolved.trade_products[0].id
            else:
                temp_id = f"raw:{project_id}:{ingredient_name.lower().replace(' ', '_')}"
                existing = session.query(KGEntity).filter(KGEntity.id == temp_id).first()
                if not existing:
                    entity = KGEntity(
                        id=temp_id, kind="raw_material",
                        canonical_name=ingredient_name,
                        role=_infer_role(ingredient_name), mention_count=1,
                    )
                    session.add(entity)
                    session.flush()
                entity_id = temp_id

            if entity_id:
                link = KGFormulationLink(
                    id=str(uuid.uuid4()), experiment_id=experiment_id,
                    entity_id=entity_id, role=_infer_role(ingredient_name),
                    weight_pct=float(weight_pct), link_type="contains",
                    project_id=project_id or "",
                )
                session.add(link)
                count += 1
        session.commit()
    logger.info("Linked experiment %d to KG: %d ingredients", experiment_id, count)
    return count
