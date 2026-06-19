# FormuMind Quick Start (5 minutes)

Run a complete formulation R&D loop in 5 minutes, with real UI screenshots.
For the full reference see [USER_GUIDE.md](./USER_GUIDE.md) (中文: [快速入门.md](./快速入门.md)).

---

## Prerequisite: start the platform

```bash
# Backend (terminal 1)
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload      # http://localhost:8000/docs

# Frontend (terminal 2)
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

Open **http://localhost:5173**. No API key required — the platform runs fully
offline.

---

## Step 0 · Overview

FormuMind uses a NotebookLM-style three-pane layout that separates
**inputs → research → outputs**: **Sources** on the left, **Research** Q&A in
the center, and the **Actions** toolbar on the right. The header holds
**⚙ Settings** (LLM provider) and **🕐 History**.

![Overview](./images/01-overview.png)

- **Left (Sources)**: research-topic prompt box, source-type checkboxes
  (patents / literature / internet / local files / **📓 NotebookLM**), file
  upload, a **Search** button, and the loaded-sources list.
- **Center (Research)**: chat that answers questions grounded in the loaded
  sources, with citations.
- **Right (Actions)**: six buttons — 🧪 Requirements, ⭐ Recommend,
  🔬 DOE Design, 📈 Optimization, ⚙️ Process Optimization, 🔄 Self-Driving Loop
  — each opening a focused modal.

---

## Step 1 · Load sources

Type a research topic (e.g. "low-VOC waterborne anti-corrosion coating"), tick
the source types to search (patents / literature / internet / **📓 NotebookLM**),
and click **Search**. You can also upload local files (PDF/DOCX/XLSX/PPTX/HTML/
images), parsed via markitdown.

![Search & sources](./images/02-search.png)

Results from every selected source are merged, de-duplicated, ranked by
relevance, and listed in the left column (each removable). Offline, patent
search returns the curated seed corpus; literature/internet search need the
optional `intel` libraries; NotebookLM needs the `notebooklm` extra and a
one-time browser login (see §12 of the full guide).

---

## Step 2 · Ask the sources (grounded Q&A)

In the center column, ask a question about the loaded sources — e.g. "What is
the main corrosion-protection mechanism in these patents?". The answer is
**grounded in the evidence** (semantic embedding or TF-IDF re-rank → LLM) and
shows citation chips linking back to the sources used.

![Research Q&A](./images/03-research.png)

---

## Step 3 · Choose your LLM (Settings)

Click **⚙ Settings** in the header. FormuMind supports **nine providers** —
Claude, OpenAI, Gemini, Grok, Meta (via Groq), DeepSeek, Qwen, Kimi, MiniMax.
Pick a provider and model, paste an API key, optionally set a custom base URL,
then **Save & test connection**. With no key, everything still runs via the
offline rule engine.

![Settings · multi-LLM](./images/04-settings.png)

---

## Step 4 · Describe the project in one sentence (✨ NL Intent)

Open **🧪 Requirements** and use the **✨ NL Intent** box at the top: type a
plain-language brief like *"Develop a waterborne epoxy anti-corrosion coating
for automotive underbody, salt spray ≥ 1000 h, cures at 120 °C"*. Click
**Parse & Fill** — domain, substrate, salt-spray hours, VOC limit and cure
temperature are extracted and auto-filled (LLM when configured, otherwise the
deterministic regex fallback).

---

## Step 5 · Recommend formulations + IP analysis

Open **⭐ Recommend** (right column) and click **research patents & recommend
formulations**. The Top-N leaderboard appears — each card shows the ingredient
table and predicted metrics, including the auto-computed `cost_cny_per_kg`,
`voc_gpl`, `sustainability_idx`, **PVC / CPVC** (pigment volume concentration
vs. critical), **Tg (°C)** and **viscosity_relative** (Fox & Mooney models),
plus **lab_L/a/b + ΔE₀₀** when the `color` extra is present.

Click **🔍 IP 合规分析** on any formula card to retrieve relevant patents,
score the formula's novelty (0–1) and surface infringement-risk highlights
and white-space hints. Expand a card and a **3D molecular-viewer panel** lists
the SMILES-bearing components to be rendered via 3Dmol.js.

![Recommend · leaderboard with molecular viewer](./images/05-recommend.png)

---

## Step 6 · Generate a DOE and feed results back

Open **🔬 DOE Design**, choose a design (e.g. **central composite CCD** or
**🧠 AI active selection**) and click **Generate DOE**. You get a run table —
one row per experiment, natural factor values plus a blank "measured" column.
The 🧠 active-learning rows are highlighted in violet: they are the points
expected-improvement says will teach the surrogate the most.

![DOE design](./images/06-doe.png)

Two feedback paths:

1. **Manual**: type lab-measured values into the "measured" column, then click
   **③ feed back results and train model**.
2. **Batch**: click **Export CSV**, hand it to the lab, then **Import CSV** once
   it's filled in.

Once a metric reaches ≥ 4 samples, a data-driven model is trained automatically;
the model-quality dashboard shows an R² half-gauge + RMSE, and subsequent
recommendations/optimization switch to the "empirical + measured" blend.

---

## Step 7 · Run the optimization loop

Open **📈 Optimization** and click **run optimization loop** to start Bayesian
multi-objective optimization (24 iterations by default). The **convergence
chart** plots the best-so-far objective score per iteration; hover for exact
values. The leaderboard updates to the optimized Top-5 formulations, balancing
salt-spray, cost and sustainability simultaneously.

![Optimization · convergence](./images/07-optimize.png)

---

## Step 8 · Process optimization & self-driving loop

- **⚙️ Process Optimization** — co-optimizes manufacturing parameters
  (cure temperature/time, dispersion RPM, film thickness, bath temperature, pH,
  …) with Arrhenius/empirical outcome models. Same Bayesian engine as
  formulation optimization, but over the *process* design space.
- **🔄 Self-Driving Loop** — one click runs **measured records → retrain →
  Bayesian optimize → next active-learning DOE batch** end-to-end. The modal
  shows model R²/RMSE cards (with a ↓ trend arrow), the convergence chart, and
  the next DOE batch (AI rows highlighted in violet). Export the next batch as
  CSV and the loop continues.

> Every successful research / recommend / optimize / feedback / loop run is
> saved as a session snapshot — open **🕐 History** in the header to review and
> restore the last 20 sessions (stored in browser localStorage).

---

## Next steps

- Custom objective weights, batch feedback, multi-LLM configuration, IP
  reports, NotebookLM bridging, real-engine wiring? See the **[full User Guide](./USER_GUIDE.md)**.
- Want stronger engines? They auto-detect on install — `pip install -e ".[optimize]"`
  for the Optuna optimizer, `".[bo]"` for the BoTorch Gaussian-process optimizer,
  `".[intel]"` for ChemCrow/paper-qa Q&A and PubChem enrichment, `".[science]"`
  for thermo-grounded VOC + PVC/Tg, `".[embedding]"` for semantic RAG,
  `".[color]"` for CIELAB / ΔE₀₀, `".[notebooklm]"` for the NotebookLM source.
  Nothing is required; each lights up automatically.
- Interactive API docs: start the backend and visit
  **http://localhost:8000/docs**.

> The offline performance numbers are engineering-reasonable screening
> estimates, not lab-validated specs. Feed real DOE data back and predictions
> get progressively more accurate.
