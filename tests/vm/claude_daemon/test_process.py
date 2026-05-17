import asyncio

import pytest

from vm.claude_daemon.process import ClaudeProcess

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
    assert out1 == "answered:hello"
    assert out2 == "answered:again"
    assert proc.session_id == "fake-sid-001"
    assert persisted == ["fake-sid-001"]  # only persisted on first init
    assert proc.last_rate_limit == {"status": "allowed", "rateLimitType": "five_hour"}
    await proc.stop()
