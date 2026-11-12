# Shift summaries → text-only timelines

**Status:** ready — data exported to `comparison/data/` (347 notes) and the
vLLM backend is wired via the local auth proxy; **no pipeline run has been
started yet** (owner decision: use vLLM `qwen3.8-27b` only, no other model).

## Preflight result (2026-11-12)

- Python venv `.venv` (3.14) created with `langchain-core` 1.8.0,
  `langchain-community` 0.4.2, `psycopg2-binary`; the unimodal runner's
  imports and `--help` work.
- DB reachable at `127.0.0.1:54320`; use database `OHDSI-CDM_V54`, schema
  `cdm_synthea` (the `postgres` database has no CDM schema).
- Export done with `comparison/export_shift_summaries.py`: **347**
  `Shift Summary` notes across **24** patients, 0 empty, 2 with HTML;
  `note_date` is null, use `note_datetime`; the manifest now carries
  `visit_start_datetime`/`visit_end_datetime`. Files and `manifest.csv` are
  in `comparison/data/` (gitignored).
- vLLM backend `http://100.73.85.24:1235/v1` was unreachable on
  2026-11-12 (empty replies) and is reportedly fixed since. The unimodal
  runner has no auth/model field, so all calls go through
  `comparison/vllm_proxy.py`. **No model call has been made from this repo
  yet.**

## Goal

Reconstruct an event timeline from each nurse shift summary with the TTA
text-only multistep pipeline, producing the artifact that will be compared with
the owner's own project.

## Data

- Source: OMOP `cdm_synthea.note` where `note_source_value = 'Shift Summary'`.
- Export (`comparison/export_shift_summaries.py`): one `.txt` per note under
  `comparison/data/`, named `note_<note_id>.txt`, plus `manifest.csv`
  (`note_id`, `person_id`, `visit_occurrence_id`, `visit_start_datetime`,
  `visit_end_datetime`, `note_date`, `note_datetime`, `note_title`,
  `filename`, `char_length`, `has_html`). Done.
- No structured EHR rows and no manual reference timelines are required for
  this run.

## Method

1. (Done) Export the shift summaries from the OMOP `note` table.
2. Run the text-only multistep pipeline via the wrapper:
    ```
    bash comparison/run_text_only.sh --check             # offline readiness
    bash comparison/run_text_only.sh --note note_12406   # smoke case
    bash comparison/run_text_only.sh --all --run-name baseline_v1
    ```
   The wrapper starts `comparison/vllm_proxy.py` (127.0.0.1:18001 →
   `http://100.73.85.24:1235`; injects `Authorization`, `model`, thinking-off)
   and then runs `unimodal/langgraph_runner_unimodal.py` **once per note**
   (`--llm_endpoint http://127.0.0.1:18001 --model_basename qwen3.8-27b
   --log_folder comparison/outputs/<run>/<note> --omit_tags think
   --n_retries 3`). Per-note invocation is deliberate: with
   `--summary_directory` + `--log_folder` the upstream runner reuses one
   folder for every file, so only the first note would be processed.
3. Verify per case that `central_events.json`, `time_distances.json`,
   `central_timeline.json`, `non_central_events.json` and `timeline.json` are
   present and non-empty.

Stages: central-event extraction → pairwise time distances → central timeline
→ non-central events → full timeline. Prompts are upstream (time zero =
admission if stated, else case presentation) and are not modified for this
run.

## LLM involved

vLLM server `http://100.73.85.24:1235/v1`, model `qwen3.8-27b` — same
configuration as the `agent_*_vllm` agents in the owner's other repo (key env
`VLLM_API_KEY`, `chat_template_kwargs.enable_thinking=false`). **Only vLLM is
used** — no Mistral, no LM Studio, no other provider. Reached exclusively via
`comparison/vllm_proxy.py`.

## Measurements

Recorded in `../measurements.md`:

- per note: event count, timeline span, central/non-central event counts,
  stage-file completeness;
- per run: attempted / completed / resumed / failed counts;
- export quality: notes exported/skipped, empty-text count, char lengths.

## Expected outputs

- `comparison/data/` (notes + `manifest.csv`) — done.
- `comparison/outputs/<run>/note_<id>/` with the five JSON files;
  `timeline.json` as `[event, hours]` pairs.
- A short run summary listing N notes and any failures.

## Out of scope

Multimodal/structured calibration (no structured source yet), `compare_tts`
reference-based metrics (no manual `.bsv` references yet), and `gap_detection`
(needs structured rows).

## Open items

- Run the smoke case (`--note note_12406`) and confirm the five non-empty
  JSONs before the full `--all` run.
- Define the comparison plan with the owner's project once these timelines
  exist.
