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
            → recommended formulations → IP novelty / risk check
            → DOE plan → cure/interface simulation
            → Bayesian closed-loop optimization → Top-N formula leaderboard
            → process-parameter optimization
                          ↑                                  │
                          └──── DOE experimental results ◄───┘
                                (train data-driven models)
                          ↑                                  │
                          └──── one-click self-driving loop ─┘
                                (data → retrain → optimize → next active-learning DOE)
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
| Research / chat | 9 LLM providers — Claude, OpenAI, Gemini, Grok, Meta (Groq), DeepSeek, Qwen, Kimi, MiniMax | rule-based synthesis over the knowledge base |
| NL intent parsing | configured LLM (`complete_json`) | regex/keyword heuristic |
| Patent search | `patent_client` | curated seed corpus per domain |
| Literature search | `arxiv`, `semanticscholar` | (offline returns no extra hits) |
| Internet search | `duckduckgo-search` | (offline returns no extra hits) |
| **NotebookLM source** | `notebooklm-py` (browser-session auth) | (off → no extra hits) |
| File ingestion | `markitdown` (PDF/DOCX/XLSX/PPTX/HTML/images…) → `pypdf`/`python-docx` | plain-text decoder |
| RAG store | sentence-transformers embedding + chromadb | in-memory TF-IDF index |
| Grounded Q&A | ChemCrow agent (chemistry questions) · paper-qa (semantic synthesis) | TF-IDF re-rank → configured LLM → snippet |
| Property prediction | RDKit (8 descriptors) · MoLFormer (reserved) | transparent empirical surrogate |
| Rheology / Tg | Fox equation + Mooney viscosity (knowledge-base `tg_k`) | skipped if data missing |
| Color (CIELAB / ΔE₀₀) | `colour-science` | (skipped) |
| VOC / density | `thermo` mass-weighted density | nominal 1.3 kg/L assumption |
| Compound data | PubChemPy (SMILES / molar mass) | hand-curated raw-material library |
| Stoichiometry & safety | ChemFormula + acid/base, SVHC, VOC-category checks | self-contained formula parser + rule checks |
| Optimization | BoTorch GP (qNEHVI) · Summit (Bayesian/TSEMO) · Optuna (NSGA-II/TPE, CPU) | numpy UCB Bayesian optimizer |
| Active-learning DOE | trained surrogate + EI on DOE grid | random LHS sampling |
| IP analysis | LLM JSON (`complete_json`) over retrieved patents | offline keyword overlap → risk tag |
| Process optimizer | shared engine over manufacturing parameters | Arrhenius / empirical outcome models |
| Cure / MD simulation | HTPolyNet · LUNAR · LAMMPS (Docker) | analytic approximation |

> The genuinely lightweight, high-value parts — the **DOE engine** (full /
> fractional factorial, Plackett-Burman, central composite, Latin hypercube,
> AI-active selection) and the **Bayesian optimizer** — are implemented for
> real in pure numpy.

---

## 3. UI tour

A dark, industrial NotebookLM-style three-column layout that separates **inputs
→ research → outputs**:

```
┌──────────────────────────────────────────────────────────────────────┐
│  FormuMind · metal surface treatment R&D platform  [⚙ Settings] [🕐 History] │
├──────────────────┬───────────────────────────┬───────────────────────┤
│  Sources (left)  │  Research (center)         │  Actions (right)      │
│                  │                            │                       │
│ · research topic │  RAG-grounded Q&A:         │ 🧪 Requirements       │
│   (prompt box)   │  · chat with the loaded    │ ⭐ Recommend          │
│ · source types:  │    sources                 │ 🔬 DOE Design         │
│   ☑ patents      │  · citation chips per      │ 📈 Optimization       │
│   ☑ literature   │    answer                  │ ⚙️ Process Optimization│
│   ☑ internet     │                            │ 🔄 Self-Driving Loop  │
│   ☑ local files  │  [ask the sources… ][send] │                       │
│   ☐ 📓 NotebookLM│                            │  (each opens a modal) │
│ [⬆ upload][search]│                            │                       │
│ ── loaded (N) ── │                            │                       │
│ 📄 patent · ✕    │                            │                       │
│ 📚 arxiv  · ✕    │                            │                       │
│ 📓 NotebookLM ✕ │                            │                       │
│ 📎 local  · ✕    │                            │                       │
└──────────────────┴───────────────────────────┴───────────────────────┘
```

