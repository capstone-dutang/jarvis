"""Path normalization for ingest_ledger comparison keys.

Purpose:
  The ingest_ledger compares jsonl file paths between two sides:
    - the DB record  (whatever the AI client wrote at ingest time)
    - the on-disk scan (~/.claude/projects/F--brain/*.jsonl)

  On Windows the same file can surface as multiple strings:
    C:/Users/lhhh0/.claude/projects/F--brain/abc.jsonl  (forward slash)
    C:\\Users\\lhhh0\\.claude\\projects\\F--brain\\abc.jsonl  (back slash)
    c:/users/lhhh0/.claude/projects/f--brain/abc.jsonl  (lower case)

  All three should join on equality. normalize_jsonl_path() produces the
  canonical form:
    - absolute (resolved if the file exists; lexically resolved otherwise)
    - forward slashes
    - lower case

  The SQL backfill in migration n4i5d6e7f8g9 uses LOWER(REPLACE(...)) which
  produces an equivalent form for the data we already have. New ingests go
  through this function so the column stays consistent.
"""

from __future__ import annotations

from pathlib import Path


def normalize_jsonl_path(p: str) -> str:
    """Return a canonical lowercase forward-slash form of `p`.

    Empty / None-ish input returns ''.

    Implementation note (P4): jarvis ingest paths arrive from two contexts:
      - Windows host CLI:   "C:\\Users\\lhhh0\\.claude\\projects\\..."
      - Linux docker server: "/host/claude_projects/..."

    A naive Path(p).resolve() inside docker prepends `/app/` to any path
    that looks relative on Linux (e.g., "C:/Users/..."), producing
    "/app/c:/users/...". That breaks equality joins against the SQL
    backfill `LOWER(REPLACE(path,'\\','/'))` form. So we treat any string
    that already looks absolute on either OS (drive letter `X:` or
    leading `/`) as canonical and only lowercase + slash-normalize. Other
    inputs go through Path.resolve() for safety.
    """
    if not p:
        return ""
    s = str(p).replace("\\", "/")
    looks_absolute = (
        s.startswith("/")
        or (len(s) >= 2 and s[1] == ":")  # 'C:' / 'D:' …
    )
    if looks_absolute:
        return s.lower()
    try:
        resolved = str(Path(s).resolve()).replace("\\", "/")
    except (OSError, ValueError):
        resolved = s
    return resolved.lower()


def basename_no_ext(p: str) -> str:
    """Return the file stem (basename without .jsonl extension).

    Used to compare a local jsonl on disk (filename = external_session_id +
    '.jsonl') against the external_session_id column in ingest_ledger.
    """
    if not p:
        return ""
    return Path(p).stem
