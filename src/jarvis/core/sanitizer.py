"""Turn text sanitizer — strip raw tool-use noise from ingested transcripts.

Background (Phase P5):
  Claude Code / agent transcripts include lines like:
    [tool_use: Read]
    [Bash] ls -la
    [TOOL_RESULT] ...
    [결과] ...
  When stored verbatim in `turns.text`, these dominate FTS / pgvector results
  and pollute episode summaries. They are not what the user said — they are
  tooling artifacts.

  This module strips those leading-bracket prefixes line by line. The
  removed lines are returned separately so the caller can preserve them in
  a `metadata.tool_payload` sidecar (lossless: nothing is dropped, just
  re-shelved).

Modes:
  - "cleaned" (default) : strip matching lines from `text`, return them
                          separately. Used by the ingest pipeline.
  - "raw"               : return text unchanged, removed = []. Used when
                          a caller explicitly wants verbatim audit.
"""

from __future__ import annotations

import re

# Patterns are line-anchored — we match against the start of each line after
# stripping leading whitespace, so " [Bash] ls" also matches.
# Each pattern matches a *line prefix* that indicates a tool-use artifact.
TOOL_PATTERNS: list[str] = [
    r"^\[tool_use:",
    r"^\[TOOL_USE",
    r"^\[tool_result\]",
    r"^\[TOOL_RESULT\]",
    r"^\[결과\]",
    r"^\[Read\]",
    r"^\[Bash\]",
    r"^\[Write\]",
    r"^\[Edit\]",
    r"^\[Grep\]",
    r"^\[Glob\]",
    r"^\[TodoWrite\]",
    r"^\[WebFetch\]",
    r"^\[WebSearch\]",
]

# Line-number leak patterns — `cat -n` style output from Read tool results.
# cleanup_preserve.py inlines the first 600 chars of tool_result into a single
# `[결과] <body>` chunk; the `[결과]` prefix lives only on line 1, so lines 2+
# (` 358          · …`) slip through. Each pattern is anchored to the *full*
# line so we do not accidentally eat real prose that happens to start with a
# digit followed by content (e.g. "1. 첫 번째 항목").
LINE_NUM_PATTERNS: list[str] = [
    r"^\s*\d+→",          # `123→content`  (Read tool arrow form)
    r"^\s*\d+\s{2,}\S.*$", # `  358          · …` (cat -n: digits + 2+ spaces + body)
    r"^\s*\d+\s*\|.*$",   # `123 | content`  (alt cat -n bar form)
    r"^\s*\d+\s*$",       # `  358`  (lone integer line — fragment of cat -n)
]

_COMPILED_TOOL_RE = re.compile("|".join(TOOL_PATTERNS + LINE_NUM_PATTERNS))


def _is_tool_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    return _COMPILED_TOOL_RE.match(stripped) is not None


# ── Wrapper strip (2026-06-03 룰북 기반) ─────────────────────────────────
# Harness가 turn 본문에 끼워넣는 XML/HTML 래퍼들. 본문은 wrapper 안이 아니라
# 그 뒤(또는 안의 <result>)에 있음. 래퍼만 제거하면 실사용 텍스트가 노출됨.

_WRAPPER_STRIP_PATTERNS = [
    # <ide_opened_file>...</ide_opened_file>  — IDE가 열린 파일 알림. 뒤에 실제 발화가 붙음.
    re.compile(r"<ide_opened_file>.*?</ide_opened_file>\s*", re.DOTALL),
    # <ide_selection>...</ide_selection>
    re.compile(r"<ide_selection>.*?</ide_selection>\s*", re.DOTALL),
    # <local-command-caveat>...</local-command-caveat>
    re.compile(r"<local-command-caveat>.*?</local-command-caveat>\s*", re.DOTALL),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>\s*", re.DOTALL),
    re.compile(r"<command-name>.*?</command-name>\s*", re.DOTALL),
    re.compile(r"<command-message>.*?</command-message>\s*", re.DOTALL),
    re.compile(r"<command-args>.*?</command-args>\s*", re.DOTALL),
    # <system-reminder>...</system-reminder> — 사용자 발화 아님, harness 알림
    re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.DOTALL),
]

# <task-notification>...<result>본문</result>...</task-notification>
# 래퍼는 제거하고 <result> 본문만 보존.
_TASK_NOTIFICATION_RE = re.compile(
    r"<task-notification>.*?(?:<result>(.*?)</result>)?.*?</task-notification>\s*",
    re.DOTALL,
)