- **Left (Sources)**: a **research-topic prompt box** at the top (sets the
  context for searches and Q&A), checkboxes to choose which source types to
  search (patents / literature / internet / local files / **📓 NotebookLM**), a
  file-upload button, a **Search** button, and the list of loaded sources (each
  removable). Each source-type checkbox carries a **status dot**: green = library
  installed and available, yellow = offline fallback active (patents always have
  a curated seed corpus), red = library missing. Below the Search button, a
  **🔬 Deep Research** button triggers `POST /api/research/deep` — the
  KnowledgeCohort multi-agent pipeline (`web_agent` + `kb_agent` with HyDE query
  expansion + LLM re-rank + `report_agent` with cross-validation and forced
  per-claim citations); the result is an async task whose output appears in the
  center column automatically. When the **Literature** or **Internet** source is
  selected, a **🧪 ChemCrow badge** indicates whether chemistry-enhanced
  retrieval (SerpAPI + paper-qa) is enabled. If a search fails (e.g. the backend
  is not running), a red **error banner** with a backend startup hint is shown at
  the top of the panel.
- **Center (Research)**: a chat interface that answers questions **grounded in
  the loaded sources** (semantic embedding or TF-IDF re-rank → LLM answer), with
  citation chips linking back to the evidence used.
- **Right (Actions)**: six buttons that each open a focused **modal** —
  🧪 Requirements (with the **✨ NL Intent** parser at the top),
  ⭐ Recommend (AI-recommended Top-N + per-card 🔍 **IP analysis**),
  🔬 DOE Design (5 designs + 🧠 AI active selection, fill measured values,
  retrain, model gauges),
  📈 Optimization (Bayesian loop, convergence chart),
  ⚙️ Process Optimization (cure temperature/time, dispersion RPM, film
  thickness, bath temperature, pH, …),
  🔄 Self-Driving Loop (data → retrain → optimize → next active-learning DOE
  in one click). Status badges on each button show running / result counts.
- **Header**: ⚙ **Settings** (LLM provider, model, API key, base URL) and
  🕐 **History** (session snapshot drawer, with a live count badge).

---

## 4. The full closed-loop workflow

### Step ⓪ — load sources and research the topic (left + center)

1. In the **left column**, type your research topic in the prompt box (e.g.
   "waterborne anti-corrosion coating with low VOC").
2. Tick the source types to search — **patents**, **literature** (arXiv +
   Semantic Scholar), **internet** (DuckDuckGo), **📓 NotebookLM** — and/or
   **upload local files** (PDF/DOCX/XLSX/PPTX/HTML/images, parsed via
   markitdown). Click **Search**.
3. Loaded sources appear in the list below. In the **center column**, ask
   questions about them — answers are grounded in the loaded evidence and cite
   the sources used.

> Offline, the patent search returns the curated seed corpus; literature,
> internet and NotebookLM search need their optional extras (see §12).

> After loading, the **status dots** on each source-type checkbox reflect which
> sources are actually available in the current environment. For a fully automated
> multi-source run, use the **🔬 Deep Research** button below the Search button:
> it launches the KnowledgeCohort pipeline (web + KB + HyDE + re-rank +
> cross-validation report) without requiring manual Q&A in the center column.

### Step ① — set requirement and retrieve recommendations (right-column modals)

The research functions live in the **right column** as buttons; each opens a
modal popup so you can focus on one task at a time.

1. Open **🧪 Requirements**. At the top, the **✨ NL Intent** box accepts a
   one-sentence brief — *"Waterborne epoxy anti-corrosion coating, 1000 h salt
   spray, cures at 120 °C"* — and parses it into structured fields (LLM when
   configured, else regex fallback) that auto-populate the form below.
2. Adjust the **domain** (e.g. "anti-corrosion coating"), **substrate** (e.g.
   `carbon_steel`), objectives, weights, directions and constraints (e.g.
   VOC limit).
3. Open **⭐ Recommend** and click **research patents & recommend formulations**.

The platform will:
- retrieve patent/literature evidence for the domain (offline seed corpus);
- build a semantic-embedding (or TF-IDF) RAG index and recall the most relevant
  items;
- generate 3 recommended formulation variants from the knowledge base templates
  (high-active / baseline / lean), scored and sorted by the target metric;
- synthesize a mechanism explanation and research conversation.

**Result**: the center shows mechanism + evidence + chat; the leaderboard shows
the 3 recommended formulations. Expand a card to see its ingredient table,
predicted metrics with uncertainty (incl. **PVC / CPVC**, **Tg (°C)**,
**viscosity_relative**, and optional **lab_L/a/b + ΔE₀₀**), the **3D
molecular-viewer panel** (see §5.10), and a **🔍 IP compliance analysis**
button (see §5.11).

