"""Data loaders for the TTA-vs-reference comparison.

Inputs:

* the owner's reference table (long format, e.g. ``table6_weaning.csv``),
* the exported note manifest and note text (``comparison/data``),
* a TTA run's per-note ``timeline.json`` (``comparison/outputs/<run>``),
* optionally, the owner's ``event_nodes`` JSONL for anchor timestamps.

All functions return pandas DataFrames with a small, stable set of columns so
another reference file can be plugged in without touching the notebook logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _require_columns(frame: pd.DataFrame, required: list[str], path) -> None:
    """Raise a clear error when a required column is missing.

    Args:
        frame: Loaded DataFrame.
        required: Column names that must exist.
        path: Source path, used in the error message.
    Returns:
        None.
    """
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}; found {list(frame.columns)}")


def load_long_reference(
    path,
    patient_col: str = "patient_id",
    source_col: str = "source",
    event_col: str = "event",
    time_col: str = "time",
) -> pd.DataFrame:
    """Load a long reference table (one timestamp per patient/event).

    Args:
        path: CSV path.
        patient_col: Patient identifier column.
        source_col: Method/source column (optional).
        event_col: Event-name column.
        time_col: Timestamp column.
    Returns:
        DataFrame with ``patient_id``, ``source``, ``event`` and ``time``
        (datetime); rows without a parseable timestamp are dropped.
    """
    frame = pd.read_csv(path)
    _require_columns(frame, [patient_col, event_col, time_col], path)
    source = (
        frame[source_col].astype(str)
        if source_col in frame.columns
        else pd.Series("reference", index=frame.index)
    )
    out = pd.DataFrame(
        {
            "patient_id": pd.to_numeric(frame[patient_col], errors="coerce").astype("Int64"),
            "source": source,
            "event": frame[event_col].astype(str).str.strip(),
            "time": pd.to_datetime(frame[time_col], errors="coerce"),
        }
    )
    return out.dropna(subset=["patient_id", "time"]).reset_index(drop=True)


def load_structured_wide(
    path,
    events: list[str],
    patient_col: str = "person_id",
    dayfirst: bool = True,
    format: str | None = None,
) -> pd.DataFrame:
    """Load a wide structured table (one column per event) into long format.

    Args:
        path: CSV path (e.g. ``weaning_structured.csv``).
        events: Event columns to read.
        patient_col: Patient identifier column.
        dayfirst: Parse ambiguous dates day-first (structured table convention).
        format: Explicit pandas datetime format (e.g. ``"ISO8601"``); takes
            precedence over ``dayfirst`` for unambiguous ISO files.
    Returns:
        Long DataFrame with ``patient_id``, ``source="structured"``, ``event``
        and ``time``; blanks and the 1970 epoch placeholder are dropped.
    """
    frame = pd.read_csv(path)
    _require_columns(frame, [patient_col], path)
    rows = []
    for column in events:
        if column not in frame.columns:
            continue
        parsed = pd.to_datetime(frame[column], errors="coerce", dayfirst=dayfirst, format=format)
        for patient_id, value in zip(frame[patient_col], parsed):
            if pd.isna(patient_id) or pd.isna(value) or value.year <= 1970:
                continue
            rows.append(
                {
                    "patient_id": int(patient_id),
                    "source": "structured",
                    "event": column,
                    "time": value,
                }
            )
    return pd.DataFrame(rows)


def load_note_manifest(path) -> pd.DataFrame:
    """Load the export manifest (note id -> patient/visit/note_datetime).

    Args:
        path: ``comparison/data/manifest.csv``.
    Returns:
        DataFrame with ``note_id``, ``person_id``, ``visit_occurrence_id``,
        ``note_datetime`` and ``filename``; rows without a timestamp are
        dropped.
    """
    frame = pd.read_csv(path)
    _require_columns(frame, ["note_id", "person_id", "note_datetime"], path)
    visit = (
        pd.to_numeric(frame["visit_occurrence_id"], errors="coerce").astype("Int64")
        if "visit_occurrence_id" in frame.columns
        else pd.Series(pd.NA, index=frame.index, dtype="Int64")
    )
    filename = (
        frame["filename"].astype(str)
        if "filename" in frame.columns
        else pd.Series("", index=frame.index)
    )
    out = pd.DataFrame(
        {
            "note_id": frame["note_id"].astype(int),
            "person_id": pd.to_numeric(frame["person_id"], errors="coerce").astype("Int64"),
            "visit_occurrence_id": visit,
            "note_datetime": pd.to_datetime(frame["note_datetime"], errors="coerce"),
            "filename": filename,
        }
    )
    return out.dropna(subset=["note_datetime"]).reset_index(drop=True)


def load_tta_run(run_dir) -> pd.DataFrame:
    """Load every ``note_<id>/timeline.json`` under a TTA run directory.

    Args:
        run_dir: ``comparison/outputs/<run>`` directory.
    Returns:
        DataFrame with one row per predicted event: ``note_id``, ``idx``,
        ``event`` and ``hours``. Empty when no case has a timeline.
    """
    run_dir = Path(run_dir)
    rows = []
    skipped = 0
    for case_dir in sorted(run_dir.glob("note_*")):
        timeline = case_dir / "timeline.json"
        if not timeline.is_file():
            skipped += 1
            continue
        try:
            payload = json.loads(timeline.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped += 1
            continue
        try:
            note_id = int(case_dir.name.split("_", 1)[1])
        except (IndexError, ValueError):
            skipped += 1
            continue
        for index, item in enumerate(payload or []):
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                event, hours = item[0], item[1]
            elif isinstance(item, dict):
                event, hours = item.get("event"), item.get("time")
            else:
                continue
            try:
                hours = float(hours)
            except (TypeError, ValueError):
                continue
            rows.append(
                {"note_id": note_id, "idx": index, "event": str(event), "hours": hours}
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        print(f"[warn] no timeline events found in {run_dir} (skipped {skipped} cases)")
        return frame
    return frame.sort_values(["note_id", "hours", "idx"]).reset_index(drop=True)


def load_event_nodes(path, note_ids=None, max_rows: int | None = None) -> pd.DataFrame:
    """Stream the owner's ``nodes.jsonl`` into clock-resolved anchor candidates.

    Args:
        path: ``nodes.jsonl`` path.
        note_ids: Optional iterable of note ids to keep.
        max_rows: Optional cap on read rows (for quick looks).
    Returns:
        DataFrame with ``note_id``, ``event_type``, ``event_time``,
        ``time_source``, ``time_confidence`` and ``evidence``.
    """
    keep = {int(note_id) for note_id in note_ids} if note_ids is not None else None
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if max_rows is not None and index >= max_rows:
                break
            try:
                node = json.loads(line)
            except json.JSONDecodeError:
                continue
            note_id = node.get("note_id")
            if note_id is None:
                continue
            note_id = int(note_id)
            if keep is not None and note_id not in keep:
                continue
            rows.append(
                {
                    "note_id": note_id,
                    "event_type": node.get("event_type"),
                    "event_time": node.get("event_time"),
                    "time_source": node.get("time_source"),
                    "time_confidence": node.get("time_confidence"),
                    "evidence": node.get("evidence"),
                }
            )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["event_time"] = pd.to_datetime(frame["event_time"], errors="coerce")
    return frame


def read_note_text(data_dir, note_id) -> str:
    """Read one exported note as text (``note_<id>.txt``).

    Args:
        data_dir: ``comparison/data`` directory.
        note_id: Integer note id.
    Returns:
        Note text, or ``""`` when the file is missing.
    """
    path = Path(data_dir) / f"note_{int(note_id)}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""