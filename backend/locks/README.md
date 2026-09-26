# Deploy dependency freezes (P1 #23)

`requirements.txt` remains the **hand-maintained** pin set used by Docker/CI.

After a successful install or image build, archive the resolved environment:

```bash
bash scripts/freeze_deploy_pins.sh
```

This writes timestamped files here (`requirements-freeze-*.txt`) plus
`requirements-freeze-latest.txt`. Commit freezes when preparing a release
image; do **not** regenerate `requirements.txt` via `pip freeze`.