![Recommend · leaderboard with molecular viewer](./images/05-recommend.png)

### Step ② — run the optimization loop

Open the **📈 Optimization** modal and click **run optimization loop**. The
platform runs an asynchronous Bayesian multi-objective optimization (24
iterations by default):

- the optimizer auto-selects the strongest installed engine (**BoTorch** →
  **Summit** → **Optuna** → built-in numpy UCB);
- it samples the design space of key formulation levers (e.g. zinc inhibitor
  loading, resin/hardener ratio);
- each candidate is validated for stoichiometry and scored with the
  empirical/data blended predictor (weighted multi-objective aggregation);
- results feed back into the optimizer and converge.

**Result**: the convergence chart appears in the modal; the leaderboard updates
to the Top-5 optimized formulations.

### Step ③ — generate a DOE and feed measured results back

1. In the **🔬 DOE Design** modal, choose a design type (central composite CCD,
   Plackett-Burman, …, or **🧠 AI active selection**) and click **Generate DOE**.
   AI-active rows are highlighted in violet — these are the points expected
   improvement (EI) flagged as most informative.
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

### Step ④ — process-parameter optimization

Open the **⚙️ Process Optimization** modal. The platform optimizes **manufacturing
parameters** (cure temperature, cure time, dispersion RPM, film thickness, bath
temperature, immersion time, pH set-point, accelerator factor) using the same
Bayesian engine but on the *process* design space. Domain-specific outcome
models (Arrhenius cure conversion, Q10 cleaning, phosphating power-law) score
each candidate. The result shows the optimal process recipe and a mini
convergence chart.

### Step ⑤ — one-click self-driving loop

Open **🔄 Self-Driving Loop** and click **iterate one round**. In a single async
task the platform:

1. pulls every measured record for the active domain from the registry;
2. retrains the data-driven model (RMSE / R² are reported per metric);
3. runs the optimizer (engine is auto-selected — BoTorch / Summit / Optuna / numpy);
4. uses the freshly-retrained surrogate to compute EI on the DOE grid and
   suggests the next active-learning batch.

The modal shows engine, sample count, R²/RMSE cards (with a ↓ trend arrow as
RMSE drops over rounds), the convergence chart, and the next DOE batch (AI rows
highlighted in violet). Export the next batch as CSV and the loop continues
across rounds.

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

### 5.2 Cost, sustainability & PVC / CPVC scoring

All 26 raw materials in the knowledge base carry `price_cny_per_kg` and
`voc_contrib` (volatile fraction, 0–1). Every prediction computes:

- `cost_cny_per_kg` — mass-fraction-weighted formulation cost;
- `voc_gpl` — VOC content (g/L); density defaults to ~1.3 kg/L but is computed
  from a real mass-weighted mixture density when the `thermo` library is present;
- `sustainability_idx` — sustainability index (0–100, penalizing high VOC and cost);
- **`pvc`** — pigment volume concentration (%); volume fraction of pigments and
  fillers vs. total non-volatile;
- **`cpvc`** — critical PVC, computed from the Asbeck formula using oil
  absorption from the knowledge base;
- **`pvc_to_cpvc_ratio`** — ratio used to flag over-filled coatings (poor gloss
  / sealing when > 1).

When a formulation's VOC exceeds the requirement's `voc_limit_gpl`, the card
shows an amber warning. The safety checker also raises warnings for acid/base
conflicts and known REACH SVHC ingredients.

### 5.3 Rheology, Tg & color

- **Tg (°C)** — multi-component glass transition temperature via the Fox
  equation, using `tg_k` values curated on resins/hardeners. Returns `None`
  when any component lacks `tg_k`.
- **viscosity_relative** — Mooney pigment-volume viscosity model
  (`η_r = exp(2.5·φ / (1 − φ/φ_max))`, `φ_max ≈ 0.64`).
- **viscoelastic_index** — 0–1 index combining PVC/CPVC and Tg distance from
  room temperature.
- **lab_L / lab_a / lab_b + delta_e** — CIELAB color & CIEDE2000 vs. white
  reference, computed from `spectral_reflectance` on pigments when the
  `colour-science` library is installed (the leaderboard card renders a 2 rem
  swatch when these fields are present).

### 5.4 Prediction confidence intervals

Every predicted metric carries a `predicted_std` uncertainty estimate:

- **sklearn random forest**: standard deviation across individual tree
  predictions (ensemble uncertainty);
