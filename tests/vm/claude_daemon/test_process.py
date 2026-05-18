import asyncio

import pytest

from vm.claude_daemon.process import ClaudeProcess, _event_invokes_speak

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
    assert out1.speak_seen is False  # fake claude doesn't emit tool_use blocks
    assert out2.speak_seen is False
    assert proc.session_id == "fake-sid-001"
    assert persisted == ["fake-sid-001"]  # only persisted on first init
    assert proc.last_rate_limit == {"status": "allowed", "rateLimitType": "five_hour"}
    await proc.stop()


def _assistant_with_bash(command: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "id": "x",
         "input": {"command": command}}]}}


@pytest.mark.parametrize("cmd, expected", [
    ('speak "hello"', True),
    ("speak hello", True),
    ("  speak 'hi there'", True),  # leading whitespace is fine
    ("speak", True),               # bare invocation (would error, but still a speak call)
    ("speakers --list", False),    # must not match unrelated commands
    ("echo speak", False),         # only matches at start of command
    ("/usr/bin/speak hi", False),  # absolute path not detected — acceptable, no one does this
    ("ls", False),
])
def test_event_invokes_speak_matches_bash_speak(cmd, expected):
    assert _event_invokes_speak(_assistant_with_bash(cmd)) is expected


def test_event_invokes_speak_ignores_other_tools():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "id": "x",
         "input": {"file_path": "/etc/passwd"}}]}}
    assert _event_invokes_speak(obj) is False


def test_event_invokes_speak_ignores_text_only_messages():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "I'll just say it here: hello"}]}}
    assert _event_invokes_speak(obj) is False
