"""Shared helpers for the TTA-vs-reference comparison.

Pure standard library: text normalization, token similarity, and the
clock-time -> absolute-datetime rule used to locate TTA's time origin.
"""

from __future__ import annotations

import difflib
import re
from datetime import datetime, time, timedelta

CLOCK_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\b")
CLEAN_RE = re.compile(r"[^a-z0-9 ]+")
HRS_RE = re.compile(r"\b(hrs?|hours?)\b", flags=re.IGNORECASE)

STOPWORDS = frozenset(
    """a an and are as at be been being but by for from had has have he her his
    i if in into is it its me my no not of on or our patient pt she so than
    that the their them then there these they this those to was we were what
    when where which while who will with would""".split()
)


def normalize_text(text: str) -> str:
    """Lower-case, drop punctuation/underscores, and collapse whitespace.

    Args:
        text: Raw event or line text (may be None).
    Returns:
        Normalized string with single spaces (``""`` for None).
    """
    if not text:
        return ""
    text = str(text).lower().replace("_", " ").replace("-", " ")
    text = CLEAN_RE.sub(" ", text)
    return " ".join(text.split())


def token_set(text: str) -> set[str]:
    """Normalized content tokens (stopwords and 1-character tokens removed).

    Args:
        text: Raw text.
    Returns:
        Set of tokens.
    """
    return {
        token
        for token in normalize_text(text).split()
        if len(token) > 1 and token not in STOPWORDS
    }


def similarity(a: str, b: str) -> float:
    """Similarity in [0, 1] between two short texts.

    Combines token Dice overlap with a character-level ratio so paraphrases
    score high without requiring shared word order.

    Args:
        a: First text.
        b: Second text.
    Returns:
        Similarity score (0.0 when either text is empty).
    """
    tokens_a, tokens_b = token_set(a), token_set(b)
    if not tokens_a or not tokens_b:
        return 0.0
    dice = 2.0 * len(tokens_a & tokens_b) / (len(tokens_a) + len(tokens_b))
    ratio = difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()
    return max(dice, ratio)


def parse_clock(line: str) -> time | None:
    """Return the first ``HH:MM(:SS)`` time in a line, if any.

    Args:
        line: One line of note text.
    Returns:
        ``datetime.time`` or None.
    """
    match = CLOCK_RE.search(line or "")
    if not match:
        return None
    hour, minute = match.group(1), match.group(2)
    second = match.group(3) or "0"
    return time(int(hour), int(minute), int(second))


def nearest_day(clock: time, anchor: datetime) -> datetime:
    """Place a clock time on the calendar day closest to ``anchor``.

    The notes carry clock times but no dates; this matches the owner's
    `event_nodes` slicer rule (pick the day with the smallest gap to the
    note's entry time, which also handles midnight rollover).

    Args:
        clock: Clock time parsed from the note.
        anchor: The note's ``note_datetime``.
    Returns:
        Absolute naive datetime.
    """
    best: tuple[float, datetime] | None = None
    for delta in (-1, 0, 1):
        candidate = datetime.combine(anchor.date() + timedelta(days=delta), clock)
        gap = abs((candidate - anchor).total_seconds())
        if best is None or gap < best[0]:
            best = (gap, candidate)
    return best[1]


def clock_candidates(
    note_text: str, note_datetime: datetime, min_text_chars: int = 3
) -> list[dict]:
    """Timed lines of a note as anchor candidates.

    Args:
        note_text: Full note text.
        note_datetime: The note's entry time (date anchor).
        min_text_chars: Ignore lines whose residual text is shorter than this.
    Returns:
        List of dicts ``{"time": datetime, "text": str, "line": str}``.
    """
    out = []
    for raw in (note_text or "").splitlines():
        clock = parse_clock(raw)
        if clock is None:
            continue
        text = CLOCK_RE.sub(" ", raw, count=1)
        text = HRS_RE.sub(" ", text).strip()
        if len(normalize_text(text)) < min_text_chars:
            continue
        out.append(
            {
                "time": nearest_day(clock, note_datetime),
                "text": text,
                "line": raw.strip(),
            }
        )
    return out


def format_dt(value) -> str:
    """Format a datetime as ``YYYY-MM-DD HH:MM:SS`` (``""`` for null).

    Args:
        value: A datetime/Timestamp or None/NaT.
    Returns:
        Formatted string, or an empty string.
    """
    if value is None:
        return ""
    try:
        if value != value:  # NaT is not equal to itself
            return ""
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except (AttributeError, ValueError):
        return ""