- **numpy ridge fallback**: training-set RMSE as a conservative constant.

Cards show `value ± std`; when std exceeds 20% of the value it is rendered amber
to flag low-confidence predictions.

### 5.5 Convergence visualization

The optimizer returns the best-so-far curve (`history`), rendered as a recharts
line chart — X axis = iteration, Y axis = best objective score, hover for exact
values. The same chart is reused inside the Self-Driving Loop modal.

### 5.6 Model-quality dashboard

Each trained model shows a half-circle SVG R² gauge (green >85% / amber >60% /
red otherwise) plus backend type (sklearn-rf or numpy-ridge), sample count,
RMSE, and cross-validated R².

### 5.7 Multi-LLM provider support

The research synthesis and grounded Q&A can run on any of **nine LLM
providers**, selectable from the ⚙ **Settings** dialog (or via environment
variables). All seven OpenAI-compatible providers share the single `openai` SDK
with a per-provider `base_url`; Claude uses `anthropic` and Gemini uses
`google-genai`.

| Provider | Recommended model | SDK / base URL |
|----------|-------------------|----------------|
| Anthropic (Claude) | `claude-sonnet-4-6` | `anthropic` |
| OpenAI | `gpt-4o` | `openai` |
| Google (Gemini) | `gemini-2.0-flash` | `google-genai` |
| xAI (Grok) | `grok-2` | `openai` · `https://api.x.ai/v1` |
| Meta (via Groq) | `llama-3.3-70b-versatile` | `openai` · `https://api.groq.com/openai/v1` |
| DeepSeek | `deepseek-v4-pro` (flagship, recommended) / `deepseek-v4-flash` (fast, economical) | `openai` · `https://api.deepseek.com` |
| Qwen (通义千问) | `qwen-plus` | `openai` · `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Kimi (Moonshot) | `moonshot-v1-128k` | `openai` · `https://api.moonshot.cn/v1` |
| MiniMax | `abab6.5s-chat` | `openai` · `https://api.minimax.chat/v1` |

In Settings, pick a provider and model, paste an API key (with show/hide), set a
custom base URL if needed, and click **Save & test connection**. The key is
synced to the backend for the session and persisted in browser localStorage.
With no key configured, everything still works via the offline rule engine.

### 5.8 Multi-source research, file upload & grounded Q&A

The research step is split into selectable sources, all behind adapters with
offline fallbacks:

- **Patents** — `patent_client` (offline: curated seed corpus per domain);
- **Literature** — arXiv + Semantic Scholar;
- **Internet** — DuckDuckGo (no API key required);
- **NotebookLM** — `notebooklm-py` direct library: a single fixed Google
  NotebookLM notebook is queried via the unofficial SDK. One-time browser
  sign-in (`notebooklm login`) writes a session file; the adapter then maps
  `chat.ask` results to `Evidence(source="NotebookLM")`. Disabled by default,
  silently returns `[]` when off, not logged in, or the library is missing.
- **Local files** — upload PDF/DOCX/XLSX/PPTX/HTML/images; **markitdown**
  converts them to text (falling back to `pypdf` / `python-docx`), then splits
  the text into evidence chunks.
- **Deep Research** — the 🔬 button in the left panel runs `POST /api/research/deep`, an async
  KnowledgeCohort pipeline: `web_agent` (DuckDuckGo), `kb_agent` (HyDE query expansion →
  vector/TF-IDF re-rank → `llm_rerank` → `answer_question`), and `report_agent`
  (cross-validates web vs. KB sources, enforces per-claim `[source]` citations and
  marks insufficient-evidence gaps). The result is a structured `ComprehensiveReport`
  with `report_markdown`, `citations`, and `candidates` that auto-populate the center
  column and leaderboard.

Results from all selected sources are merged, de-duplicated, and ranked by
relevance into the left-column source list. The center-column chat then answers
questions **grounded in those sources**: a semantic-embedding (with
`sentence-transformers`) or TF-IDF re-rank selects the most relevant evidence,
the LLM composes an answer, and the citations used are shown as chips under
each reply.

### 5.9 Optional intelligence engines (auto-detected)

Beyond the LLM providers, FormuMind layers several specialist engines on top of
the built-in fallbacks. Each is **auto-detected**: install its library and the
adapter switches over with no code change; absent, the platform keeps using the
deterministic offline path, so behaviour never breaks.

