"""Phase P5: sanitizer + quality gate unit tests."""

from __future__ import annotations

from jarvis.core.sanitizer import sanitize_turn_text
from jarvis.core.summary_quality_gate import is_low_quality_summary


# ── sanitize_turn_text ───────────────────────────────────────────────────────


def test_sanitize_strips_tool_use_prefix():
    txt = "hello\n[tool_use: Read] /etc/hosts\nworld"
    cleaned, removed = sanitize_turn_text(txt)
    assert cleaned == "hello\nworld"
    assert removed == ["[tool_use: Read] /etc/hosts"]


def test_sanitize_strips_all_bracket_tool_prefixes():
    txt = (
        "user said\n"
        "[Read] file.py\n"
        "[Bash] ls\n"
        "[Write] x.txt\n"
        "[Edit] x.txt\n"
        "[Grep] foo\n"
        "[Glob] *.py\n"
        "[TodoWrite] todo\n"
        "[WebFetch] http://x\n"
        "[WebSearch] q\n"
        "[tool_result] ok\n"
        "[TOOL_RESULT] ok\n"
        "[TOOL_USE] thing\n"
        "[결과] 응\n"
        "end"
    )
    cleaned, removed = sanitize_turn_text(txt)
    assert cleaned == "user said\nend"
    assert len(removed) == 13


def test_sanitize_keeps_text_with_inline_brackets():
    # Brackets in the middle of a line — NOT a tool prefix — must be kept.
    txt = "I used [Bash] inside a sentence."
    cleaned, removed = sanitize_turn_text(txt)
    assert cleaned == "I used [Bash] inside a sentence."
    assert removed == []


def test_sanitize_handles_leading_whitespace_tool_lines():
    txt = "   [Bash] ls\nkeep this"
    cleaned, removed = sanitize_turn_text(txt)
    assert cleaned == "keep this"
    assert removed == ["   [Bash] ls"]


def test_sanitize_raw_mode_passthrough():
    txt = "[Bash] ls\nhello"
    cleaned, removed = sanitize_turn_text(txt, mode="raw")
    assert cleaned == "[Bash] ls\nhello"
    assert removed == []


def test_sanitize_preserves_trailing_newline():
    txt = "hello\n[Bash] ls\n"
    cleaned, _ = sanitize_turn_text(txt)
    assert cleaned == "hello\n"


def test_sanitize_empty_text():
    cleaned, removed = sanitize_turn_text("")
    assert cleaned == ""
    assert removed == []


# ── is_low_quality_summary ───────────────────────────────────────────────────


def test_quality_gate_flags_none_empty_whitespace():
    assert is_low_quality_summary(None) is True
    assert is_low_quality_summary("") is True
    assert is_low_quality_summary("   \n\t  ") is True


def test_quality_gate_flags_short_summaries():
    assert is_low_quality_summary("too short") is True
    assert is_low_quality_summary("a" * 49) is True


def test_quality_gate_accepts_long_summaries():
    s = "2026-05-29 자비스 Phase P5 작업. turn dedupe + tool sanitize 도입."
    assert is_low_quality_summary(s) is False


def test_quality_gate_flags_jsonl_agent_pattern():
    assert is_low_quality_summary("jsonl-agent-7c") is True
    assert is_low_quality_summary("jsonl-agent-ab ") is True


def test_quality_gate_flags_bare_integer():
    assert is_low_quality_summary("18") is True
    assert is_low_quality_summary("42 ") is True
    assert is_low_quality_summary("  99\n") is True


def test_quality_gate_accepts_numeric_inside_real_summary():
    # Bare-integer regex must not swallow real summaries that happen to
    # start with a number. Length must clear the 50-char floor.
    s = (
        "18개 episode 처리 완료 — 자비스 Phase P5 정합성 검증 OK. "
        "turn dedupe 5,793건 제거됨."
    )
    assert len(s) >= 50
    assert is_low_quality_summary(s) is False
