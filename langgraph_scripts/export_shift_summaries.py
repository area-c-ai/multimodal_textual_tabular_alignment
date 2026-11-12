"""Export OMOP shift-summary notes to .txt files plus a manifest.

Reads rows from the `note` table whose `note_source_value` matches the
requested source (default ``Shift Summary``) and writes one UTF-8 ``.txt``
file per note into ``--out-dir``, named ``note_<note_id>.txt``. A
``manifest.csv`` in the same folder records the identifiers and text lengths
so results can be joined back to ``note_id``/``person_id``.

Connection parameters come from CLI flags, falling back to the standard
``PG*`` environment variables (``PGHOST``, ``PGPORT``, ``PGUSER``,
``PGPASSWORD``, ``PGDATABASE``). Optional dependencies: ``psycopg2`` or
``psycopg2-binary``.

Example:
    python langgraph_scripts/export_shift_summaries.py --limit 5
"""

import argparse
import csv
import html
import os
import re

import psycopg2

DEFAULT_DB = "OHDSI-CDM_V54"
DEFAULT_SCHEMA = "cdm_synthea"
DEFAULT_SOURCE = "Shift Summary"
DEFAULT_OUT_DIR = os.path.join("data", "shift_summaries")

HTML_TAG_RE = re.compile(r"<[^>]+>")
MANIFEST_FIELDS = [
    "note_id",
    "person_id",
    "visit_occurrence_id",
    "note_date",
    "note_datetime",
    "note_title",
    "filename",
    "char_length",
    "has_html",
]


def connect(args):
    """Open a psycopg2 connection using CLI flags with PG* env fallbacks.

    Args:
        args: Parsed argparse namespace.
    Returns:
        A psycopg2 connection.
    """
    return psycopg2.connect(
        host=args.host or os.environ.get("PGHOST", "127.0.0.1"),
        port=int(args.port or os.environ.get("PGPORT", "54320")),
        user=args.user or os.environ.get("PGUSER", "postgres"),
        password=args.password or os.environ.get("PGPASSWORD"),
        dbname=args.dbname or os.environ.get("PGDATABASE", DEFAULT_DB),
        connect_timeout=8,
    )


def fetch_notes(conn, schema, source, limit=None):
    """Fetch note rows for one source value, ordered by note_id.

    Args:
        conn: Open psycopg2 connection.
        schema: Schema containing the note table (e.g. ``cdm_synthea``).
        source: Value of ``note_source_value`` to select.
        limit: Optional maximum number of rows (for smoke runs).
    Returns:
        List of dicts keyed by the selected column names.
    """
    sql = (
        "select note_id, person_id, visit_occurrence_id, note_date, "
        "note_datetime, note_title, note_text "
        f"from {schema}.note where note_source_value = %s order by note_id"
    )
    params = (source,)
    if limit:
        sql += " limit %s"
        params = (source, limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def clean_text(text, strip_html=False):
    """Normalize line endings and optionally remove HTML tags/entities.

    Args:
        text: Raw ``note_text`` value (may be None).
        strip_html: When True, replace tags with spaces and unescape entities.
    Returns:
        The cleaned text (empty string for null notes).
    """
    if text is None:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if strip_html:
        text = html.unescape(HTML_TAG_RE.sub(" ", text))
    return text.strip()


def export_notes(rows, out_dir, strip_html=False):
    """Write one .txt per note and build the manifest rows.

    Args:
        rows: Dicts returned by ``fetch_notes``.
        out_dir: Destination directory for the .txt files.
        strip_html: Passed to ``clean_text``.
    Returns:
        Tuple ``(manifest_rows, skipped_count)``; rows with empty text are
        skipped and counted.
    """
    os.makedirs(out_dir, exist_ok=True)
    manifest = []
    skipped = 0
    for row in rows:
        raw = row["note_text"]
        text = clean_text(raw, strip_html=strip_html)
        if not text:
            skipped += 1
            continue
        filename = f"note_{row['note_id']}.txt"
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        manifest.append(
            {
                "note_id": row["note_id"],
                "person_id": row["person_id"],
                "visit_occurrence_id": row["visit_occurrence_id"],
                "note_date": row["note_date"].isoformat() if row["note_date"] else "",
                "note_datetime": (
                    row["note_datetime"].isoformat(sep=" ") if row["note_datetime"] else ""
                ),
                "note_title": row["note_title"] or "",
                "filename": filename,
                "char_length": len(text),
                "has_html": int(bool(HTML_TAG_RE.search(raw or ""))),
            }
        )
    return manifest, skipped


def write_manifest(manifest, out_dir):
    """Write ``manifest.csv`` into ``out_dir``.

    Args:
        manifest: Rows produced by ``export_notes``.
        out_dir: Directory that also holds the exported .txt files.
    Returns:
        Path to the written manifest.
    """
    path = os.path.join(out_dir, "manifest.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(manifest)
    return path


def main(argv=None):
    """Parse arguments, export the notes, and print a one-line summary.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).
    Returns:
        Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(
        description="Export OMOP shift-summary notes to .txt files plus a manifest."
    )
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="Max notes to export")
    parser.add_argument("--dbname", default=None)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument(
        "--strip-html",
        action="store_true",
        help="Remove HTML tags/entities instead of exporting text verbatim",
    )
    args = parser.parse_args(argv)

    conn = connect(args)
    try:
        rows = fetch_notes(conn, args.schema, args.source, limit=args.limit)
    finally:
        conn.close()

    manifest, skipped = export_notes(rows, args.out_dir, strip_html=args.strip_html)
    manifest_path = write_manifest(manifest, args.out_dir)
    print(
        f"Exported {len(manifest)} {args.source!r} notes to {args.out_dir} "
        f"(skipped {skipped} empty); manifest: {manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())