- **Optimizer tiering** — the closed-loop optimizer auto-selects the best engine
  installed: **BoTorch** (GP + qNEHVI, the `bo` extra) → **Summit**
  (Bayesian/TSEMO, the `heavy` extra) → **Optuna** (NSGA-II/TPE, CPU-only, the
  lightweight `optimize` extra) → the built-in numpy UCB optimizer. The `engine`
  used is reported on the optimization result.
- **Active-learning DOE** — when ≥ `min_train_samples` records exist, the DOE
  modal's *🧠 AI active selection* uses the trained surrogate's expected
  improvement on the DOE grid; otherwise it falls back to random LHS.
- **Grounded-Q&A routing** — a chemistry-flavoured question (LogP, solubility,
  toxicity, compatibility, reaction, structure…) is routed to the **ChemCrow**
  agent when installed; other questions use **paper-qa** semantic synthesis with
  page-level citations. Both fall back to the TF-IDF re-rank → configured LLM →
  snippet path.
- **ChemCrow availability badge** — when the `intel` extra is installed and
  **Literature** or **Internet** is selected, the Sources panel shows a green
  🧪 badge ("ChemCrow chemistry-enhanced retrieval · enabled"); when `intel` is
  absent, a grey badge with a `pip install -e '.[intel]'` hint is displayed
  instead.
- **Semantic RAG** — with `sentence-transformers` installed (the `embedding`
  extra), the RAG store upgrades from TF-IDF to MiniLM embeddings + chromadb,
  significantly improving recall on synonyms (e.g. "epoxy" ↔ "bisphenol-A").
- **Compound enrichment** — set `FORMUMIND_ENRICH_COMPOUNDS=true` and, with
  **PubChemPy** installed, the platform backfills missing SMILES / molar-mass on
  the raw-material library at startup (curated values always win).
- **Physically-grounded VOC + Tg + viscosity** — with **thermo** installed,
  `voc_gpl` is computed from a real mass-weighted mixture density; the Fox &
  Mooney models pick up `tg_k` values curated on resins.
- **Color metrology** — with **`colour-science`** installed (the `color`
  extra), CIELAB and CIEDE2000 ΔE₀₀ are computed from spectral data; the
  leaderboard card renders a real color swatch.
- **NotebookLM source** — with **`notebooklm-py`** installed (the `notebooklm`
  extra) and `FORMUMIND_NOTEBOOKLM_ENABLED=true`, queries against the
  configured notebook return as `source="NotebookLM"` evidence.

### 5.10 NL intent parser (✨)

The top of **🧪 Requirements** has a *✨ NL Intent · 智能解析* box. Paste a
one-sentence project brief and the platform extracts a structured `Requirement`:

- with an LLM configured, the parser uses `complete_json()` (the same shared
  JSON-extraction helper that powers IP analysis);
- without an LLM, a fully deterministic regex/keyword fallback handles domain
  detection (anti-corrosion / degreaser / surface-treatment), substrate
  (carbon/galvanized/aluminum/stainless/magnesium), salt-spray hours, VOC limit
  (200 ≤ g/L for "low VOC / waterborne") and cure temperature.

The response lists the fields it filled, and the form below is updated in place.

### 5.11 IP compliance analysis (🔍)

Each leaderboard card has a **🔍 IP 合规分析** button. The platform:

- extracts chemical-term keywords from the ingredient list;
- retrieves up to N relevant patents from `literature.search_patents()`;
- with an LLM configured, calls `complete_json()` with a prompt asking for
  `{novelty_score, risks[], whitespace_hints[]}`;
- without an LLM, falls back to a deterministic keyword-overlap risk tagger.

The result modal shows a novelty gauge (0–1), per-patent risk cards with claim
overlap and remediation hints, and white-space opportunities for differentiation.

### 5.12 Process-parameter optimization (⚙️)

Manufacturing knobs — cure temperature/time, dispersion RPM, film thickness,
bath temperature, immersion time, pH, accelerator factor — are an independent
design space from the formulation composition. The **Process Optimization**
modal runs the same Bayesian engine over this space; domain-specific outcome
models score each candidate:

- **anti-corrosion**: Arrhenius cure conversion, dispersion uniformity, salt-spray
  thickness factor;
- **degreaser**: temperature-corrected cleaning efficiency, foam decay;
- **surface treatment**: phosphating power-law mass deposition.

### 5.13 Self-driving loop (🔄)

The Self-Driving Loop modal wires together every part of the platform into a
one-click iteration:

```
records (per domain) → retrain → optimize (auto-selected engine)
                              ↓
                  RMSE / R² → next active-learning DOE batch
```

