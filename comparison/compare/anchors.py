"""Estimate TTA's per-note time origin (hours -> absolute datetimes).

TTA emits ``event|hours`` relative to a time zero it inferred. The notes are
shift logs whose lines carry clock times; matching the predicted events to
those timed lines lets us convert hours into real datetimes:

    absolute = origin + hours

``origin`` is estimated per note as the median of ``line_time - hours`` over
matched pairs, and is reported together with its residual spread so unstable
notes can be flagged instead of silently trusted.
"""

from __future__ import annotations

import pandas as pd

from .common import clock_candidates, similarity
from .loaders import read_note_text


def node_candidates(nodes, note_id, min_confidence: float = 0.8) -> list[dict]:
    """Clock-resolved ``event_nodes`` rows of one note as anchor candidates.

    Args:
        nodes: DataFrame from ``loaders.load_event_nodes`` (may be None).
        note_id: Integer note id.
        min_confidence: Minimum ``time_confidence``.
    Returns:
        List of dicts ``{"time": Timestamp, "text": str}``.
    """
    if nodes is None or nodes.empty:
        return []
    subset = nodes[
        (nodes["note_id"] == note_id)
        & (nodes["time_source"] == "note_clock")
        & (nodes["time_confidence"].fillna(0) >= min_confidence)
    ]
    out = []
    for row in subset.itertuples(index=False):
        if pd.isna(row.event_time):
            continue
        text = row.evidence or row.event_type or ""
        out.append({"time": row.event_time, "text": str(text)})
    return out


def _best_candidate(event_text: str, candidates: list[dict]) -> tuple[dict | None, float]:
    """Return the best-matching candidate and its similarity score.

    Args:
        event_text: TTA event string.
        candidates: Anchor candidates with ``text``.
    Returns:
        ``(candidate or None, score)``.
    """
    best, best_score = None, 0.0
    for candidate in candidates:
        score = similarity(event_text, candidate["text"])
        if score > best_score:
            best, best_score = candidate, score
    return best, best_score


def estimate_note_anchor(
    note_id: int,
    tta_events: pd.DataFrame,
    note_datetime,
    candidates: list[dict],
    min_pairs: int = 3,
    min_score: float = 0.35,
    max_mad_min: float = 120.0,
) -> dict:
    """Estimate the absolute origin of one note's TTA hour axis.

    Args:
        note_id: Integer note id.
        tta_events: DataFrame with ``event`` and ``hours`` for this note.
        note_datetime: The note's entry time (used as fallback origin).
        candidates: Anchor candidates with ``"time"`` and ``"text"``.
        min_pairs: Minimum matched pairs to trust the estimate.
        min_score: Minimum text similarity to accept a pair.
        max_mad_min: Maximum median absolute deviation (minutes) for a
            "stable" estimate.
    Returns:
        Dict with ``note_id``, ``origin`` (Timestamp), ``offset_min`` (origin
        minus note_datetime), ``n_pairs``, ``mad_min``, ``source``
        (``"event_nodes"``/``"note_lines"``/``"fallback_note_datetime"``) and
        ``stable``.
    """
    pairs = []
    if tta_events is not None and not tta_events.empty and candidates:
        for row in tta_events.itertuples(index=False):
            candidate, score = _best_candidate(row.event, candidates)
            if candidate is None or score < min_score:
                continue
            origin = candidate["time"] - pd.Timedelta(hours=float(row.hours))
            pairs.append(origin)

    if len(pairs) < min_pairs:
        return {
            "note_id": note_id,
            "origin": note_datetime,
            "offset_min": 0.0,
            "n_pairs": len(pairs),
            "mad_min": float("nan"),
            "source": "fallback_note_datetime",
            "stable": False,
        }

    origins = pd.Series(pairs)
    origin = origins.median()
    mad = (origins - origin).abs().dt.total_seconds().div(60).median()
    if pd.notna(mad) and mad > 0:
        tolerance = pd.Timedelta(minutes=max(90.0, 3.0 * float(mad)))
        kept = origins[(origins - origin).abs() <= tolerance]
        if len(kept) >= min_pairs:
            origins = kept
            origin = origins.median()
            mad = (origins - origin).abs().dt.total_seconds().div(60).median()

    return {
        "note_id": note_id,
        "origin": origin,
        "offset_min": (origin - note_datetime).total_seconds() / 60.0,
        "n_pairs": len(pairs),
        "mad_min": float(mad) if pd.notna(mad) else float("nan"),
        "source": "event_nodes",
        "stable": bool(pd.notna(mad) and mad <= max_mad_min),
    }


