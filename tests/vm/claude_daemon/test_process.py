import asyncio

import pytest

from vm.claude_daemon.process import (
    ClaudeProcess,
    _SUBSTANTIAL_SPEAK_CMD_LEN,
    _max_speak_cmd_len_in,
)

FAKE_CLAUDE = '''#!/usr/bin/env python3
import json, sys
sid = "fake-sid-001"
def emit(o): print(json.dumps(o), flush=True)
emit({"type":"system","subtype":"init","session_id":sid})
emit({"type":"rate_limit_event",
      "rate_limit_info":{"status":"allowed","rateLimitType":"five_hour"}})
for line in sys.stdin:
    obj = json.loads(line)
    content = obj["message"]["content"]
    emit({"type":"assistant","message":{"role":"assistant","content":"...thinking..."}})
    emit({"type":"result","result":"answered:"+content,
          "duration_ms":42,"session_id":sid})
'''


@pytest.fixture
def fake_claude(tmp_path):
    p = tmp_path / "fake-claude"
    p.write_text(FAKE_CLAUDE)
    p.chmod(0o755)
    return p


@pytest.mark.asyncio
async def test_process_round_trip(fake_claude, tmp_path):
    persisted = []
    proc = ClaudeProcess(workdir=tmp_path, binary=str(fake_claude))
    await proc.ensure_alive(resume_id=None)
    out1 = await proc.ask("hello", persist_session_id=persisted.append)
    out2 = await proc.ask("again", persist_session_id=persisted.append)
    assert out1.text == "answered:hello"
    assert out2.text == "answered:again"
    # Fake claude never invokes the `speak` tool, so no substantial speak
    # was observed → fallback is needed.
    assert out1.needs_fallback_speak is True
    assert out2.needs_fallback_speak is True
    assert proc.session_id == "fake-sid-001"
    assert persisted == ["fake-sid-001"]  # only persisted on first init
    assert proc.last_rate_limit == {"status": "allowed", "rateLimitType": "five_hour"}
    await proc.stop()


def _bash_speak_event(command: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "id": "x",
         "input": {"command": command}}]}}


def _text_event(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": text}]}}


def _bash_other_event(command: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "id": "y",
         "input": {"command": command}}]}}


def _max_over(events: list[dict]) -> int:
    m = 0
    for e in events:
        m = _max_speak_cmd_len_in(e, m)
    return m


@pytest.mark.parametrize("cmd, is_speak", [
    ('speak "hello"', True),
    ("speak hello", True),
    ("  speak 'hi there'", True),
    ("speak", True),
    ("speakers --list", False),
    ("echo speak", False),
    ("/usr/bin/speak hi", False),
    ("ls", False),
])
def test_speak_command_detection(cmd, is_speak):
    """Only `speak ...` at the start of a Bash command counts. Unrelated
    commands contribute 0 to max_speak_cmd_len; speaks contribute len(cmd)."""
    n = _max_speak_cmd_len_in(_bash_speak_event(cmd), 0)
    if is_speak:
        assert n == len(cmd)
    else:
        assert n == 0


def test_failure_mode_only_ack_then_text():
    """Production failure pattern: ack-speak at turn start, a middle round
    of tool use, then the answer in plain text with no closing speak. The
    ack is short, so max stays below threshold → fallback fires."""
    events = [
        _bash_speak_event('speak "Looking that up."'),
        _text_event("Let me check..."),
        _bash_other_event("find / -name slack"),
        _text_event("The Slack MCP server lets Claude read your Slack..."),
    ]
    assert _max_over(events) < _SUBSTANTIAL_SPEAK_CMD_LEN


def test_happy_path_substantial_closing_speak():
    """Ack-speak, some work, then a substantial closing speak. Max ≥
    threshold → no fallback."""
    answer = 'speak "Found it — the answer is that the MCP server is X."'
    assert len(answer) >= _SUBSTANTIAL_SPEAK_CMD_LEN  # sanity
    events = [
        _bash_speak_event('speak "On it."'),
        _text_event("Let me check..."),
        _bash_other_event("grep -r foo /etc"),
        _bash_speak_event(answer),
    ]
    assert _max_over(events) >= _SUBSTANTIAL_SPEAK_CMD_LEN


def test_doubled_emission_substantial_speak_then_text():
    """Observed regression: Claude correctly spoke the answer with a
    substantial speak, then also wrote the same content as final text. The
    substantial speak should suppress the fallback — otherwise the daemon
    re-speaks the text and the user hears the answer twice."""
    answer = (
        'speak "Found it—Slack MCP is an official integration from Slack '
        'that lets Claude search messages, retrieve threads, send messages, '
        'and manage canvases in your workspace."'
    )
    events = [
        _bash_speak_event('speak "Searching now."'),
        _bash_other_event("WebSearch ..."),
        _bash_speak_event(answer),
        _text_event("**The Slack MCP Server** is an official integration..."),
    ]
    assert _max_over(events) >= _SUBSTANTIAL_SPEAK_CMD_LEN


def test_multiple_short_acks_dont_add_up_to_substantial():
    """If Claude only ever did short acks (no real answer-speak), even
    several of them shouldn't be mistaken for an answer. We track max, not
    sum, so step-by-step progress speaks don't suppress the fallback."""
    events = [
        _bash_speak_event('speak "Step 1 done."'),
        _bash_speak_event('speak "Step 2 done."'),
        _bash_speak_event('speak "Step 3 done."'),
        _text_event("Detailed report ..."),
    ]
    assert _max_over(events) < _SUBSTANTIAL_SPEAK_CMD_LEN


def test_no_speak_at_all():
    """Pure text-only failure mode — no speak observed at all."""
    events = [_text_event("Here's the answer in text only.")]
    assert _max_over(events) == 0


def test_non_assistant_events_are_passthrough():
    """system, user/tool_result, rate_limit, and result events must not
    affect max_speak_cmd_len."""
    for obj in [
        {"type": "system", "subtype": "init"},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x", "is_error": False,
             "content": "ok"}]}},
        {"type": "rate_limit_event", "rate_limit_info": {}},
        {"type": "result", "result": "done"},
    ]:
        assert _max_speak_cmd_len_in(obj, 42) == 42
        assert _max_speak_cmd_len_in(obj, 0) == 0
