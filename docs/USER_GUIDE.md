# FormuMind User Guide

**AI-assisted formulation R&D platform for metal surface treatment** — covering
anti-corrosion coatings, degreasers, and surface treatment agents.

This guide is written for R&D engineers and lab technicians, and walks through
every feature and the end-to-end workflow.

> 中文版见 [使用指南.md](./使用指南.md)。

---

## Table of contents

1. [Overview](#1-overview)
2. [Design principle: adapter + offline fallback](#2-design-principle-adapter--offline-fallback)
3. [UI tour](#3-ui-tour)
4. [The full closed-loop workflow](#4-the-full-closed-loop-workflow)
5. [Feature reference](#5-feature-reference)
6. [DOE feedback & model training](#6-doe-feedback--model-training)
7. [Import & export](#7-import--export)
8. [Session history](#8-session-history)
9. [API reference](#9-api-reference)
10. [Install & run](#10-install--run)
11. [Configuration](#11-configuration)
12. [Enabling the real engines](#12-enabling-the-real-engines)
13. [FAQ & scope notes](#13-faq--scope-notes)

---

## 1. Overview

FormuMind takes an R&D requirement (substrate, salt-spray target, film weight,
cure temperature, cleaning efficiency, VOC limit, …) and runs a complete loop:

```
requirement → patent/literature retrieval → RAG-grounded research
            → recommended formulations → DOE plan → cure/interface simulation
            → Bayesian closed-loop optimization → Top-N formula leaderboard
                          ↑                                  │
                          └──── DOE experimental results ◄───┘
                                (train data-driven models)
```

**Core value**: raise formulation success rate and shorten the development
cycle. As real experimental data accumulates, data-driven models progressively
supersede the empirical surrogate, so recommendations and optimization move
ever closer to measured reality.

**Three product families:**

| Domain | Key metrics |
|--------|-------------|
| `anticorrosion_coating` | salt-spray hours, film weight, adhesion, pencil hardness |
| `degreaser` | cleaning efficiency, foam index, bath life |
| `surface_treatment` | coating weight, salt-spray hours, adhesion-promotion index |

---

## 2. Design principle: adapter + offline fallback

Every heavy external engine sits behind an **adapter** with a **deterministic
offline fallback**. FormuMind therefore **runs end-to-end today** — no GPU, no
API key, no C++ toolchain — and "lights up" the real engine automatically once
it is installed.

| Capability | Real engine (optional) | Built-in fallback |
|------------|------------------------|-------------------|
| Research / chat | Anthropic Claude (`anthropic`) | rule-based synthesis over the knowledge base |
| Patent / literature | `patent_client`, `paper-qa` | curated seed corpus per domain |
| RAG store | OpenNotebook pipeline | in-memory TF-IDF index |
| Property prediction | RDKit + DeepChem/ChemBERTa | transparent empirical surrogate |
| Optimization | Summit (Bayesian/TSEMO) | numpy UCB Bayesian optimizer |
| Stoichiometry | ChemFormula / RDKit | self-contained formula parser |
| Cure / MD simulation | HTPolyNet · LUNAR · LAMMPS (Docker) | analytic approximation |

> The genuinely lightweight, high-value parts — the **DOE engine** (full /
> fractional factorial, Plackett-Burman, central composite, Latin hypercube)
> and the **Bayesian optimizer** — are implemented for real in pure numpy.

---

## 3. UI tour

A dark, industrial three-column layout:

```
┌─────────────────────────────────────────────────────────────────────┐
│  FormuMind · metal surface treatment R&D platform   [🕐 History (n)] │
├──────────────┬─────────────────────────────┬────────────────────────┤
│  Requirements│  AI Research Stream          │  Convergence (chart)   │
│              │  · mechanism                 │                        │
│  · domain    │  · evidence cards            │                        │
│  · substrate │  · chat stream               ├────────────────────────┤
│  · sliders   ├─────────────────────────────┤  Top-N Leaderboard     │
│  · VOC limit │  DOE Feedback                │  · formula cards       │
│              │  · design / generate / import│  · predicted ± std     │
│  [① research]│  · model quality gauges      │  · export JSON/CSV/PDF │
│  [② optimize]│  · run table (fill measured) │                        │
└──────────────┴─────────────────────────────┴────────────────────────┘
```

- **Left (Requirements)**: pick domain and substrate, set target metrics with
  sliders, two primary action buttons.
- **Center top (Research Stream)**: reaction mechanism, evidence cards (sorted by
  relevance, expandable snippets), and the AI research conversation.
- **Center bottom (DOE Feedback)**: choose a DOE design, generate a plan,
  import/export CSV, the model-quality dashboard, and fill in measured values to
  retrain.
- **Right top (Convergence)**: the best-so-far optimization curve after a run
  (placeholder animation when idle).
- **Right bottom (Leaderboard)**: Top-N formula cards with ingredient tables,
  predicted metrics (with ± uncertainty), and an export menu.
- **Header (History)**: clock button + live count badge opens the session
  history drawer.

---

## 4. The full closed-loop workflow

### Step ① — set requirement and retrieve recommendations

1. In the left column, pick a **domain** (e.g. "anti-corrosion coating") and a
   **substrate** (e.g. `carbon_steel`).
2. Drag the sliders to set targets — salt-spray hours, film weight, cure
   temperature, VOC limit (sliders switch with the domain).
3. Click **① research patents & recommend formulations**.

The platform will:
- retrieve patent/literature evidence for the domain (offline seed corpus);
- build a TF-IDF RAG index and recall the most relevant items;
- generate 3 recommended formulation variants from the knowledge base templates
  (high-active / baseline / lean), scored and sorted by the target metric;
- synthesize a mechanism explanation and research conversation.

**Result**: the center shows mechanism + evidence + chat; the leaderboard shows
the 3 recommended formulations.

### Step ② — run the DOE optimization loop

Click **② run DOE optimization loop**. The platform runs an asynchronous
Bayesian multi-objective optimization (24 iterations by default):

- the optimizer samples the design space of key formulation levers (e.g. zinc
  inhibitor loading, resin and hardener ratio);
- each candidate is validated for stoichiometry and scored with the
  empirical/data blended predictor (weighted multi-objective aggregation);
- results feed back into the optimizer and converge.

**Result**: the convergence chart appears top-right; the leaderboard updates to
the Top-5 optimized formulations.

### Step ③ — generate a DOE and feed measured results back

1. In the DOE Feedback area, choose a design type (central composite CCD,
   Plackett-Burman, …) and click **Generate DOE**.
2. The system produces a run table (one row per experiment, natural factor
   values + a blank measured column).
3. Two feedback paths:
   - **Manual**: type the lab-measured metric values directly into the
     "measured" column, then click **③ feed back results and train model**.
   - **Batch**: click **Export CSV** to hand off to the lab → they fill it in →
     click **Import CSV** to upload.
4. Once a metric accumulates ≥ `min_train_samples` (default 4) samples, the
   platform automatically trains a data-driven model for that (domain, metric).

**Result**: the model-quality dashboard shows an R² half-gauge + RMSE;
recommendations and optimization switch to the "empirical + data" blend.

> **The closed loop**: the more measured data, the higher the data-driven model
> weight (blend weight `w = n/(n+8)`), and the closer predictions track reality.

---

## 5. Feature reference

### 5.1 Multi-objective optimization (weighted Pareto aggregation)

Industrial formulation is usually a trade-off — high salt-spray endurance *and*
low cost *and* low VOC. Each domain ships with default objective sets:

| Domain | Default objectives |
|--------|--------------------|
| Anti-corrosion | salt_spray (maximize, 0.5) + cost (minimize, 0.25) + sustainability (maximize, 0.25) |
| Degreaser | cleaning (maximize, 0.5) + cost (minimize, 0.3) + VOC (minimize, 0.2) |
| Surface treatment | salt_spray (maximize, 0.5) + coating weight (maximize, 0.2) + cost (minimize, 0.3) |

Each objective is min-max normalized then weighted; "minimize" objectives are
inverted so higher is always better. You can override metrics, weights and
directions via the API's `objectives` field.

### 5.2 Cost & sustainability scoring

All 26 raw materials in the knowledge base carry `price_cny_per_kg` and
`voc_contrib` (volatile fraction, 0–1). Every prediction computes:

- `cost_cny_per_kg` — mass-fraction-weighted formulation cost;
- `voc_gpl` — VOC content (g/L, assuming density ~1.3 kg/L);
- `sustainability_idx` — sustainability index (0–100, penalizing high VOC and cost).

When a formulation's VOC exceeds the requirement's `voc_limit_gpl`, the card
shows an amber warning.

### 5.3 Prediction confidence intervals

Every predicted metric carries a `predicted_std` uncertainty estimate:

- **sklearn random forest**: standard deviation across individual tree
  predictions (ensemble uncertainty);
- **numpy ridge fallback**: training-set RMSE as a conservative constant.

Cards show `value ± std`; when std exceeds 20% of the value it is rendered amber
to flag low-confidence predictions.

### 5.4 Convergence visualization

The optimizer returns the best-so-far curve (`history`), rendered as a recharts
line chart — X axis = iteration, Y axis = best objective score, hover for exact
values.

### 5.5 Model-quality dashboard

Each trained model shows a half-circle SVG R² gauge (green >85% / amber >60% /
red otherwise) plus backend type (sklearn-rf or numpy-ridge), sample count,
RMSE, and cross-validated R².

---

## 6. DOE feedback & model training

This is the heart of the platform. The empirical predictor is only a prior;
real experimental data is the source of truth.

### DOE design types

| Design | Use |
|--------|-----|
| `full_factorial` | full exploration when factors are few |
| `fractional_factorial` | fewer runs at some confounding |
| `plackett_burman` | main-effect screening, fewest runs |
| `ccd` | central composite, builds a quadratic response surface |
| `lhs` | Latin hypercube, uniform space filling |

### Training mechanism

1. Each experiment record is featurized (`features.py`) into a role-based
   composition + process vector.
2. Once a (domain, metric) accumulates ≥ `FORMUMIND_MIN_TRAIN_SAMPLES`
   (default 4) samples, a model is trained:
   - scikit-learn `RandomForestRegressor` when installed;
   - otherwise a self-contained numpy ridge regressor.
3. `predictor.predict` **blends** the trained model with the empirical prior,
   weighting the model more as samples grow (`w = n/(n+8)`).
4. **The dataset is the source of truth**: models are not pickled — they are
   rebuilt from the persisted dataset on startup, keeping persistence simple and
   reproducible.

---

## 7. Import & export

### DOE worksheet export (B3)

- In the DOE area, click **Export CSV** or **XLSX** to download a worksheet with
  blank `measured_<metric>` columns for the lab to fill in.
- API: `GET /api/doe/{plan_id}/export?format=csv|xlsx`
- XLSX requires the optional `openpyxl` (`[export]` extra); CSV uses only the
  standard library and is always available.

### Experiment-result CSV import (B3)

- In the DOE area, click **Import CSV** to upload a filled-in worksheet for bulk
  ingestion and automatic retraining.
- API: `POST /api/experiments/import-csv`
- Parsing: `measured_*` columns become measured values, `cure_temperature_c` is
  routed to the process field, unfilled rows are skipped, and Excel's UTF-8 BOM
  is tolerated.

### Formula export (F2)

The **Export ▾** menu on each leaderboard card offers:

- **Copy JSON** — copy the full formulation object to the clipboard;
- **Export CSV** — ingredient rows + predicted metrics as a table;
- **Export PDF** — a one-page report card (name, ingredient table, predicted
  metrics with uncertainty, rationale). jsPDF is lazy-loaded so it never bloats
  the initial bundle.

---

## 8. Session history (F5)

- After every completed **research, optimization, or feedback** run, the
  platform automatically saves a session snapshot (requirement + leaderboard +
  models + convergence curve + timestamp).
- Click the header **🕐 History** button to open the right-side drawer listing
  the last 20 sessions.
- Each entry shows the domain label, time, Top-1 formula name and score; click
  to **restore** that session state in one tap.
- A **clear** action wipes history. Data lives in browser localStorage, so it
  survives a page refresh with no backend required.

---

## 9. API reference

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/research` | retrieve prior art + RAG + recommended formulations |
| POST | `/api/doe?design=…` | generate a DOE plan (5 designs) |
| GET | `/api/doe/{plan_id}/export?format=csv\|xlsx` | export a fill-in worksheet (blank measured columns) |
| POST | `/api/optimize` | start the async multi-objective optimizer → returns `task_id` |
| GET | `/api/tasks/{id}` | poll task progress + result (Top-N leaderboard) |
| POST | `/api/experiments` | feed back measured results → persist + (re)train |
| POST | `/api/experiments/import-csv` | upload a filled-in worksheet → bulk-ingest + train |
| POST | `/api/train` | force a retrain over all stored experiments |
| GET | `/api/models` | list trained models with `n_samples`, `R²`, `cv_R²`, `RMSE` |
| GET | `/api/ingredients` | full raw-material library incl. price & VOC contribution |
| GET | `/api/meta`, `/api/templates/{domain}` | metadata & baseline templates |
| GET | `/health` | service + active-engine status |

Interactive docs: after starting the backend, visit `http://localhost:8000/docs`.

### Feedback request example

```bash
curl -X POST localhost:8000/api/experiments -H 'content-type: application/json' -d '{
  "records": [
    {"domain": "anticorrosion_coating",
     "factors": {"Zinc phosphate": 12, "Bisphenol-A epoxy (DGEBA)": 38, "Polyamide hardener": 14},
     "cure_temperature_c": 80,
     "measured": {"salt_spray_hours": 980}}
  ]
}'
```

### Custom multi-objective optimization example

```bash
curl -X POST localhost:8000/api/optimize -H 'content-type: application/json' -d '{
  "requirement": {
    "domain": "anticorrosion_coating",
    "salt_spray_hours": 800,
    "objectives": [
      {"metric": "salt_spray_hours", "weight": 0.7, "direction": "maximize"},
      {"metric": "cost_cny_per_kg",  "weight": 0.3, "direction": "minimize"}
    ]
  },
  "iterations": 30
}'
```

---

## 10. Install & run

### Local (no Docker, fully offline)

```bash
# Backend
cd backend
pip install -e ".[dev]"
pytest -q                          # 58 tests, all offline
uvicorn app.main:app --reload      # http://localhost:8000/docs

# Frontend (separate shell)
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

### Docker

```bash
cp .env.example .env               # optional: add ANTHROPIC_API_KEY
docker compose up                  # redis + backend + worker + frontend
docker compose --profile heavy up  # also start LAMMPS / HTPolyNet engines
```

---

## 11. Configuration

All settings are environment-driven (prefix `FORMUMIND_`) with safe offline
defaults.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | empty | Claude key; falls back to offline synthesis when unset |
| `FORMUMIND_LLM_MODEL` | `claude-fable-5` | LLM model |
| `FORMUMIND_DB_URL` | `sqlite:///./data/formumind.db` | experiment database; can point at Postgres |
| `FORMUMIND_REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `FORMUMIND_CELERY_EAGER` | `true` | run tasks in-process without a broker |
| `FORMUMIND_OPTIMIZE_ITERATIONS` | `24` | optimization iterations |
| `FORMUMIND_TOP_N_FORMULAS` | `5` | leaderboard size |
| `FORMUMIND_MIN_TRAIN_SAMPLES` | `4` | min samples before training a metric's model |
| `FORMUMIND_AUTO_RETRAIN` | `true` | retrain automatically on new experiments |

### Data persistence (B5)

- Experiment records are persisted in a SQL database (SQLite by default,
  zero-config, file-backed).
- On startup, a legacy `experiments.json` is **automatically migrated** into
  SQLite (idempotent; the original file is kept as an audit trail).
- For multi-process deployments, point `FORMUMIND_DB_URL` at Postgres — no code
  changes required.

---

## 12. Enabling the real engines

Install the corresponding extras on a capable machine and the adapters switch
over automatically — no code change:

```bash
pip install -e ".[llm]"      # Anthropic Claude
pip install -e ".[science]"  # scipy, scikit-learn, RDKit, ChemFormula
pip install -e ".[intel]"    # patent_client, paper-qa, chemcrow
pip install -e ".[heavy]"    # torch, deepchem, transformers, summit, ase
pip install -e ".[export]"   # openpyxl (XLSX export; CSV needs nothing)
```

After installing the `science` extra:
- property prediction adds RDKit molecular descriptor features;
- model training upgrades from numpy ridge to scikit-learn random forest (with
  ensemble uncertainty);
- stoichiometry validation switches to ChemFormula for exact computation.

---

## 13. FAQ & scope notes

**Q: Does it work without an API key?**
Yes. Everything runs end-to-end fully offline; only LLM research synthesis is
replaced by the knowledge-base rule engine.

**Q: Are the predicted performance numbers trustworthy?**
The offline numbers are **engineering-reasonable screening estimates**, not
lab-validated specifications. Feed real DOE results back so data-driven models
progressively supersede the empirical prior.

**Q: Where is the 3D simulation?**
This version keeps 3D visualization as a skeleton + data contract only; real
OVITO/3Dmol trajectory rendering needs HTPolyNet/LAMMPS (Docker `heavy`
profile). The right column currently shows the optimization convergence chart.

**Q: Is patent retrieval a live online crawl?**
By default it uses an offline seed corpus per domain. Adapters for real
USPTO/EPO retrieval are in place but need the `intel` extra (`patent_client`,
etc.) and the corresponding key flow.

**Q: Where is session history stored?**
Browser localStorage (up to 20 entries), never uploaded to the server.
Experiment data is persisted in the backend SQLite/Postgres database.

---

> This document corresponds to FormuMind v0.2 (ten upgrades: multi-objective
> optimization, cost/sustainability, confidence intervals, DOE import/export,
> SQL persistence, evidence panel, formula export, convergence chart, model
> dashboard, and session history).
