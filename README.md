# Pathfinders-Multi-Agent-AI-Delivery-System
Search &amp; Problem Solving,Knowledge Representation &amp; Reasoning, Agents &amp; Game Theory, Applications of Deep Learning


## Evaluation (R8)

One command reruns the evaluation: the main sweep (180 runs), the three-mode sweep (18 runs, six of them slow centralized runs),
the tables, the figures F6, F7 and F9, the pass/fail verdicts and `reports/r8/RESULTS.md`:

```bash
python -m src.eval.run_all --workers 2
```

It is resumable (finished runs are skipped). `--skip-run` rebuilds the tables, figures and verdicts from existing logs. Put
`data/raw/metr-la.h5` in place first if you want the traffic model; `reports/r8/MANIFEST.json` records whether it was used.
Protocol and pre-registered criteria: `docs/EXPERIMENT_PROTOCOL.md`. Report sections: `docs/reports/r8_report_sections.md`.
