from vm.claude_daemon.session import SessionStore


def test_session_read_write(tmp_path):
    store = SessionStore(tmp_path / "sess")
    assert store.read() is None
    store.write("abc-123")
    assert store.read() == "abc-123"


def test_session_corrupt_file_returns_none(tmp_path):
    p = tmp_path / "sess"
    p.write_text("")
    assert SessionStore(p).read() is None
