"""Map TTA timeline events to the owner's target events.

In scope for now: admission, separation attempt, and weaning. The keyword
tables are intentionally plain and editable from the notebook; the caller
maps each concept to the reference table's event name (e.g. ``visit_start``).
"""

from __future__ import annotations

import pandas as pd

from .common import normalize_text, similarity

#: Edit these lists from the notebook when the wording of the notes changes.
CONCEPTS = {
    "admission": {
        "keywords": [
            "admission",
            "admitted",
            "hospital admission",
            "icu admission",
            "transferred to icu",
            "transfer to icu",
            "admitted to icu",
        ],
        "negative": [
            "pre admission",
            "preadmission",
            "admission weight",
            "admission swab",
        ],
    },
    "separation_attempt": {
        "keywords": [
            "separation attempt",
            "sbt",
            "spontaneous breathing trial",
            "low support",
            "psv",
            "pressure support",
            "cpap",
            "t piece",
            "t-piece",
            "weaning trial",
            "trial of spontaneous breathing",
        ],
        "negative": [],
    },
    "weaning": {
        "keywords": [
            "weaning",
            "wean",
            "weaned",
            "reduce support",
            "weaning started",
            "weaning commenced",
            "imv weaning",
            "start weaning",
        ],
        "negative": [
            "weaning plan",
            "plan to wean",
            "planned to wean",
            "consider weaning",
        ],
    },
}


def score_text(text: str, concept: dict) -> tuple[float, str]:
    """Score how strongly a TTA event matches one concept.

    Args:
        text: TTA event string.
        concept: One entry of ``CONCEPTS`` (``keywords``/``negative``).
    Returns:
        ``(score, matched_keyword)``; the score is 0..1, reduced by 0.35 when
        a negative phrase is present.
    """
    normalized = normalize_text(text)
    best, best_keyword = 0.0, ""
    for keyword in concept["keywords"]:
        key = normalize_text(keyword)
        if not key:
            continue
        score = 1.0 if key in normalized else similarity(text, keyword) * 0.9
        if score > best:
            best, best_keyword = score, keyword
    penalty = 0.35 if any(normalize_text(neg) in normalized for neg in concept["negative"]) else 0.0
    return max(0.0, best - penalty), best_keyword


def candidate_table(tta_abs: pd.DataFrame, threshold: float = 0.5) -> pd.DataFrame:
    """All TTA events that match at least one target concept.

    Args:
        tta_abs: TTA events with ``person_id``, ``note_id``, ``event``,
            ``hours``, ``abs_time``, ``source``, ``stable`` and ``offset_min``.
        threshold: Minimum concept score.
    Returns:
        Long DataFrame: ``concept, patient_id, note_id, event, hours,
        abs_time, score, matched_keyword, anchor_source, anchor_stable,
        offset_min`` (empty, but with these columns, when nothing matches).
    """
    columns = [
        "concept",
        "patient_id",
        "note_id",
        "event",
        "hours",
        "abs_time",
        "score",
        "matched_keyword",
        "anchor_source",
        "anchor_stable",
        "offset_min",
    ]
    rows = []
    for concept_name, concept in CONCEPTS.items():
        for row in tta_abs.itertuples(index=False):
            if pd.isna(row.abs_time) or pd.isna(row.person_id):
                continue
            score, keyword = score_text(row.event, concept)
            if score < threshold:
                continue
            rows.append(
                {
                    "concept": concept_name,
                    "patient_id": int(row.person_id),
                    "note_id": int(row.note_id),
                    "event": row.event,
                    "hours": float(row.hours),
                    "abs_time": row.abs_time,
                    "score": score,
                    "matched_keyword": keyword,
                    "anchor_source": row.source,
                    "anchor_stable": bool(row.stable),
                    "offset_min": float(row.offset_min) if pd.notna(row.offset_min) else float("nan"),
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _apply_override(group: pd.DataFrame, rule: dict | None) -> pd.DataFrame:
    """Narrow a candidate group with a manual override rule.

    Args:
        group: Candidates for one (patient, concept).
        rule: ``{"note_id": int|None, "contains": str|None}`` or None.
    Returns:
        Filtered candidates (the original group when no rule applies).
    """
    if not rule:
        return group
    if rule.get("note_id") is not None:
        group = group[group["note_id"] == int(rule["note_id"])]
    if rule.get("contains"):
        group = group[
            group["event"].str.contains(rule["contains"], case=False, na=False)
        ]
    return group


def resolve_targets(
    candidates: pd.DataFrame,
    event_map: dict[str, str],
    overrides: dict | None = None,
    patients: list[int] | None = None,
) -> pd.DataFrame:
    """Pick one TTA event per patient and target event.

    Args:
        candidates: Output of ``candidate_table``.
        event_map: ``{concept: reference_event_name}``, e.g.
            ``{"admission": "visit_start", ...}``.
        overrides: Optional ``{(patient_id, concept): {"note_id": ...,
            "contains": "..."}}`` manual selections.
        patients: Patient ids to emit (missing ones become ``MISSING``); when
            omitted, only patients with candidates are emitted.
    Returns:
        DataFrame: ``patient_id, event, concept, time, note_id, matched_text,
        score, n_candidates, status, anchor_source, anchor_stable,
        offset_min``. The earliest absolute time wins; ``status`` is
        ``"FOUND"`` or ``"MISSING"`` (always with these columns, even when
        empty).
    """
    columns = [
        "patient_id",
        "event",
        "concept",
        "time",
        "note_id",
        "matched_text",
        "score",
        "n_candidates",
        "status",
        "anchor_source",
        "anchor_stable",
        "offset_min",
    ]
    overrides = overrides or {}
    patient_ids = sorted(
        int(pid) for pid in (patients if patients is not None else candidates["patient_id"].dropna().unique())
    )
    rows = []
    for patient_id in patient_ids:
        for concept, reference_event in event_map.items():
            group = candidates[
                (candidates["patient_id"] == patient_id) & (candidates["concept"] == concept)
            ]
            group = _apply_override(group, overrides.get((patient_id, concept)))
            if group.empty:
                rows.append(
                    {
                        "patient_id": patient_id,
                        "event": reference_event,
                        "concept": concept,
                        "time": pd.NaT,
                        "note_id": pd.NA,
                        "matched_text": "",
                        "score": float("nan"),
                        "n_candidates": 0,
                        "status": "MISSING",
                        "anchor_source": "",
                        "anchor_stable": False,
                        "offset_min": float("nan"),
                    }
                )
                continue
            best = group.sort_values(["abs_time", "score"], ascending=[True, False]).iloc[0]
            rows.append(
                {
                    "patient_id": patient_id,
                    "event": reference_event,
                    "concept": concept,
                    "time": best["abs_time"],
                    "note_id": int(best["note_id"]),
                    "matched_text": best["event"],
                    "score": float(best["score"]),
                    "n_candidates": int(len(group)),
                    "status": "FOUND",
                    "anchor_source": best["anchor_source"],
                        "anchor_stable": bool(best["anchor_stable"]),
                        "offset_min": float(best["offset_min"]) if pd.notna(best["offset_min"]) else float("nan"),
                    }
                )
    return pd.DataFrame(rows, columns=columns)