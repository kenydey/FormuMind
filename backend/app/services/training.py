"""Experiment feedback & model training.

Closes the DOE loop: measured lab results are stored, and a data-driven
regression model is trained per (domain, metric). Once enough samples exist the
trained model supersedes the empirical surrogate inside ``predictor.predict``.

Training backend follows the adapter+fallback pattern:
* scikit-learn ``RandomForestRegressor`` when ``scikit-learn`` is installed;
* otherwise a self-contained numpy ridge regressor (standardised features,
  closed-form solution) — no third-party dependency, fully deterministic.

Models are not pickled: the experiment dataset is the source of truth and
models are rebuilt from it on load, which keeps persistence simple and
reproducible.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..domain import features
from ..domain.schemas import ExperimentRecord, ModelInfo, ProductDomain
from ..pipeline import reconstruct  # lightweight: form-from-factors, no cycle


class _RidgeModel:
    """Closed-form ridge regression with feature standardisation."""

    backend = "numpy-ridge"

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self._mean: np.ndarray
        self._std: np.ndarray
        self._w: np.ndarray
        self._b: float = 0.0
        self._rmse: float = 0.0  # training RMSE used as constant uncertainty estimate

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


def _make_regressor():
    """Return (model, backend_name). Prefer sklearn, fall back to numpy ridge."""
    try:  # pragma: no cover - depends on optional extra
        from sklearn.ensemble import RandomForestRegressor

        class _SkModel:
            backend = "sklearn-rf"

            def __init__(self) -> None:
                self._m = RandomForestRegressor(n_estimators=200, random_state=0)

            def fit(self, X, y):
                self._m.fit(X, y)

            def predict(self, X):
                return self._m.predict(X)

            def predict_std(self, X) -> float:
                tree_preds = np.array([t.predict(X)[0] for t in self._m.estimators_])
                return float(np.std(tree_preds))

        return _SkModel(), "sklearn-rf"
    except Exception:
        return _RidgeModel(), "numpy-ridge"


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    if ss_tot < 1e-12:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _kfold_r2(X: np.ndarray, y: np.ndarray, k: int = 5) -> float | None:
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
    return _r2(y, preds)


class _Trained:
    def __init__(self, model, info: ModelInfo) -> None:
        self.model = model
        self.info = info


class ModelRegistry:
    """Stores experiment records and per-(domain, metric) trained models."""

    def __init__(self, path: str | None = None) -> None:
        settings = get_settings()
        self.path = Path(path or settings.experiments_path)
        self.min_samples = settings.min_train_samples
        self._records: list[ExperimentRecord] = []
        self._models: dict[tuple[ProductDomain, str], _Trained] = {}
        self._lock = threading.RLock()
        self.load()

    # --- persistence ---------------------------------------------------
    def load(self) -> None:
        with self._lock:
            self._records = []
            if self.path.exists():
                raw = json.loads(self.path.read_text() or "[]")
                self._records = [ExperimentRecord(**r) for r in raw]
            self._retrain_all()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([r.model_dump() for r in self._records], indent=2))

    def reset(self, persist: bool = False) -> None:
        with self._lock:
            self._records = []
            self._models = {}
            if persist:
                self._persist()

    # --- ingestion -----------------------------------------------------
    def add(self, records: list[ExperimentRecord], retrain: bool = True) -> None:
        with self._lock:
            self._records.extend(records)
            self._persist()
            if retrain:
                self._retrain_all()

    @property
    def total_records(self) -> int:
        return len(self._records)

    # --- training ------------------------------------------------------
    def _dataset(self, domain: ProductDomain, metric: str) -> tuple[np.ndarray, np.ndarray] | None:
        rows, ys = [], []
        for rec in self._records:
            if rec.domain != domain or metric not in rec.measured:
                continue
            form = reconstruct.formulation_from_factors(rec.domain, rec.factors)
            process = {"cure_temperature_c": rec.cure_temperature_c or 0.0}
            rows.append(features.vector(form, process))
            ys.append(rec.measured[metric])
        if len(rows) < self.min_samples:
            return None
        return np.array(rows, dtype=float), np.array(ys, dtype=float)

    def _metrics_for(self, domain: ProductDomain) -> set[str]:
        return {m for rec in self._records if rec.domain == domain for m in rec.measured}

    def _retrain_all(self) -> None:
        self._models = {}
        for domain in ProductDomain:
            for metric in self._metrics_for(domain):
                data = self._dataset(domain, metric)
                if data is None:
                    continue
                X, y = data
                model, backend = _make_regressor()
                model.fit(X, y)
                info = ModelInfo(
                    domain=domain,
                    metric=metric,
                    backend=backend,
                    n_samples=len(y),
                    r2=round(_r2(y, np.asarray(model.predict(X))), 4),
                    cv_r2=(round(v, 4) if (v := _kfold_r2(X, y)) is not None else None),
                    rmse=round(math.sqrt(np.mean((y - np.asarray(model.predict(X))) ** 2)), 4),
                )
                self._models[(domain, metric)] = _Trained(model, info)

    def train(self) -> list[ModelInfo]:
        with self._lock:
            self._retrain_all()
            return [t.info for t in self._models.values()]

    # --- inference -----------------------------------------------------
    def predict(self, domain: ProductDomain, metric: str, feature_vec: list[float]) -> tuple[float, int] | None:
        """Return (prediction, n_samples) for a trained metric, else None."""
        with self._lock:
            trained = self._models.get((domain, metric))
            if trained is None:
                return None
            arr = np.array([feature_vec], dtype=float)
            return float(trained.model.predict(arr)[0]), trained.info.n_samples

    def predict_with_std(
        self, domain: ProductDomain, metric: str, feature_vec: list[float]
    ) -> tuple[float, float, int] | None:
        """Return (prediction, std, n_samples) for a trained metric, else None.

        std is the ensemble std for sklearn-RF; training RMSE for numpy-ridge.
        """
        with self._lock:
            trained = self._models.get((domain, metric))
            if trained is None:
                return None
            arr = np.array([feature_vec], dtype=float)
            pred = float(trained.model.predict(arr)[0])
            std = float(trained.model.predict_std(arr))
            return pred, std, trained.info.n_samples

    def info(self) -> list[ModelInfo]:
        with self._lock:
            return [t.info for t in self._models.values()]


# Global registry used by the API and the predictor.
registry = ModelRegistry()
