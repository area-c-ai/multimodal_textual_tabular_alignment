# comparison/ — TTA text-only baseline on shift summaries

Self-contained workspace for running the TTA **text-only** timeline pipeline
(`unimodal/`, upstream code unchanged) on the owner's nurse shift summaries,
using the owner's **vLLM server** (`qwen3.8-27b`). These timelines are the
comparison baseline against the owner's own project.

## Layout

| Path | Role |
|---|---|
| `export_shift_summaries.py` | Exports `note` rows with `note_source_value = 'Shift Summary'` to `data/note_<note_id>.txt` + `manifest.csv` (incl. visit window for the admission anchor). |
| `vllm_proxy.py` | Local reverse proxy on `127.0.0.1:18001` → vLLM; injects `Authorization`, `model`, and thinking-off. |
| `run_text_only.sh` | One-command wrapper: proxy + per-note unimodal runs with resume. |
| `data/` | Exported notes + `manifest.csv` (gitignored). |
| `outputs/` | Run results: `outputs/<run>/<note_id>/*.json` (gitignored). |
| `compare/` | Comparison notebook + modules (`compare_tta.ipynb`). |
| `out/` | Comparison results written by the notebook (gitignored). |
| `requirements.txt` | Python deps for export + comparison notebook. |
| `.env.example` | Non-secret template for `.env` (gitignored). |

## Setup

```bash
cp comparison/.env.example comparison/.env   # fill PGPASSWORD, VLLM_API_KEY
```

- `VLLM_UPSTREAM` — vLLM base URL **without** trailing `/v1`
  (default `http://100.73.85.24:1235`).
- `VLLM_MODEL` — must match the server's `--served-model-name`
  (default `qwen3.8-27b`).
- `VLLM_API_KEY` — bearer token; the owner's other repo keeps it in
  `agents/.env`. If unset, `run_text_only.sh` falls back to that file
  (read-only) — but `comparison/.env` is preferred.

## Export

```bash
PGPASSWORD=<pw> .venv/bin/python comparison/export_shift_summaries.py
# --limit 5 for a smoke run; --strip-html to clean tags; --out-dir to move output
```

Manifest columns: `note_id, person_id, visit_occurrence_id,
visit_start_datetime, visit_end_datetime, note_date, note_datetime,
note_title, filename, char_length, has_html`. Note: `cdm_synthea.visit_occurrence`
holds duplicate identical rows per visit — the exporter collapses them.

## Run

```bash
bash comparison/run_text_only.sh --check          # offline readiness, no network
bash comparison/run_text_only.sh --dry-run --all  # print the commands only
bash comparison/run_text_only.sh --note note_12406            # one case
bash comparison/run_text_only.sh --all --run-name baseline_v1 # all 347
```

The wrapper starts the proxy, waits for the **local** port only (never calls
the model itself), then runs the unimodal runner **once per note** with its
own `--log_folder` (the upstream runner reuses one folder for a whole
directory, so a second note would be skipped as "already exists"). Re-running
is safe: finished notes are skipped via the runner's own output check.

Each note produces `central_events.json`, `time_distances.json`,
`central_timeline.json`, `non_central_events.json`, `timeline.json`
(`[event, hours]` pairs) under `outputs/<run>/note_<id>/`.

## Comparison notebook

`compare/compare_tta.ipynb` turns a finished run into an absolute-time
comparison against the owner's curated timestamps (default:
`table6_weaning.csv`, `source = "structured"`, events
`visit_start` / `first_low_support_time` / `first_weaning`):

1. anchor each note's hour axis (owner's clock-resolved `event_nodes` first,
   the note's own clock lines otherwise),
2. match TTA events to the target concepts, with a candidate-review table
   and manual `OVERRIDES`,
3. compute `diff = TTA − reference` in minutes with the same definitions as
   the owner's `weaning_event/time_difference_findings.md`.

```bash
pip install -r comparison/requirements.txt   # if not using the TTA conda env
jupyter lab comparison/compare/compare_tta.ipynb
```

A second reference (section 10 of the notebook) compares the same run
against the owner's LLM-evaluation answers
(`Evluating_llms/results/investigate_extubation_output/all_answers_36.csv`)
on `first_imv_end` / `first_weaning` / `extubation_plan`. The final
`tta_vs_reference.csv` / `..._summary.csv` combine both references,
labelled by the `source` column (`structured` / `chained`).

All thresholds/paths are editable in the notebook's **Parameters** cell.
Results (incl. `all_event_decisions_tta.csv` in the owner's
`all_event_decisions` schema) go to `comparison/out/`. See
`compare/README.md` for details and caveats (e.g. `table6_weaning.csv`
has no `visit_start`).

## Deviations from upstream (recorded)

- **`comparison/vllm_proxy.py`** (new file): the upstream runner posts
  `/v1/chat/completions` with no `Authorization` header and no `model` field,
  which a vLLM server started with `--api-key` rejects. The proxy injects the
  key, `model=qwen3.8-27b`, and
  `chat_template_kwargs.enable_thinking=false` (same as the owner's other
  repo). `unimodal/` is not modified.
- **Per-note invocation** (in `run_text_only.sh`): works around the upstream
  shared-`--log_folder` batch behaviour. No algorithm or prompt changes.
- `unimodal/langgraph_runner_unimodal.py` is run with `--omit_tags think`
  and its fixed `max_tokens=16000` / `temperature=0.6`.
- Time zero follows the upstream prompts (admission if stated, else case
  presentation); shift summaries are not discharge summaries — record this
  caveat in any comparison.
