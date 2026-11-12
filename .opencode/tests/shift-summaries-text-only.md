# Shift summaries → text-only timelines

**Status:** planned.

## Goal

Reconstruct an event timeline from each nurse shift summary with the TTA
text-only multistep pipeline, producing the artifact that will be compared with
the owner's own project.

## Data

- Source: OMOP `cdm_synthea.note` where `note_source_value = 'Shift Summary'`.
- Export (planned script, owner: `db`): one `.txt` per note under
  `data/shift_summaries/`, named `note_<note_id>.txt`, plus `manifest.csv`
  (`note_id`, `person_id`, `visit_occurrence_id`, `note_date`,
  `note_datetime`, `filename`, `char_length`).
- No structured EHR rows and no manual reference timelines are required for
  this run.

## Method

1. Export the shift summaries from the OMOP `note` table (limit first for a
   smoke run).
2. Run the text-only multistep pipeline from `unimodal/`:
   ```
   python langgraph_runner_unimodal.py \
     --summary_directory ../data/shift_summaries \
     --llm_endpoint <OpenAI-compatible URL> --model_basename <model> \
     --log_folder ../outputs/<run> --omit_tags think --n_retries 3
   ```
3. Verify per case that `central_events.json`, `time_distances.json`,
   `central_timeline.json`, `non_central_events.json` and `timeline.json` are
   present and non-empty.

Stages: central-event extraction → pairwise time distances → central timeline
→ non-central events → full timeline. Prompts are upstream (time zero =
admission if stated, else case presentation) and are not modified for this
run.

## LLM involved

The owner's OpenAI-compatible model endpoint (base URL + model name to be
recorded in the run notes). The endpoint must serve
`POST {url}/v1/chat/completions`.

## Measurements

Recorded in `../measurements.md`:

- per note: event count, timeline span, central/non-central event counts,
  stage-file completeness;
- per run: attempted / completed / resumed / failed counts;
- export quality: notes exported/skipped, empty-text count, char lengths.

## Expected outputs

- `data/shift_summaries/` (notes + `manifest.csv`).
- `outputs/<run>/<case>/` with the five JSON files; `timeline.json` as
  `[event, hours]` pairs.
- A short run summary listing N notes and any failures.

## Out of scope

Multimodal/structured calibration (no structured source yet), `compare_tts`
reference-based metrics (no manual `.bsv` references yet), and `gap_detection`
(needs structured rows).

## Open items

- Confirm the chat-completions base URL and model name.
- Decide the export location and whether `data/` and `outputs/` are
  gitignored.
- Define the comparison plan with the owner's project once these timelines
  exist.