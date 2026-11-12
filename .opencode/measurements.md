# Measurements

This file records the measurements/quantities the evaluations need. It is a
placeholder — entries are added as the evaluations in `tests/` are defined.
Do not invent numbers; every reported value must trace to an artifact.

## Definitions

- **Data reality:** nurse shift summaries are OMOP rows in `cdm_synthea.note`
  with `note_source_value = 'Shift Summary'`. They are exported by
  `comparison/export_shift_summaries.py` to `comparison/data/*.txt` with
  `manifest.csv`. The exported count and per-note character lengths are read
  from that manifest at analysis time, never hardcoded.
- **Case unit:** one shift-summary note (one `.txt` → one run folder → one
  `timeline.json`).
- **Time unit:** hours, relative to time zero. Prompts are upstream discharge-
  summary prompts: time 0 = admission when stated, else case presentation.
  Shift summaries may state neither; record this caveat with any timeline
  metric.
- **Current method scope:** text-only multistep (`unimodal/`) only. No
  structured-EHR calibration, so "certain_EHR" is not applicable.

## Per-evaluation measurements

- **`shift-summaries-text-only`** (see `.opencode/tests/`)
  - Per note: event count in `timeline.json`; timeline span (max − min hours);
    count of central events; count of non-central events; presence of each of
    the five stage JSON files; whether any stage had to retry (visible in run
    log, not stored in JSON).
  - Per run: number of notes attempted, completed (all five JSONs
    non-empty), skipped by resume, and failed; wall-clock time if recorded by
    the operator.
  - Export quality: notes exported, notes skipped, empty/NULL `note_text`
    count, char-length distribution.

## Comparison measurements (this fork vs. the owner's project)

To be defined when the owner's reference timelines/chains are available.
Candidate quantities (as named by `compare_tts/README.md`; confirm each before
use):

- Event-match rate under embedding-based matching (threshold to be fixed via
  `threshold_sweep.ipynb`).
- Concordance between predicted and reference event order.
- Log-time discrepancy and AULTC between predicted and reference event times.
- Event counts / granularity per note (to check the two projects extract
  comparable units).

Reference timelines (manual `.bsv`) do not exist yet for the shift summaries;
state that the quantitative comparison is pending until they do.

## Output artifacts

- `comparison/data/manifest.csv` — export inventory (note_id, person_id,
  visit_occurrence_id, visit_start_datetime, visit_end_datetime, note_date,
  note_datetime, note_title, filename, char_length, has_html).
- `comparison/outputs/<run>/note_<id>/*.json` — per-case pipeline stages and
  `timeline.json`.
- `compare_tts` matched-event CSVs and diagnostic plots (once references
  exist).