def estimate_all_anchors(
    notes: pd.DataFrame,
    tta: pd.DataFrame,
    data_dir,
    nodes: pd.DataFrame | None = None,
    anchor_source: str = "event_nodes",
    min_pairs: int = 3,
    min_score: float = 0.35,
    max_mad_min: float = 120.0,
) -> pd.DataFrame:
    """Estimate an anchor for every note that has a TTA timeline.

    Args:
        notes: Manifest DataFrame (``note_id``, ``note_datetime``).
        tta: TTA events DataFrame (``note_id``, ``event``, ``hours``).
        data_dir: ``comparison/data`` for note text fallback.
        nodes: Optional clock-resolved ``event_nodes`` DataFrame.
        anchor_source: ``"event_nodes"`` (preferred) or ``"note_lines"``.
        min_pairs: Minimum matched pairs per note.
        min_score: Minimum similarity to accept a pair.
        max_mad_min: Maximum stable residual MAD in minutes.
    Returns:
        DataFrame with one row per anchored note (empty, but with the same
        columns, when there is nothing to anchor).
    """
    columns = [
        "note_id",
        "origin",
        "offset_min",
        "n_pairs",
        "mad_min",
        "source",
        "stable",
    ]
    if tta is None or tta.empty or "note_id" not in tta:
        return pd.DataFrame(columns=columns)
    notes_by_id = notes.set_index("note_id")
    rows = []
    for note_id, group in tta.groupby("note_id"):
        if note_id not in notes_by_id.index:
            continue
        note_datetime = notes_by_id.at[note_id, "note_datetime"]
        candidates: list[dict] = []
        source_used = "note_lines"
        if anchor_source == "event_nodes" and nodes is not None:
            candidates = node_candidates(nodes, note_id)
            source_used = "event_nodes"
        if not candidates:
            candidates = clock_candidates(
                read_note_text(data_dir, note_id), note_datetime
            )
            source_used = "note_lines"
        result = estimate_note_anchor(
            note_id,
            group,
            note_datetime,
            candidates,
            min_pairs=min_pairs,
            min_score=min_score,
            max_mad_min=max_mad_min,
        )
        if result["source"] != "fallback_note_datetime":
            result["source"] = source_used
        rows.append(result)
    return pd.DataFrame(rows, columns=columns)


def apply_anchors(tta: pd.DataFrame, anchors: pd.DataFrame) -> pd.DataFrame:
    """Attach absolute predicted times to the TTA event table.

    Args:
        tta: TTA events (``note_id``, ``event``, ``hours``).
        anchors: DataFrame from ``estimate_all_anchors``.
    Returns:
        The input plus ``origin``, ``abs_time``, ``source``, ``stable``,
        ``n_pairs``, ``mad_min`` and ``offset_min``. ``abs_time`` is NaT for
        notes without an anchor estimate.
    """
    columns = ["note_id", "origin", "source", "stable", "n_pairs", "mad_min", "offset_min"]
    if tta is None or tta.empty or "note_id" not in tta:
        return pd.DataFrame(columns=["note_id", "event", "hours", "abs_time", *columns[1:]])
    merged = tta.merge(anchors[columns], on="note_id", how="left")
    merged["abs_time"] = merged["origin"] + pd.to_timedelta(merged["hours"], unit="h")
    return merged