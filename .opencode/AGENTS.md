# AGENTS.md — Project Orientation for AI Agents

Read this first. It explains what this project is, how it relates to the
owner's other project, and how the repository fits together before touching
anything.

## 1. Purpose

This repository is a **fork of the code release for *Text Knows What, Tables
Know When: Clinical Timeline Reconstruction via Retrieval-Augmented
Multimodal Alignment*** (TTA). It reconstructs absolute clinical timelines
from narrative notes: it extracts clinical events, reasons about their
relative timing, assembles a full timeline, and (in the full method)
calibrates event times using retrieved structured EHR rows.

**What this fork is used for:** running the TTA method on the owner's
**nurse shift summaries** to produce event timelines that serve as a
**comparison baseline** for the owner's own project.

## 2. Relationship to the owner's other project

The `.opencode/` agent setup was originally written for a different repository
(the owner's own project) that:

- stores clinical data in an OMOP CDM v5.4 database (`cdm_synthea`),
- extracts nurse-action **event chains** from notes (`event_nodes/`),
- runs LLM **contestants vs judges** over those chains (`Evluating_llms/`),
- tracks runs in MLflow and analyses device risk.

**None of those components exist in this fork.** They are referenced only as
the source of the comparison. The intention of this project, relative to that
one, is:

> Reconstruct timelines from nurse shift summaries with the TTA method, then
> compare those timelines with the timelines/chains produced by the owner's
> own project, so the two approaches can be evaluated on the same notes.

Because this fork is a comparison baseline, **stay close to upstream TTA
code**. Prefer running the released runners unchanged; document every
deviation from upstream (file + reason) so the comparison stays fair and
reproducible.

## 3. Data layer

- **Shift summaries** are rows in the owner's OMOP database: table `note`
  where `note_source_value = 'Shift Summary'`.
- **Connection details:** `data.md` (owner-local, gitignored). If it is absent
  from this fork, recreate it locally from the owner's project — never commit
  credentials. The database reference is `omop-database-structure.md` when
  present.
- The known local instance is Postgres on `localhost:54320` (user `postgres`,
  database `OHDSI-CDM_V54`, schema `cdm_synthea`). Treat connection info as
  owner-local and verify it against `data.md`; do not hardcode public IPs.
- **Shift summaries must be exported to `.txt`** (one file per note) before
  the TTA runners can read them. Export with
  `comparison/export_shift_summaries.py` (notes + manifest land in
  `comparison/data/`, gitignored); see `comparison/README.md` and
  `.opencode/tests/shift-summaries-text-only.md`.
- **No structured EHR calibration yet.** This fork does not ship MIMIC
  structured tables, and the owner has not yet defined a structured source
  for these notes. Current runs are **text-only**.

## 4. Current scope

- **Text-only multistep pipelines** (`unimodal/`): extract central events →
  pairwise time distances → central timeline → non-central events → full
  timeline. Output: `timeline.json` as `event | time` in hours.
- **Out of scope for now:** the multimodal (`langgraph_scripts/`) and
  ablation variants, `compare_tts/` reference-based metrics, and
  `gap_detection/` — they either need structured data or manual reference
  annotations.
- Prompts are kept **as upstream**: time zero follows the discharge-summary
  convention (admission when stated, else case presentation). Shift summaries
  are not discharge summaries; record this caveat in any comparison.
- Comparison measurements (event-match rate, concordance, log-time
  discrepancy/AULTC, event counts) are recorded in `.opencode/measurements.md`
  as they are defined.

## 5. Repository map

| Path | Role |
|---|---|
| `README.md` | Upstream paper README: method, workflow, quick start, data caveats. |
| `unimodal/` | **Text-only multistep runner** (`langgraph_runner_unimodal.py`), its `templates/`, `run_langgraph.sh`, `requirements.txt`. The current runnable path. Upstream, unmodified. |
| `comparison/` | **Shift-summary baseline workspace:** export script, local vLLM auth proxy (`vllm_proxy.py`), run wrapper (`run_text_only.sh`), `data/` (notes + manifest) and `outputs/` (runs) — both gitignored. |
| `prompt_templates/` | Canonical prompt templates for every workflow node (+ `README.md`). `unimodal/templates/` is a copy of the 5 text-only ones. |
| `langgraph_scripts/` | Full multimodal runners, ablations, single-step runner, `mimic_struct_utils.py` (structured retrieval), `create_structured_timeline.py`, Slurm/server launchers. **Import-broken as forked** — see `pipeline` subagent. |
| `compare_tts/` | R/Python evaluation of predicted vs manual timelines (event matching, concordance, log-time discrepancy/AULTC). |
| `gap_detection/` | Textual-vs-structured gap analysis (`well_captured`, `complete_absence`, `temporal_mismatch`, `semantic_distance`, `detail_gap`). |
| `threshold_sweep.ipynb` | Exploratory event-match threshold analysis. |
| `environment.yml` | Conda export from the study runtime (cluster paths inside; not directly portable). |
| `TTA-Overview-*` | Paper overview figures. |
| `.opencode/` | Agent configuration (this file, subagents, measurements, notes, test plans). |

Runners load prompts from `templates/` **relative to the current working
directory**; run `unimodal/langgraph_runner_unimodal.py` from inside
`unimodal/`, or create a symlink.

## 6. Conventions for AI agents

- Read this file first; for pipeline mechanics use the `pipeline` subagent,
  for database/export work the `db` subagent.
- **Comparison-baseline rule:** do not change upstream algorithm code or
  prompts to improve results without recording the deviation (file, reason,
  date) — an unrecorded change invalidates the comparison.
- **No fabricated numbers.** Every reported quantity must trace to an
  artifact (per-case JSON, manifest, run log). Record measurement definitions
  in `.opencode/measurements.md`; write test plans under `.opencode/tests/`
  following `.opencode/tests/README.md`.
- **Strict LLM output protocol.** Templates request raw BSV (`|`-delimited,
  one header line, no prose/fences). Reasoning models need
  `--omit_tags think`. Parsers silently drop malformed rows — check that
  outputs are non-empty.
- **No secrets in the repo.** `data.md`, `.env`, connection strings, keys and
  note text stay owner-local and gitignored.
- **Delegating:** use the project subagents below instead of loading all
  domain context into the main session.
- **Code conventions:** every function has a docstring (what it does,
  params, return value). Comments are sparse — only for non-obvious *why*.

## 7. Subagents

Subagents live in `.opencode/agents/` and are invoked via the Task tool.

**Primary agent.** `coder` (`.opencode/agents/coder.md`) is the main-session
implementation agent (switch with Tab). The table lists it plus the subagents
delegated to from the main session.

| Agent | File | Delegate when… |
|---|---|---|
| `coder` | `.opencode/agents/coder.md` | Writing/changing any code (primary agent). |
| `pipeline` | `.opencode/agents/pipeline.md` | Running or understanding the timeline runners: `unimodal/`, `langgraph_scripts/`, templates, BSV protocol, resume/caching, LLM endpoint wiring, multimodal-runner status. |
| `db` | `.opencode/agents/db.md` | OMOP database work: connection, querying `note` for shift summaries, exporting `.txt` files, (future) structured-row export. Read-only. |
| `analysis` | `.opencode/agents/analysis.md` | Notebooks and results: `threshold_sweep.ipynb`, per-case timelines under `outputs/`, `compare_tts/` outputs, `gap_detection/` results, cross-project comparison. Read-only. |
| `debug` | `.opencode/agents/debug.md` | Runtime failures: endpoint errors, empty/parse-failed BSV, missing `templates/`, `mimic_struct_utils.py` import crashes, CUDA/model errors, missing `hadm_id`. Read-only. |
| `docs` | `.opencode/agents/docs.md` | Documentation maintenance across READMEs and `.opencode/*.md`. Cheaper model. |
| `paperwriter` | `.opencode/agents/paperwriter.md` | Drafting/reviewing paper sections and auditing claims against artifacts. Read-only. |
| `git_resolver` | `.opencode/agents/git_resolver.md` | Anything requiring git/history. The ONLY agent with git access. |

**Manual-only:** `measurements` (`.opencode/measurements.md`) and
`.opencode/tests/` are maintained by hand, not delegated.

**Git rule:** git commands are denied in the main session and in every
subagent except `git_resolver`.

## 8. Status

Fork used as a text-only comparison baseline. The `unimodal/` multistep
pipeline is runnable today and the shift-summary export is done
(`comparison/`, 347 notes); the multimodal/structured path is planned.
Upstream release code is otherwise unmodified; recorded deviations live in
`comparison/README.md`.