# 단독 noise 문구 — strip 후 본문이 이 패턴 중 하나면 빈 문자열 반환.
_SOLO_NOISE_RES = [
    re.compile(r"^Unknown skill:.*$", re.DOTALL),
    re.compile(
        r"^Todos have been modified successfully\. Ensure that you continue to use the todo list.*$",
        re.DOTALL,
    ),
    re.compile(
        r"^\[?결과\]?\s*Command running in background with ID:.*$",
        re.DOTALL,
    ),
    # plan mode boilerplate
    re.compile(
        r"^In plan mode, you should:.*?ExitPlanMode.*?approval\.\s*$",
        re.DOTALL,
    ),
    re.compile(r"^Your plan has been saved to:\s*\S+\s*$", re.DOTALL),
]


def strip_wrappers(text: str) -> str:
    """Strip harness wrapper tags. Preserves <result> body inside <task-notification>.

    Returns the wrapper-stripped text. May be empty if the entire text was wrappers.
    """
    if not text:
        return text
    out = text
    # task-notification: keep <result> body, drop the rest.
    def _tn_sub(m: re.Match) -> str:
        body = m.group(1)
        return (body + "\n") if body else ""
    out = _TASK_NOTIFICATION_RE.sub(_tn_sub, out)
    for pat in _WRAPPER_STRIP_PATTERNS:
        out = pat.sub("", out)
    return out


def is_solo_noise(text: str) -> bool:
    """True if `text` (after strip) is one of the well-known single-line noise patterns."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return any(p.match(stripped) for p in _SOLO_NOISE_RES)


def sanitize_turn_text(text: str, mode: str = "cleaned") -> tuple[str, list[str]]:
    """Strip tool-use prefix lines from `text`.

    Args:
        text: raw turn text (any role).
        mode: "cleaned" to strip, "raw" to pass through unchanged.

    Returns:
        (sanitized_text, removed_lines).
          - sanitized_text: text with tool lines removed and remaining lines
            rejoined with the original newline.
          - removed_lines: the lines (verbatim, no trailing newline) that
            were stripped. Order preserved.

        In "raw" mode: (text, []).

    Lossless contract: the original text can be reconstructed by interleaving
    sanitized + removed (order is not preserved across the merge, but no
    character data is lost between the two outputs).
    """
    if not text:
        return text, []
    if mode == "raw":
        return text, []
    if mode != "cleaned":
        raise ValueError(f"sanitize_turn_text: unknown mode {mode!r}")

    # Pass 1: strip XML/HTML wrappers (ide_opened_file, system-reminder, command-*, task-notification).
    pre = strip_wrappers(text)

    # Pass 2: solo-noise drop — if entire body is one well-known noise line, return empty.
    if is_solo_noise(pre):
        return "", [pre.rstrip("\n")]

    kept: list[str] = []
    removed: list[str] = []
    for line in pre.splitlines():
        if _is_tool_line(line):
            removed.append(line)
        else:
            kept.append(line)

    # If the input ended with a newline, preserve that trailing newline.
    sanitized = "\n".join(kept)
    if text.endswith("\n") and sanitized and not sanitized.endswith("\n"):
        sanitized += "\n"
    return sanitized, removed


if __name__ == "__main__":
    # Self-test: cat -n leak — only `normal` should survive.
    sample = "1   foo\n2     bar\nnormal\n 358          · 한국어\n123→x\n  500  |  baz\n  77\n진짜 본문"
    cleaned, removed = sanitize_turn_text(sample)
    assert cleaned.splitlines() == ["normal", "진짜 본문"], cleaned.splitlines()
    assert len(removed) == 6, removed
    # Real prose starting with digits + dot must NOT be stripped.
    prose = "1. 첫 번째 항목\n2. 두 번째"
    cleaned2, removed2 = sanitize_turn_text(prose)
    assert cleaned2 == prose, cleaned2
    assert removed2 == [], removed2

    # Wrapper strip: ide_opened_file should be removed, payload preserved.
    wrapped = "<ide_opened_file>The user opened file foo.py</ide_opened_file>\n자비스 mcp 됨?"
    c3, _ = sanitize_turn_text(wrapped)
    assert c3.strip() == "자비스 mcp 됨?", c3

    # system-reminder strip
    sr = "<system-reminder>Today is 2026-06-03</system-reminder>\n작업 시작"
    c4, _ = sanitize_turn_text(sr)
    assert c4.strip() == "작업 시작", c4

    # task-notification: keep <result>
    tn = "<task-notification><task-id>abc</task-id><status>completed</status><result>완료 보고 본문</result></task-notification>"
    c5, _ = sanitize_turn_text(tn)
    assert c5.strip() == "완료 보고 본문", c5

    # Solo noise drop
    noise = "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. Please proceed with the current tasks if applicable"
    c6, r6 = sanitize_turn_text(noise)
    assert c6 == "", c6
    assert len(r6) == 1, r6

    # Mixed wrapper + prose
    mixed = "<ide_opened_file>x</ide_opened_file>\n<system-reminder>y</system-reminder>\n실제 발화"
    c7, _ = sanitize_turn_text(mixed)
    assert c7.strip() == "실제 발화", c7

    print("sanitizer self-test OK")
