"""Build the TTA-vs-reference table and the summary metrics.

The definitions mirror the owner's existing weaning comparison
(`weaning_event/time_difference_findings.md`): difference in minutes computed
as ``extracted - structured``, median signed/absolute difference, tolerance
bands, and a matched flag. Only the target events are kept.
"""

from __future__ import annotations

import pandas as pd


def build_comparison(
    tta_targets: pd.DataFrame,
    reference: pd.DataFrame,
    event_map: dict[str, str],
    source: str = "structured",
) -> pd.DataFrame:
    """Join TTA target times with the reference and compute differences.

    Args:
        tta_targets: Output of ``targets.resolve_targets``.
        reference: Long reference DataFrame (``patient_id, source, event, time``).
        event_map: ``{concept: reference_event_name}``.
        source: Reference source to compare against (e.g. ``"structured"``).
    Returns:
        Long DataFrame: one row per (patient, event) with ``ref_time``,
        ``tta_time``, ``diff_min`` (= tta - ref), ``abs_diff_min`` and
        ``matched``. Patients present only in the reference keep a NaT
        ``tta_time``.
    """
    reference = reference[
        reference["event"].isin(event_map.values()) & (reference["source"] == source)
    ].copy()
    reference = reference.drop_duplicates(subset=["patient_id", "event"], keep="first")
    reference = reference.rename(columns={"time": "ref_time"})
    reverse = {reference_event: concept for concept, reference_event in event_map.items()}
    reference["concept"] = reference["event"].map(reverse)

    tta = tta_targets.rename(columns={"time": "tta_time"})
    tta_columns = [
        "patient_id",
        "event",
        "concept",
        "tta_time",
        "note_id",
        "matched_text",
        "score",
        "n_candidates",
        "status",
        "anchor_source",
        "anchor_stable",
        "offset_min",
    ]
    out = reference.merge(tta[tta_columns], on=["patient_id", "event"], how="outer")
    out["diff_min"] = (out["tta_time"] - out["ref_time"]).dt.total_seconds() / 60.0
    out["abs_diff_min"] = out["diff_min"].abs()
    out["matched"] = out["diff_min"].notna()
    return out.reset_index(drop=True)


def summarize(
    comparison: pd.DataFrame, tolerances: tuple[int, ...] = (5, 15, 30, 60)
) -> pd.DataFrame:
    """Per-event summary of the comparison.

    Args:
        comparison: Output of ``build_comparison``.
        tolerances: Absolute-difference thresholds in minutes.
    Returns:
        One row per event: reference/tta/matched counts, median signed and
        absolute difference, p90 absolute difference, and the percentage of
        matched pairs within each tolerance.
    """
    rows = []
    for event, group in comparison.groupby("event", dropna=False):
        matched = group[group["matched"]]
        row = {
            "event": event,
            "n_ref": int(group["ref_time"].notna().sum()),
            "n_tta": int(group["tta_time"].notna().sum()),
            "n_matched": int(len(matched)),
        }
        if len(matched):
            row["median_diff_min"] = float(matched["diff_min"].median())
            row["median_abs_diff_min"] = float(matched["abs_diff_min"].median())
            row["p90_abs_diff_min"] = float(matched["abs_diff_min"].quantile(0.9))
            for tolerance in tolerances:
                row[f"pct_within_{tolerance}min"] = float(
                    (matched["abs_diff_min"] <= tolerance).mean() * 100.0
                )
        rows.append(row)
    return pd.DataFrame(rows)


def anchor_summary(anchors: pd.DataFrame) -> pd.DataFrame:
    """Counts and spread of the per-note anchor estimates.

    Args:
        anchors: Output of ``anchors.estimate_all_anchors``.
    Returns:
        Small summary DataFrame (source/stable counts and offset/MAD stats).
    """
    if anchors is None or anchors.empty:
        return pd.DataFrame()
    rows = []
    for (source, stable), group in anchors.groupby(["source", "stable"], dropna=False):
        rows.append(
            {
                "source": source,
                "stable": bool(stable),
                "n_notes": int(len(group)),
                "median_offset_min": float(group["offset_min"].median()),
                "median_mad_min": float(group["mad_min"].median()),
                "p90_mad_min": float(group["mad_min"].quantile(0.9)),
            }
        )
    return pd.DataFrame(rows)


def to_event_decisions(tta_targets: pd.DataFrame, run_id: str = "tta") -> pd.DataFrame:
    """Convert resolved TTA targets to the owner's ``all_event_decisions`` schema.

    Args:
        tta_targets: Output of ``targets.resolve_targets``.
        run_id: Identifier written into the ``run_id`` column.
    Returns:
        DataFrame with ``patient_id, event, group, state, answer, reason,
        confidence, run_id, run_timestamp``. ``state`` is ``FOUND`` for timed
        matches and ``NOT_FOUND_IN_CHAIN`` otherwise.
    """
    frame = tta_targets.copy()
    frame["group"] = "weaning"
    frame["state"] = frame["status"].map(
        {"FOUND": "FOUND", "MISSING": "NOT_FOUND_IN_CHAIN"}
    ).fillna("NOT_FOUND_IN_CHAIN")
    frame["answer"] = frame["time"].dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    note = frame["note_id"].astype("Int64").astype(str)
    frame["reason"] = (
        "TTA event '"
        + frame["matched_text"].fillna("")
        + "'"
        + "; note "
        + note
        + "; anchor="
        + frame["anchor_source"].fillna("")
        + ", offset="
        + frame["offset_min"].round(1).astype(str)
        + " min"
    )
    frame["confidence"] = frame["score"]
    frame["run_id"] = run_id
    frame["run_timestamp"] = pd.Timestamp.now()
    return frame[
        [
            "patient_id",
            "event",
            "group",
            "state",
            "answer",
            "reason",
            "confidence",
            "run_id",
            "run_timestamp",
        ]
    ]