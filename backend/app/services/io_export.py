"""DOE plan export & experiment-result import (CSV / XLSX).

Bridges the gap between the platform and the lab bench: a generated DOE plan is
exported as a worksheet with one row per run (natural factor values + a blank
``measured_<metric>`` column) for the technician to fill in, and the completed
sheet is imported back as :class:`ExperimentRecord` rows.

CSV uses only the standard library (always available). XLSX uses ``openpyxl``
when installed (declared as the optional ``export`` extra); when it is absent
the API surfaces a clear error rather than failing opaquely — same
adapter+fallback philosophy as the rest of the platform.
"""
from __future__ import annotations

from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import csv
import io

from ..domain.schemas import DOEPlan, ExperimentRecord, ProductDomain

# Columns that are metadata, never treated as formulation factors on import.
_META_COLS = {"run_id", "domain"}
_MEASURED_PREFIX = "measured_"


def _export_headers(plan: DOEPlan, metrics: list[str]) -> list[str]:
    factor_names = [f.name for f in plan.factors]
    return ["run_id", "domain", *factor_names, *[f"{_MEASURED_PREFIX}{m}" for m in metrics]]


def _export_rows(plan: DOEPlan, headers: list[str]) -> list[list]:
    domain = plan.domain.value if plan.domain else ""
    rows: list[list] = []
    for run in plan.runs:
        row: list = []
        for col in headers:
            if col == "run_id":
                row.append(run.run_id)
            elif col == "domain":
                row.append(domain)
            elif col.startswith(_MEASURED_PREFIX):
                row.append("")  # blank — to be filled by the lab
            else:
                row.append(run.natural.get(col, ""))
        rows.append(row)
    return rows


def plan_to_csv(plan: DOEPlan, metrics: list[str]) -> str:
    """Render a DOE plan as a CSV string with blank measured columns."""
    headers = _export_headers(plan, metrics)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(_export_rows(plan, headers))
    return buf.getvalue()


def plan_to_xlsx(plan: DOEPlan, metrics: list[str]) -> bytes:
    """Render a DOE plan as XLSX bytes. Requires the optional ``openpyxl`` extra."""
    try:
        from openpyxl import Workbook  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "XLSX export requires the optional 'openpyxl' dependency "
            "(pip install -e '.[export]'). Use format=csv otherwise."
        ) from exc
    headers = _export_headers(plan, metrics)
    wb = Workbook()
    ws = wb.active
    ws.title = f"DOE-{plan.design}"[:31]
    ws.append(headers)
    for row in _export_rows(plan, headers):
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _coerce_float(value: str) -> float | None:
    value = (value or "").strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def csv_to_records(text: str, default_domain: ProductDomain | None = None) -> list[ExperimentRecord]:
    """Parse a filled-in DOE/experiment CSV into ExperimentRecord rows.

    Recognised columns:
    * ``domain`` — product family (falls back to ``default_domain`` if absent);
    * ``measured_<metric>`` — one or more measured property columns;
    * ``cure_temperature_c`` — mapped to the record's process field;
    * every other non-metadata column is treated as a formulation factor.

    Rows with no measured value are skipped.
    """
    reader = csv.DictReader(io.StringIO(text))
    records: list[ExperimentRecord] = []
    for raw in reader:
        # Normalise keys (strip surrounding whitespace from headers).
        row = {(k or "").strip(): v for k, v in raw.items()}

        domain_val = (row.get("domain") or "").strip()
        try:
            domain = ProductDomain(domain_val) if domain_val else default_domain
        except ValueError:
            domain = default_domain
        if domain is None:
            raise ValueError(f"Row missing a valid 'domain' and no default supplied: {row!r}")

        measured: dict[str, float] = {}
        factors: dict[str, float] = {}
        cure_temp: float | None = None
        for col, val in row.items():
            if col in _META_COLS:
                continue
            num = _coerce_float(val)
            if col.startswith(_MEASURED_PREFIX):
                if num is not None:
                    measured[col[len(_MEASURED_PREFIX):]] = num
            elif col == "cure_temperature_c":
                cure_temp = num
            elif num is not None:
                factors[col] = num

        if not measured:
            continue  # nothing to learn from an unfilled row
        records.append(
            ExperimentRecord(
                domain=domain,
                factors=factors,
                cure_temperature_c=cure_temp,
                measured=measured,
                source="csv-import",
            )
        )
    return records
