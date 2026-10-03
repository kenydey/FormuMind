"""Experiment feedback & model training.

Closes the DOE loop: measured lab results are stored via a pluggable
:class:`ExperimentStore`, and a data-driven regression model is trained per
(domain, metric). Once enough samples exist the trained model supersedes the
empirical surrogate inside ``predictor.predict``.

Persistence follows the adapter+fallback pattern:
* :class:`~app.db.store.SqlExperimentStore` (SQLAlchemy, SQLite by default) —
  transactional, concurrency-safe, Postgres-ready — is the production default.
* :class:`~app.db.store.JsonExperimentStore` — the v0.1 JSON file store,
  retained for isolated unit tests (pass ``path=`` to ``ModelRegistry``).

Training backend follows the same pattern:
* scikit-learn ``RandomForestRegressor`` when installed;
* otherwise a self-contained numpy ridge regressor.
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone

import numpy as np

from ..config import get_settings

logger = logging.getLogger(__name__)
from ..domain import features
from ..domain.schemas import ExperimentRecord, ModelInfo, ProductDomain, Requirement, Substrate
from ..pipeline import reconstruct  # lightweight: form-from-factors, no cycle
from . import model_store


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class _RidgeModel:
    """Closed-form ridge regression with feature standardisation."""

    backend = "numpy-ridge"

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self._mean: np.ndarray
        self._std: np.ndarray
        self._w: np.ndarray
        self._b: float = 0.0
        self._rmse: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._mean = X.mean(axis=0)
        self._std = X.std(axis=0)
        self._std[self._std < 1e-9] = 1.0
        Xs = (X - self._mean) / self._std
        n_features = Xs.shape[1]
        self._b = float(y.mean())
        yc = y - self._b
        a = Xs.T @ Xs + self.alpha * np.eye(n_features)
        self._w = np.linalg.solve(a, Xs.T @ yc)
        residuals = y - self.predict(X)
        self._rmse = float(np.sqrt(np.mean(residuals ** 2)))

    def predict(self, X: np.ndarray) -> np.ndarray:
        Xs = (X - self._mean) / self._std
        return Xs @ self._w + self._b

    def predict_std(self, X: np.ndarray) -> float:
        return self._rmse


class _SklearnRFModel:
    """Module-level so joblib/pickle can serialize trained surrogates (P1 #20)."""

    backend = "sklearn-rf"

    def __init__(self) -> None:
        from sklearn.ensemble import RandomForestRegressor

        self._m = RandomForestRegressor(n_estimators=200, random_state=0)

    def fit(self, X, y):
        self._m.fit(X, y)

    def predict(self, X):
        return self._m.predict(X)

    def predict_std(self, X) -> float:
        tree_preds = np.array([t.predict(X)[0] for t in self._m.estimators_])
        return float(np.std(tree_preds))


def _make_regressor():
    """Return (model, backend_name). Prefer sklearn, fall back to numpy ridge."""
    try:  # pragma: no cover - depends on optional extra
        return _SklearnRFModel(), "sklearn-rf"
    except Exception:
        return _RidgeModel(), "numpy-ridge"


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    if ss_tot < 1e-12:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _kfold_oof(X: np.ndarray, y: np.ndarray, k: int = 5) -> np.ndarray | None:
    """Out-of-fold predictions via k-fold; None when the set is too small."""
    n = len(y)
    if n < k or n < 5:
        return None
    rng = np.random.default_rng(0)
    idx = rng.permutation(n)
    folds = np.array_split(idx, k)
    preds = np.zeros(n)
    for f in folds:
        train = np.setdiff1d(idx, f)
        model, _ = _make_regressor()
        model.fit(X[train], y[train])
        preds[f] = model.predict(X[f])
    return preds


# z≈1.645 maps 90% two-sided normal coverage half-width ↔ σ.
_CONFORMAL_TO_STD = 1.64485362695


class _Trained:
    def __init__(self, model, info: ModelInfo) -> None:
        self.model = model
        self.info = info


def _make_store(path: str | None):
    """Return an ExperimentStore: JsonExperimentStore when *path* is given
    (backward-compat for tests), otherwise the configured default store."""
    if path is not None:
        from ..db.store import JsonExperimentStore
        return JsonExperimentStore(path)
    from ..db.store import get_experiment_store
    return get_experiment_store()


class ModelRegistry:
    """Stores experiment records via a pluggable store and trains per-(domain, metric) models."""

    def __init__(self, path: str | None = None, store=None) -> None:
        settings = get_settings()
        self.min_samples = settings.min_train_samples
        # Explicit store > path (backwards-compat) > default SQL store.
        self._store = store if store is not None else _make_store(path)
        self._records: list[ExperimentRecord] = []
        self._models: dict[tuple[str, str], _Trained] = {}
        self._lock = threading.RLock()
        self.load()

    # --- persistence ---------------------------------------------------
    def load(self) -> None:
        """Reload records from the backing store and retrain all models."""
        with self._lock:
            self._records = self._store.all()
            self._retrain_all()

    def reset(self, persist: bool = False) -> None:
        """Clear in-memory records and models; when *persist* is True also wipe the store."""
        with self._lock:
            self._records = []
            self._models = {}
            if persist:
                self._store.clear()

    # --- ingestion -----------------------------------------------------
    def add(self, records: list[ExperimentRecord], retrain: bool = True) -> bool:
        """Persist *records*; return True when the surrogate models were retrained.

        ``retrain`` is what the caller asked for; the global ``auto_retrain``
        setting (Settings UI "实验自动重训") can veto it, in which case models
        only refresh through ``train()`` / ``POST /api/train`` or a restart.
        """
        with self._lock:
            self._store.add(records)
            self._records.extend(records)
            if retrain and get_settings().auto_retrain:
                self._retrain_all()
                return True
            return False

    def known_labels(self) -> set[str]:
        with self._lock:
            return {r.label for r in self._records if r.label}

    @property
    def total_records(self) -> int:
        with self._lock:
            return len(self._records)

    def all_records(self) -> list[ExperimentRecord]:
        """B: 全部训练记录（供状态总览按 domain 聚合）。"""
        with self._lock:
            return list(self._records)

    def records_for(self, domain: ProductDomain, project_id: str = "") -> list[ExperimentRecord]:
        """Return stored experiment records for a domain / project."""
        with self._lock:
            pid = project_id or domain.value
            return [
                rec
                for rec in self._records
                if rec.domain == domain
                and (not project_id or rec.project_id in ("", pid, domain.value))
            ]

    # --- training ------------------------------------------------------
    def _dataset(
        self, domain: ProductDomain, metric: str, project_id: str = ""
    ) -> tuple[np.ndarray, np.ndarray] | None:
        rows, ys = [], []
        pid = project_id or domain.value
        for rec in self._records:
            if rec.domain != domain or metric not in rec.measured:
                continue
            # Scoped project training must not pull empty-project rows (or other
            # projects). Domain-level models use project_id="" / domain.value.
            rec_pid = (rec.project_id or "").strip() or rec.domain.value
            if project_id:
                if rec_pid != pid:
                    continue
            req = Requirement(domain=rec.domain)
            sub_raw = rec.factors.get("substrate")
            if sub_raw is not None:
                try:
                    req.substrate = Substrate(str(sub_raw))
                except Exception:
                    pass
            form = reconstruct.formulation_from_factors(req, rec.factors)
            process = {"cure_temperature_c": rec.cure_temperature_c or 0.0}
            rows.append(features.vector(form, process))
            ys.append(rec.measured[metric])
        if len(rows) < self.min_samples:
            return None
        return np.array(rows, dtype=float), np.array(ys, dtype=float)

    def _training_rows(self, domain: ProductDomain, metric: str, project_id: str) -> list[ExperimentRecord]:
        pid = project_id or domain.value
        rows: list[ExperimentRecord] = []
        for rec in self._records:
            if rec.domain != domain or metric not in rec.measured:
                continue
            rec_pid = (rec.project_id or "").strip() or rec.domain.value
            if project_id:
                if rec_pid != pid:
                    continue
            rows.append(rec)
        return rows

    def _persist_enabled(self) -> bool:
        return bool(getattr(get_settings(), "model_persist_enabled", True))

    def _try_load_cached(
        self,
        domain: ProductDomain,
        pid: str,
        metric: str,
        data_hash: str,
        feature_version: str,
    ) -> _Trained | None:
        if not self._persist_enabled():
            return None
        loaded = model_store.load_model(
            pid,
            metric,
            data_hash=data_hash,
            feature_version=feature_version,
        )
        if loaded is None:
            return None
        model, info_dict = loaded
        try:
            info = self._info_from_artifact(
                domain,
                pid,
                metric,
                info_dict,
                version_id=info_dict.get("version_id"),
                data_hash=info_dict.get("data_hash") or data_hash,
                feature_version=info_dict.get("feature_version") or feature_version,
            )
        except Exception as exc:
            logger.warning("cached ModelInfo invalid for %s/%s: %s", pid, metric, exc)
            return None
        return _Trained(model, info)

    @staticmethod
    def _info_from_artifact(
        domain: ProductDomain,
        pid: str,
        metric: str,
        info_dict: dict,
        *,
        version_id: str | None,
        data_hash: str | None = None,
        feature_version: str | None = None,
        pinned: bool = False,
        newer_version_id: str | None = None,
    ) -> ModelInfo:
        cq = info_dict.get("conformal_q90")
        return ModelInfo(
            domain=domain,
            project_id=pid,
            metric=metric,
            backend=str(info_dict.get("backend") or "cached"),
            n_samples=int(info_dict.get("n_samples") or 0),
            r2=float(info_dict.get("r2") or 0.0),
            cv_r2=info_dict.get("cv_r2"),
            rmse=float(info_dict.get("rmse") or 0.0),
            trained_at=info_dict.get("trained_at"),
            data_hash=data_hash if data_hash is not None else info_dict.get("data_hash"),
            feature_version=(
                feature_version if feature_version is not None else info_dict.get("feature_version")
            ),
            version_id=version_id,
            conformal_q90=float(cq) if cq is not None else None,
            uncertainty_calibrated=bool(info_dict.get("uncertainty_calibrated")),
            pinned=pinned,
            newer_version_id=newer_version_id,
        )

    def _load_pinned(self, domain: ProductDomain, pid: str, metric: str) -> _Trained | None:
        """The version a rollback pinned for (pid, metric), loaded; None when nothing is pinned.

        A pin whose artifact is gone or unreadable is dropped (with a warning) so the
        metric falls back to normal training instead of silently serving nothing.
        """
        if not self._persist_enabled():
            return None
        vid = model_store.pinned_version_id(pid, metric)
        if vid is None:
            return None
        loaded = model_store.load_model(pid, metric, version_id=vid)
        if loaded is None:
            logger.warning("pinned model %s/%s@%s is unreadable; releasing the pin", pid, metric, vid)
            model_store.release_pin(pid, metric)
            return None
        model, info_dict = loaded
        newest = model_store.newest_version_id(pid, metric)
        try:
            info = self._info_from_artifact(
                domain,
                pid,
                metric,
                info_dict,
                version_id=vid,
                pinned=True,
                newer_version_id=newest if newest and newest != vid else None,
            )
        except Exception as exc:
            logger.warning("pinned ModelInfo invalid for %s/%s: %s", pid, metric, exc)
            return None
        return _Trained(model, info)

    def _fit_and_describe(
        self,
        domain: ProductDomain,
        pid: str,
        metric: str,
        data: tuple[np.ndarray, np.ndarray],
        data_hash: str,
        feat_ver: str,
    ):
        """Train one surrogate on *data*; return ``(model, ModelInfo)`` (not yet persisted)."""
        X, y = data
        model, backend = _make_regressor()
        model.fit(X, y)
        trained_at = _utcnow_iso()
        oof = _kfold_oof(X, y)
        cv_r2 = round(_r2(y, oof), 4) if oof is not None else None
        q90: float | None = None
        if oof is not None:
            resid = np.abs(y - oof)
            n = len(resid)
            q_level = min(1.0, 0.9 * (1.0 + 1.0 / n))
            q90 = float(np.quantile(resid, q_level))
        info = ModelInfo(
            domain=domain,
            project_id=pid,
            metric=metric,
            backend=backend,
            n_samples=len(y),
            r2=round(_r2(y, np.asarray(model.predict(X))), 4),
            cv_r2=cv_r2,
            rmse=round(math.sqrt(np.mean((y - np.asarray(model.predict(X))) ** 2)), 4),
            trained_at=trained_at,
            data_hash=data_hash,
            feature_version=feat_ver,
            conformal_q90=round(q90, 6) if q90 is not None else None,
            uncertainty_calibrated=bool(q90 is not None and q90 > 0),
        )
        return model, info

    def _retrain_all(self) -> None:
        self._models = {}
        keys: set[tuple[str, str]] = set()
        for rec in self._records:
            pid = rec.project_id or rec.domain.value
            for m in rec.measured:
                keys.add((pid, m))
        feat_ver = features.feature_set_version()
        for pid, metric in keys:
            domain = next((r.domain for r in self._records if (r.project_id or r.domain.value) == pid), None)
            if domain is None:
                continue
            data = self._dataset(domain, metric, project_id=pid)
            pinned = self._load_pinned(domain, pid, metric)
            if pinned is not None:
                # A rollback is in force: keep serving it, but still learn from the new data
                # and archive that as a version the user can switch to (or release the pin to).
                self._models[(pid, metric)] = pinned
                if data is not None:
                    self._archive_candidate(domain, pid, metric, data, feat_ver)
                    newest = model_store.newest_version_id(pid, metric)
                    if newest and newest != pinned.info.version_id:
                        pinned.info = pinned.info.model_copy(update={"newer_version_id": newest})
                continue
            if data is None:
                continue
            rows = self._training_rows(domain, metric, project_id=pid)
            data_hash = model_store.compute_data_hash(rows, metric)
            cached = self._try_load_cached(domain, pid, metric, data_hash, feat_ver)
            if cached is not None:
                self._models[(pid, metric)] = cached
                continue
            model, info = self._fit_and_describe(domain, pid, metric, data, data_hash, feat_ver)
            if self._persist_enabled():
                try:
                    vid = model_store.save_model(pid, metric, model, info.model_dump(mode="json"))
                    info = info.model_copy(update={"version_id": vid})
                except Exception as exc:
                    logger.warning("model persist failed for %s/%s: %s", pid, metric, exc)
            self._models[(pid, metric)] = _Trained(model, info)

    def _archive_candidate(
        self,
        domain: ProductDomain,
        pid: str,
        metric: str,
        data: tuple[np.ndarray, np.ndarray],
        feat_ver: str,
    ) -> None:
        """Train on the current data and store it beside a pinned model without taking over.

        Skipped when persistence is off or a version trained on exactly this data already exists.
        """
        if not self._persist_enabled():
            return
        rows = self._training_rows(domain, metric, project_id=pid)
        data_hash = model_store.compute_data_hash(rows, metric)
        if model_store.has_version_for_data(pid, metric, data_hash):
            return
        try:
            model, info = self._fit_and_describe(domain, pid, metric, data, data_hash, feat_ver)
            model_store.save_model(pid, metric, model, info.model_dump(mode="json"), make_current=False)
        except Exception as exc:
            logger.warning("archiving a candidate model failed for %s/%s: %s", pid, metric, exc)

    def train(self) -> list[ModelInfo]:
        with self._lock:
            self._retrain_all()
            return [t.info for t in self._models.values()]

    def list_model_versions(self, project_id: str, metric: str) -> list[dict]:
        """P1 #20: disk versions for one surrogate (newest first)."""
        return model_store.list_versions(project_id, metric)

    def _domain_for(self, project_id: str, info_dict: dict) -> ProductDomain | None:
        domain_raw = info_dict.get("domain")
        try:
            domain = ProductDomain(domain_raw) if domain_raw else None
        except Exception:
            domain = None
        if domain is None:
            domain = next(
                (r.domain for r in self._records if (r.project_id or r.domain.value) == project_id),
                None,
            )
        return domain

    def rollback_model(self, project_id: str, metric: str, version_id: str) -> ModelInfo | None:
        """Serve a prior artifact and pin it.

        Pinning is what makes the rollback stick: without it the very next retrain (an
        experiment submit with ``auto_retrain`` on, ``POST /api/train``, or a restart whose
        data no longer matches the old artifact's hash) overwrote it. Rolling back to the
        newest version is just "use the latest" and pins nothing.
        """
        with self._lock:
            loaded = model_store.load_model(project_id, metric, version_id=version_id)
            if loaded is None:
                return None
            model, info_dict = loaded
            domain = self._domain_for(project_id, info_dict)
            if domain is None:
                return None
            newest = model_store.newest_version_id(project_id, metric)
            pinned = bool(newest) and newest != version_id
            if not model_store.set_current_version(project_id, metric, version_id, pinned=pinned):
                return None
            info = self._info_from_artifact(
                domain,
                project_id,
                metric,
                info_dict,
                version_id=version_id,
                pinned=pinned,
                newer_version_id=newest if pinned else None,
            )
            self._models[(project_id, metric)] = _Trained(model, info)
            return info

    def unpin_model(self, project_id: str, metric: str) -> ModelInfo | None:
        """Release a rollback pin and switch to the newest archived version.

        None when the metric has no archived version at all.
        """
        with self._lock:
            newest = model_store.newest_version_id(project_id, metric)
            if newest is None:
                return None
            loaded = model_store.load_model(project_id, metric, version_id=newest)
            if loaded is None:
                return None
            model, info_dict = loaded
            domain = self._domain_for(project_id, info_dict)
            if domain is None:
                return None
            if not model_store.set_current_version(project_id, metric, newest, pinned=False):
                return None
            info = self._info_from_artifact(domain, project_id, metric, info_dict, version_id=newest)
            self._models[(project_id, metric)] = _Trained(model, info)
            return info

    # --- inference -----------------------------------------------------
    def predict(
        self,
        domain: ProductDomain,
        metric: str,
        feature_vec: list[float],
        *,
        project_id: str = "",
    ) -> tuple[float, int] | None:
        """Return (prediction, n_samples) for a trained metric, else None."""
        with self._lock:
            pid = project_id or domain.value
            trained = self._models.get((pid, metric))
            if trained is None:
                trained = self._models.get((domain.value, metric))
            if trained is None:
                return None
            arr = np.array([feature_vec], dtype=float)
            return float(trained.model.predict(arr)[0]), trained.info.n_samples

    def predict_with_std(
        self,
        domain: ProductDomain,
        metric: str,
        feature_vec: list[float],
        *,
        project_id: str = "",
    ) -> tuple[float, float, int] | None:
        """Return (prediction, std, n_samples) for a trained metric, else None.

        P1 #19: when ``conformal_q90`` is available, ``std`` is at least
        ``q90 / 1.645`` so EI/UCB never trusts under-dispersed tree-std or
        constant ridge RMSE alone.
        """
        with self._lock:
            pid = project_id or domain.value
            trained = self._models.get((pid, metric))
            if trained is None and project_id:
                trained = self._models.get((domain.value, metric))
            if trained is None:
                return None
            arr = np.array([feature_vec], dtype=float)
            pred = float(trained.model.predict(arr)[0])
            raw_std = float(trained.model.predict_std(arr))
            q90 = trained.info.conformal_q90
            if q90 is not None and q90 > 0:
                calibrated = float(q90) / _CONFORMAL_TO_STD
                std = max(raw_std, calibrated)
            else:
                std = raw_std
            return pred, std, trained.info.n_samples

    def predict_interval(
        self,
        domain: ProductDomain,
        metric: str,
        feature_vec: list[float],
        *,
        project_id: str = "",
    ) -> tuple[float, float, float, int] | None:
        """Return (pred, lo, hi, n) using conformal half-width when available."""
        with self._lock:
            pid = project_id or domain.value
            trained = self._models.get((pid, metric))
            if trained is None and project_id:
                trained = self._models.get((domain.value, metric))
            if trained is None:
                return None
            arr = np.array([feature_vec], dtype=float)
            pred = float(trained.model.predict(arr)[0])
            q90 = trained.info.conformal_q90
            if q90 is None or q90 <= 0:
                raw = float(trained.model.predict_std(arr))
                half = raw * _CONFORMAL_TO_STD
            else:
                half = float(q90)
            return pred, pred - half, pred + half, trained.info.n_samples

    def info(self) -> list[ModelInfo]:
        with self._lock:
            return [t.info for t in self._models.values()]


# Global registry — uses configured ExperimentStore (Datalab or SQLite).
# On startup, migrate legacy experiments.json and inline SQL rows if needed.
def _build_registry() -> ModelRegistry:
    from ..db.migrate import migrate_experiments_if_needed

    try:
        migrate_experiments_if_needed()
    except Exception as exc:
        logger.warning("experiment migrate skipped during registry build: %s", exc)
    try:
        return ModelRegistry()
    except Exception as exc:
        logger.warning("ModelRegistry unavailable (%s); using empty registry", exc)
        # Construct without load by temporarily using an empty JSON store.
        from ..db.store import JsonExperimentStore
        import tempfile
        from pathlib import Path

        empty = Path(tempfile.mkdtemp(prefix="fm-empty-exp-")) / "empty.json"
        empty.write_text("[]", encoding="utf-8")
        reg = object.__new__(ModelRegistry)
        reg._lock = threading.RLock()
        reg._store = JsonExperimentStore(str(empty))
        reg._models = {}
        reg._records = []
        return reg


class _RegistryProxy:
    """Lazy :class:`ModelRegistry` built on first use.

    Import-time construction crashed the backend whenever the configured
    ExperimentStore (Datalab) was not yet reachable: ``get_experiment_store``
    raises, uvicorn fails to import the app, and the container restarts in a
    loop until Datalab happens to come up. Deferring construction to first
    actual use lets the API boot, pass health checks, and fail only on the
    specific code path that needs lab data — with a normal request error
    instead of a whole-process crash.

    All call sites use attribute access (``registry.records_for(...)``,
    ``registry.predict(...)``), so a ``__getattr__`` proxy is transparent.
    """

    def __init__(self) -> None:
        self._registry: ModelRegistry | None = None
        self._lock = threading.RLock()

    def _get(self) -> ModelRegistry:
        if self._registry is None:
            with self._lock:
                if self._registry is None:
                    self._registry = _build_registry()
        return self._registry

    def __getattr__(self, name: str):
        return getattr(self._get(), name)

    def __setattr__(self, name: str, value) -> None:
        if name in {"_registry", "_lock"}:
            object.__setattr__(self, name, value)
            return
        # Delegate attribute assignment (e.g. tests swapping
        # ``registry._store``) to the real registry so the proxy stays
        # transparent for both reads and writes.
        setattr(self._get(), name, value)


registry = _RegistryProxy()
