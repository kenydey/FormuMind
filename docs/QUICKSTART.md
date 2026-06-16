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

A dark, industrial three-column layout: requirements on the left, research & DOE
in the center, convergence chart & leaderboard on the right.

![Overview](./images/01-overview.png)

- **Left**: pick a product domain / substrate, set target metrics with sliders
  (salt-spray, film weight, cure temperature, VOC limit).
- **Center**: AI research stream (top) and DOE feedback (bottom).
- **Right**: convergence chart (placeholder until you optimize) and the Top-N
  leaderboard.

---

## Step 1 · Research patents & recommend formulations

After setting the requirement, click **① research patents & recommend
formulations** (bottom-left).

![Research](./images/02-research.png)

The platform:
- retrieves patent/literature evidence (center "Research Stream", click ▼ to
  expand snippets, sorted by relevance);
- shows a reaction-mechanism summary (highlighted blue paragraph);
- produces 3 recommended formulations in the right-hand leaderboard — each card
  shows the ingredient table and predicted metrics, including the
  auto-computed `cost_cny_per_kg`, `voc_gpl`, and `sustainability_idx`.

---

## Step 2 · Run the DOE optimization loop

Click **② run DOE optimization loop** to start Bayesian multi-objective
optimization (24 iterations by default).

![Optimize](./images/03-optimize.png)

- A **convergence line chart** appears top-right: X = iteration, Y = best
  objective score, hover for exact values.
- The leaderboard updates to the **Top-5 optimized formulations** (cards named
  `Optimized …` with a score).
- Optimization balances salt-spray, cost and sustainability simultaneously
  (weighted multi-objective aggregation; default weights in the user guide).

---

## Step 3 · Generate a DOE and feed measured results back

In the "DOE Feedback" area, choose a design (e.g. **central composite CCD**) and
click **Generate DOE**.

![DOE table](./images/04-doe.png)

You get a run table — one row per experiment, natural factor values plus a blank
"measured" column. Two feedback paths:

1. **Manual**: type lab-measured values into the "measured" column, then click
   **③ feed back results and train model**.
2. **Batch**: click **Export CSV**, hand it to the lab, then **Import CSV** once
   it's filled in.

Once a metric reaches ≥ 4 samples, a data-driven model is trained automatically;
the model-quality dashboard shows an R² half-gauge + RMSE, and subsequent
recommendations/optimization switch to the "empirical + measured" blend.

---

## Step 4 · Review and restore session history

After every successful research / optimize / feedback run, a session snapshot is
saved automatically. Click the **🕐 History** button in the header.

![History](./images/05-history.png)

- The right drawer lists the last 20 sessions with domain, time, Top-1 formula
  name and score.
- Click any entry to **restore** that session's requirement and leaderboard.
- History lives in browser localStorage and survives a page refresh.

---

## Next steps

- Custom objective weights, batch feedback, wiring up the real engines? See the
  **[full User Guide](./USER_GUIDE.md)**.
- Interactive API docs: start the backend and visit
  **http://localhost:8000/docs**.

> The offline performance numbers are engineering-reasonable screening
> estimates, not lab-validated specs. Feed real DOE data back and predictions
> get progressively more accurate.