It's a thin orchestrator over existing pieces (`registry.records_for`,
`workflow.run_optimization`, `active_learning_doe`); when no measured data
exists yet it degrades gracefully to empirical prior + LHS sampling.

### 5.14 3D molecular viewer (reserved)

Each expanded leaderboard card carries a **3D molecular-viewer panel**. In this
release it is a **placeholder + data contract**: it lists the formulation
components that carry a SMILES string and notes that they will be rendered as
ball-and-stick models via **3Dmol.js**. The full WebGL viewer (and the reserved
**MoLFormer** embedding path for richer property prediction) ships in a later
upgrade; the placeholder keeps the bundle lightweight today.

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
| `active` | 🧠 AI active selection — EI on the DOE grid (uses the trained surrogate; LHS fallback when sample count is below the min) |

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

- After every completed **research, optimization, feedback or loop** run, the
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
| POST | `/api/search` | multi-source retrieval (patents / literature / internet / NotebookLM) → merged, de-duped evidence |
| POST | `/api/ingest` | upload a local file → extracted evidence chunks |
| POST | `/api/chat` | Q&A grounded in the loaded sources (semantic / TF-IDF re-rank → LLM answer with citations) |
| GET/POST | `/api/settings` | read / update the active LLM provider, model, key, base URL (`POST /api/settings/test` checks the connection) |
| POST | `/api/intent/parse` | natural-language project brief → structured `Requirement` (LLM `complete_json` or regex fallback) |
| POST | `/api/research` | retrieve prior art + RAG + recommended formulations (accepts optional pre-loaded `sources`) |
| POST | `/api/research/deep` | async deep-research: KnowledgeCohort multi-agent pipeline (web + KB + HyDE + re-rank + cross-validation report) → returns `task_id` |
| GET | `/api/search/status` | lightweight per-source availability check (no retrieval, no network) |
| POST | `/api/agents/review` | multi-agent formulation review: ChemistAgent (RDKit + water-incompatibility rules) + InspectorAgent (SVHC/VOC) + InitializeAgent supervisor → `ReviewVerdict` |
| POST | `/api/ip/analyze` | per-formula novelty score, infringement-risk list, white-space hints |
| POST | `/api/doe?design=…` | generate a DOE plan (5 designs) |
| POST | `/api/doe/active` | 🧠 active-learning DOE: surrogate EI selection (LHS fallback) |
| GET | `/api/doe/{plan_id}/export?format=csv\|xlsx` | export a fill-in worksheet (blank measured columns) |
| POST | `/api/optimize` | start the async multi-objective optimizer → returns `task_id` |
| POST | `/api/process-optimize` | optimize manufacturing process parameters (Arrhenius / empirical outcome models) |
| POST | `/api/loop/iterate` | one-click self-driving loop: data → retrain → optimize → next active-learning DOE → returns `task_id` |
| POST | `/api/qc/analyze` | (reserved) computer-vision QC analysis stub |
| GET | `/api/tasks/{id}` | poll task progress + result (Top-N leaderboard, loop report, …) |
| POST | `/api/experiments` | feed back measured results → persist + (re)train |
| POST | `/api/experiments/import-csv` | upload a filled-in worksheet → bulk-ingest + train |
| POST | `/api/train` | force a retrain over all stored experiments |
| GET | `/api/models` | list trained models with `n_samples`, `R²`, `cv_R²`, `RMSE` |
| GET | `/api/ingredients` | full raw-material library incl. price & VOC contribution |
| GET | `/api/meta`, `/api/templates/{domain}` | metadata & baseline templates |
| GET | `/health` | service + active-engine status |

Interactive docs: after starting the backend, visit `http://localhost:8000/docs`.

### Self-driving loop request example

```bash
curl -X POST localhost:8000/api/loop/iterate -H 'content-type: application/json' -d '{
  "domain": "anticorrosion_coating",
  "substrate": "carbon_steel",
  "salt_spray_hours": 800,
  "film_weight_gsm": 70,
  "cure_temperature_c": 80,
  "cleaning_efficiency": 0,
  "voc_limit_gpl": 250,
  "ph_target": null,
  "notes": "",
  "objectives": [],
  "optimize_iterations": 24,
  "n_suggest": 4
}'
# → {"task_id": "...", "poll_url": "/api/tasks/..."}
# Poll /api/tasks/{id}; the result includes optimization.top_formulations,
# next_doe (with ai_suggested flags) and rmse_by_metric.
```

### Intent-parse example

