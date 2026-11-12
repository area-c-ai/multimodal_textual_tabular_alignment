# opencode notes

Session-level learnings. Keep entries extremely short; favour data
architecture and data-type facts over conclusions.

## Purpose / environment
- This fork is a **comparison baseline**: run the TTA timeline method on the
  owner's nurse shift summaries; results are compared with the owner's own
  project. Stay close to upstream code; record deviations.
- Only `unimodal/` (text-only) is runnable today. Its imports need just
  `langchain-community`, `langchain-core`, `requests` (GGUF path adds
  `llama-cpp-python`); no torch/CUDA for text-only.
- `environment.yml` is a study-runtime conda export with cluster paths
  (`/data/kumars33/...`); do not create the env blindly.
- Runners load prompts from `templates/` **relative to CWD**
  (`self.template_dir = "templates"`). `unimodal/templates/` exists;
  `prompt_templates/` is canonical (adds the single-step and update
  templates).

## Data / DB
- Shift summaries: OMOP `cdm_synthea.note` where
  `note_source_value = 'Shift Summary'`; export one `.txt` per note + a
  manifest before running. `data.md` is owner-local/gitignored and absent in
  this fork.
- Known local DB: Postgres `localhost:54320`, user `postgres`, schema
  `cdm_synthea`. psycopg2 returns DATE columns as `date` — wrap with
  `pd.to_datetime`; use `psycopg2-binary`.
- `note_datetime` is entry time, not necessarily event time.

## Pipeline facts
- `mimic_struct_utils.py` is **not valid Python as forked**:
  `m3_dst_hadm_ids = [....]` and `m4_pt_to_hadm = {define own mapping}` are
  masked placeholders (SyntaxError), and it reads hardcoded
  `/data/CHARM-MIMIC/...` CSVs at import. Every `langgraph_scripts/*.py`
  runner imports it at module level → all fail at import.
- v3 `reconstruct_timeline` consumes `updated_central_timeline`, and every
  `--pipeline_variant` includes an update step → **no text-only path in v3**,
  despite the root README's claim. Text-only = `unimodal/`.
- MedTE retrieval model `mohammadkhodadad/MedTE-cl15-step-8000` calls
  `.cuda()`; multimodal runs need a GPU + the 5-column structured CSVs +
  admissions tables + a `hadm_id` convention.
- BSV parsers silently drop malformed rows; pass `--omit_tags think` for
  reasoning models. A run can exit 0 with empty JSON — check the files.
- Directory mode shuffles files and infers an ID from the last numeric
  substring of the filename (multimodal only; unimodal ignores IDs).
- Resume: each runner skips cases/steps whose output JSONs already exist and
  are non-empty.

## Preflight (2026-11-12)
- Venv `.venv` (Python 3.14) with `langchain-core` 1.8.0,
  `langchain-community` 0.4.2, `psycopg2-binary`; unimodal runner imports and
  `--help` work. Root `.gitignore` covers `.venv/`, `data/`, `outputs/`.
- DB: use **database `OHDSI-CDM_V54`**, schema `cdm_synthea` on
  `127.0.0.1:54320` (`postgres` DB has only a `public` schema). `note` has 928
  rows; 347 are `note_source_value = 'Shift Summary'` across 24 patients
  (2025-05-20 → 2026-05-30). All have text; 2 contain HTML; `note_date` is
  null — use `note_datetime`; `note_title` and type/class ids are null.
- Export: `comparison/export_shift_summaries.py` → `comparison/data/` (347
  `.txt` + `manifest.csv` incl. `visit_start_datetime`/`visit_end_datetime`).
  Run with `PGPASSWORD` in env; `--limit` for smoke runs, `--strip-html` to
  clean tags. `visit_occurrence` holds duplicate identical rows per visit —
  the exporter collapses per `visit_occurrence_id` or the join doubles rows.
- Model backend: **vLLM only** — `http://100.73.85.24:1235/v1`, model
  `qwen3.8-27b`, key env `VLLM_API_KEY`, thinking off per request via
  `chat_template_kwargs.enable_thinking=false`. On 2026-11-12 both servers
  (LM Studio `:1234`, vLLM `:1235`) returned empty replies; the owner reports
  vLLM fixed since — no request has been sent from this repo yet. No local
  GGUF files.
- Runner↔vLLM gap: the unimodal runner POSTs `{endpoint}/v1/chat/completions`
  with **no `Authorization` and no `model`** field (fixed
  `max_tokens=16000`/`temperature=0.6`), and a directory run with
  `--log_folder` reuses one folder for all files (2nd note skipped as done).
  `comparison/` fixes both without touching upstream: `vllm_proxy.py`
  (127.0.0.1:18001, injects auth/model/thinking-off) + `run_text_only.sh`
  (one runner call per note, per-note `--log_folder`, CWD=`unimodal` so
  `templates/` resolves).
- Agent Service routes: `/agents`, `/agents/{name}`, `/agents/{name}/run`
  (body needs `user_prompt`), `/health`. It is not chat-completions.

## Comparison
- `compare_tts/` needs manual `.bsv` references + `pritamdeka/S-PubMedBert-MS-MARCO`
  + R; not runnable until reference timelines exist.
- Fix the event-match threshold with `threshold_sweep.ipynb` before comparing
  metrics; record definitions in `.opencode/measurements.md`.