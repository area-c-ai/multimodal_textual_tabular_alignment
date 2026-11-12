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

## Comparison
- `compare_tts/` needs manual `.bsv` references + `pritamdeka/S-PubMedBert-MS-MARCO`
  + R; not runnable until reference timelines exist.
- Fix the event-match threshold with `threshold_sweep.ipynb` before comparing
  metrics; record definitions in `.opencode/measurements.md`.