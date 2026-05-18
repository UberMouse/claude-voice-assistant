import asyncio

import pytest

from vm.claude_daemon.process import ClaudeProcess, _update_awaiting_speak

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
    # Fake claude emits an assistant message with string content
    # ("...thinking...") and never invokes the `speak` tool, so the turn
    # ends with text awaiting speech → fallback is needed.
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


def _fold(events: list[dict]) -> bool:
    state = False
    for e in events:
        state = _update_awaiting_speak(state, e)
    return state


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
    """Speak detection must match `speak ...` at the start of a Bash command
    but not unrelated commands. Tested via the fold: a single speak event
    after a text event clears the awaiting-speak flag iff the command is a
    real speak invocation."""
    after_text_then_cmd = _fold([_text_event("answer"), _bash_speak_event(cmd)])
    # If cmd is a speak, the flag clears; otherwise the text is still
    # awaiting speech.
    assert after_text_then_cmd is (not is_speak)


def test_failure_mode_ack_then_text_then_no_speak():
    """The exact pattern observed in production: ack-speak at turn start, a
    middle round of tool use, then the answer in plain text with no closing
    speak. Must report True so the fallback fires."""
    events = [
        _bash_speak_event('speak "Looking that up."'),
        _text_event("Let me check..."),
        _bash_other_event("find / -name slack"),
        _text_event("The Slack MCP server lets Claude read your Slack..."),
    ]
    assert _fold(events) is True


def test_happy_path_speak_last():
    """Ack-speak, some work, then a closing speak. No unspoken text → no
    fallback needed."""
    events = [
        _bash_speak_event('speak "On it."'),
        _text_event("Let me check..."),
        _bash_other_event("grep -r foo /etc"),
        _bash_speak_event('speak "Found it — the answer is X."'),
    ]
    assert _fold(events) is False


def test_text_and_speak_in_same_event_block_order():
    """A single assistant message can carry both text and a tool_use block.
    Blocks are walked in order: text sets the flag, then speak in the same
    message clears it."""
    mixed = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Here's the answer:"},
        {"type": "tool_use", "name": "Bash", "id": "z",
         "input": {"command": 'speak "Yes, here it is."'}},
    ]}}
    assert _update_awaiting_speak(False, mixed) is False


def test_empty_text_doesnt_set_flag():
    """Whitespace-only text shouldn't trip the flag — it's not user-facing
    content that needs to be spoken."""
    assert _update_awaiting_speak(False, _text_event("   ")) is False
    assert _update_awaiting_speak(False, _text_event("")) is False


def test_non_assistant_events_are_passthrough():
    """system, user/tool_result, rate_limit, and result events must not
    change the flag."""
    for obj in [
        {"type": "system", "subtype": "init"},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x", "is_error": False,
             "content": "ok"}]}},
        {"type": "rate_limit_event", "rate_limit_info": {}},
        {"type": "result", "result": "done"},
    ]:
        assert _update_awaiting_speak(True, obj) is True
        assert _update_awaiting_speak(False, obj) is False