```bash
curl -X POST localhost:8000/api/intent/parse -H 'content-type: application/json' -d '{
  "text": "Develop a waterborne epoxy anti-corrosion coating for automotive underbody, salt spray ≥ 1000 h, cures at 120 °C"
}'
# → {"requirement": {"domain":"anticorrosion_coating", "salt_spray_hours":1000, ...},
#    "engine": "offline-heuristic", "extracted_fields": ["domain","salt_spray_hours",...]}
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
python3 -m venv .venv              # create once (required on Debian/Ubuntu with PEP 668)
source .venv/bin/activate          # Linux/macOS  (.venv\Scripts\activate on Windows)
pip install -e ".[dev]"
pytest -q                          # 193 tests, all offline
uvicorn app.main:app --reload      # http://localhost:8000/docs

# Frontend (separate shell)
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

For production or Docker deployments, use the locked runtime deps:

```bash
# Locked runtime deps (for Docker / production)
pip install -r requirements.txt
pip install -e . --no-deps
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
| `FORMUMIND_LLM_PROVIDER` | `anthropic` | active provider: `anthropic`/`openai`/`gemini`/`xai`/`groq`/`deepseek`/`qwen`/`moonshot`/`minimax` |
| `FORMUMIND_LLM_MODEL` | `claude-sonnet-4-6` | LLM model for the active provider |
| `FORMUMIND_<PROVIDER>_API_KEY` | empty | API key, e.g. `FORMUMIND_ANTHROPIC_API_KEY`, `FORMUMIND_DEEPSEEK_API_KEY`; falls back to offline synthesis when unset |
| `FORMUMIND_LLM_BASE_URL` | provider default | override the OpenAI-compatible base URL |
| `FORMUMIND_SEARCH_LIMIT_PER_SOURCE` | `5` | max results fetched per source type |
| `FORMUMIND_RAG_BACKEND` | `auto` | RAG store: `auto` (embedding if installed, else TF-IDF) / `embedding` / `tfidf` |
| `FORMUMIND_USE_CHEMCROW` | `true` | route chemistry questions to ChemCrow when installed |
| `FORMUMIND_ENRICH_COMPOUNDS` | `false` | backfill SMILES/molar-mass via PubChem on startup (needs `intel` + network) |
| `FORMUMIND_NOTEBOOKLM_ENABLED` | `false` | enable the NotebookLM retrieval source |
| `FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID` | empty | the fixed notebook id to query |
| `FORMUMIND_NOTEBOOKLM_STORAGE_PATH` | `./data/notebooklm_auth.json` | session file written by `notebooklm login` |
| `FORMUMIND_DB_URL` | `sqlite:///./data/formumind.db` | experiment database; can point at Postgres |
| `FORMUMIND_REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `FORMUMIND_CELERY_EAGER` | `true` | run tasks in-process without a broker |
| `FORMUMIND_OPTIMIZE_ITERATIONS` | `24` | optimization iterations |
| `FORMUMIND_TOP_N_FORMULAS` | `5` | leaderboard size |
| `FORMUMIND_MIN_TRAIN_SAMPLES` | `4` | min samples before training a metric's model |
| `FORMUMIND_AUTO_RETRAIN` | `true` | retrain automatically on new experiments |
| `FORMUMIND_PDF_DOWNLOAD` | `false` | Download patent PDFs for full-text extraction during deep research (requires network + USPTO/EPO access; false by default to keep tests offline) |
| `FORMUMIND_PDF_DOWNLOAD_MAX` | `3` | Max PDFs to download per KnowledgeCohort run |

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
pip install -e ".[llm]"          # Claude + OpenAI + Gemini SDKs (covers all 9 providers)
pip install -e ".[science]"      # scipy, scikit-learn, RDKit, ChemFormula, thermo
pip install -e ".[optimize]"     # optuna (CPU multi-objective optimizer, NSGA-II/TPE)
pip install -e ".[bo]"           # BoTorch + gpytorch + torch CPU (GP qNEHVI optimizer)
pip install -e ".[intel]"        # patent_client, paper-qa, chemcrow, pubchempy, arxiv, semanticscholar, duckduckgo-search
pip install -e ".[file_ingest]"  # markitdown, pypdf, python-docx (local file upload)
pip install -e ".[embedding]"    # sentence-transformers (+ chromadb) → semantic RAG
pip install -e ".[color]"        # colour-science (CIELAB / CIEDE2000)
pip install -e ".[notebooklm]"   # notebooklm-py[browser] (NotebookLM source; run `notebooklm login` once)
pip install -e ".[heavy]"        # torch, deepchem, transformers (MoLFormer), summit, ase
pip install -e ".[export]"       # openpyxl (XLSX export; CSV needs nothing)
```

