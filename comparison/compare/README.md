# compare/ — TTA vs. owner-reference comparison

Notebook-driven comparison of a TTA text-only run against the owner's curated
event timestamps. The notebook is the place to inspect anchors, review
candidate matches, apply manual overrides, and read/write the results.

## Run it

```bash
# from anywhere, in an environment with pandas + matplotlib + jupyterlab
jupyter lab comparison/compare/compare_tta.ipynb
```

Prerequisites: `comparison/data/` exported and a TTA run finished under
`comparison/outputs/` (`bash comparison/run_text_only.sh --all`).

All thresholds, paths, and the concept → reference-event mapping are editable
in the **Parameters** cell (overridable via `TTA_REFERENCE_CSV` /
`TTA_NODES_JSONL` env vars).

## How it works

1. **Reference truth** — `table6_weaning.csv` (long: `patient_id, source,
   event, time`) for now; `weaning_structured.csv` (wide) is supported via
   `REFERENCE_KIND = "wide"`. Restricted to the mapped events and
   `source = "structured"`.
2. **TTA run** — newest directory under `comparison/outputs/` (or set
   `RUN_NAME`); per-note `timeline.json` loaded as `[event, hours]`.
3. **Anchoring** — per note, TTA events are matched to timed lines (owner's
   clock-resolved `event_nodes` first, the note's own clock lines otherwise);
   the median of `line_time − hours` is the note origin,
   `abs_time = origin + hours`. Residual spread (`mad_min`) flags unstable
   notes.
4. **Target matching** — keyword concepts (admission / separation attempt /
   weaning / extubation plan; editable `CONCEPTS` in `targets.py`), candidate
   review table, optional `OVERRIDES`, earliest absolute time per
   (patient, event).
5. **Metrics** — `diff_min = TTA − reference`; median signed/absolute
   difference, p90, tolerance bands — same definitions as the owner's
   `weaning_event/time_difference_findings.md`.
6. **Second reference (section 10)** — the owner's LLM-evaluation answers
   (`all_answers_33.csv`, wide, ISO timestamps, `"Non present"` sentinel)
   compared on `first_imv_end` / `first_weaning` / `extubation_plan` via
   `separation_attempt` / `weaning` / `extubation_plan`. The write step
   combines both references into the final `tta_vs_reference.csv` /
   `..._summary.csv`, one block per `source` (`structured`, `chained`).

## Modules (pure pandas + stdlib)

| Module | Role |
|---|---|
| `common.py` | text normalization, token similarity, clock parsing, nearest-day resolution |
| `loaders.py` | reference tables, note manifest, TTA `timeline.json`, `nodes.jsonl` |
| `anchors.py` | per-note origin estimation (`estimate_all_anchors`, `apply_anchors`) |
| `targets.py` | keyword concepts (`CONCEPTS`), candidate table, target resolution |
| `metrics.py` | comparison table, summary, anchor summary, `all_event_decisions` export |

## Outputs (`comparison/out/`, when `WRITE_OUTPUTS = True`)

| File | Content |
|---|---|
| `tta_vs_reference.csv` | per (patient, event) and `source`: ref/tta time, `diff_min`. Final version combines both references: `structured` (table6) + `chained` (`all_answers_33.csv`); section 9 writes the structured-only version first |
| `tta_vs_reference_summary.csv` | per (source, event) counts, medians, tolerance % |
| `tta_targets.csv` | resolved TTA targets (incl. `MISSING` rows) |
| `tta_anchor_estimates.csv` | per-note origin, MAD, source, stability |
| `all_event_decisions_tta.csv` | owner's `all_event_decisions` schema (`run_id` = TTA run name) |
| `tta_vs_extubation_answers.csv` | same, vs `all_answers_33.csv` |
| `tta_vs_extubation_answers_summary.csv` | per-event summary vs `all_answers_33.csv` |
| `all_event_decisions_tta_extubation.csv` | `all_event_decisions` schema for the extubation-answers comparison |

## Caveats

- `table6_weaning.csv` has no `visit_start`, so admission is unmatched
  against it (use the wide `weaning_structured.csv` for that event).
- Generic wording in TTA events (e.g. `... commenced`) can score above the
  match threshold; review the candidate table and use `OVERRIDES`.
- TTA's time zero follows the upstream prompts (admission if stated, else
  case presentation); the anchor step estimates it per note and reports the
  residual, so check `tta_anchor_estimates.csv` before trusting differences.
- `all_answers_33.csv` is parsed with `format="ISO8601"` (never `dayfirst`,
  which swaps day/month in ISO strings); `"Non present"` becomes NaT.
- `separation_attempt ↔ first_imv_end` is the closest TTA concept, not an
  identity (low support ≠ end of IMV); "wean" can also mean drug weaning
  (e.g. "Norad weaned off") — resolve via the review table / `OVERRIDES`.
- `extubation_plan` penalises past-tense and "not for extubation" phrasings
  (per-concept `negative_penalty = 0.9` in `targets.CONCEPTS`).