After installing the `science` extra:
- property prediction adds **8 RDKit descriptors** (MolWt, MolLogP, TPSA,
  HBD/HBA, rotatable bonds, FractionCSP3, RingCount) and uses Fox/Mooney for
  Tg / viscosity;
- PVC / CPVC are computed from `density_gcm3` + `oil_absorption` in the
  knowledge base;
- model training upgrades from numpy ridge to scikit-learn random forest (with
  ensemble uncertainty);
- stoichiometry validation switches to ChemFormula for exact computation;
- `thermo` grounds the VOC g/L calculation in a real mixture density.

The optimizer auto-selects the strongest engine installed (**BoTorch** →
**Summit** → **Optuna** → numpy), grounded Q&A routes chemistry questions to
**ChemCrow** and others to **paper-qa**, both falling back to TF-IDF + the
configured LLM, and the RAG store upgrades from TF-IDF to semantic embeddings
when `sentence-transformers` is installed (see §5.9). The IP analyser and NL
intent parser share `complete_json()` against the configured LLM. None of these
are required — each lights up automatically.

### NotebookLM setup (one-time)

```bash
pip install -e ".[notebooklm]"
notebooklm login                   # opens a browser for Google sign-in (~170 MB Chromium)
# Then in .env:
FORMUMIND_NOTEBOOKLM_ENABLED=true
FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID=<your-notebook-id>
```

> NotebookLM has no official public API; `notebooklm-py` calls unofficial
> Google endpoints that may change. Treat the NotebookLM source as an R&D
> convenience, not a production crawl.

---

## 13. FAQ & scope notes

**Q: Does it work without an API key?**
Yes. Everything runs end-to-end fully offline; only LLM research synthesis is
replaced by the knowledge-base rule engine. The NL intent parser and IP
analyser also have deterministic offline fallbacks.

**Q: Are the predicted performance numbers trustworthy?**
The offline numbers are **engineering-reasonable screening estimates**, not
lab-validated specifications. Feed real DOE results back so data-driven models
progressively supersede the empirical prior — or run the **🔄 Self-Driving
Loop** to do it in one click per round.

**Q: Where is the 3D simulation?**
Each leaderboard card has a **3D molecular-viewer panel**, currently a
placeholder + data contract (it lists the SMILES-bearing components to be drawn
via 3Dmol.js). Full WebGL rendering — and reactive-MD trajectory rendering via
HTPolyNet/LAMMPS (Docker `heavy` profile) — ship in a later upgrade. The
optimization convergence chart is shown in the Optimization and Self-Driving
Loop modals.

**Q: Is patent retrieval a live online crawl?**
By default it uses an offline seed corpus per domain. Adapters for real
USPTO/EPO retrieval are in place but need the `intel` extra (`patent_client`,
etc.) and the corresponding key flow. The IP analyser uses the same retrieval.

**Q: Is NotebookLM safe to use in production?**
`notebooklm-py` uses undocumented Google endpoints that may change at any time.
The integration is therefore **off by default**, fully degradable to no-op, and
should be treated as an R&D aid rather than a production crawl.

**Q: Where is session history stored?**
Browser localStorage (up to 20 entries), never uploaded to the server.
Experiment data is persisted in the backend SQLite/Postgres database.

---

> This document corresponds to the current FormuMind branch:
> a NotebookLM-style three-pane redesign (Sources / Research / Actions) with a
> six-button Actions toolbar (🧪 Requirements, ⭐ Recommend, 🔬 DOE Design,
> 📈 Optimization, ⚙️ Process Optimization, 🔄 Self-Driving Loop), multi-LLM
> support across nine providers with an in-app Settings dialog, multi-source
> research (patents / literature / internet / **NotebookLM** / local files),
> RAG-grounded Q&A with semantic-embedding upgrade — plus auto-detected
> intelligence engines (BoTorch/Summit/Optuna optimization, active-learning DOE,
> ChemCrow/paper-qa Q&A, PubChem enrichment, thermo-grounded VOC, Fox/Mooney
> rheology, CIELAB/ΔE₀₀ color, PVC/CPVC, IP novelty analysis, ✨ NL intent
> parser) — on top of the multi-objective optimization, cost/sustainability,
> confidence intervals, DOE import/export, SQL persistence, formula export,
> convergence chart, model dashboard and session history shipped earlier.
