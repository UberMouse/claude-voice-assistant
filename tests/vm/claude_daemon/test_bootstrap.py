from vm.claude_daemon.cli import _bootstrap_workspace


def test_bootstrap_copies_template(tmp_path):
    workdir = tmp_path / "vw"
    workdir.mkdir()
    _bootstrap_workspace(workdir)
    assert (workdir / "CLAUDE.md").exists()
    assert (workdir / ".claude" / "settings.json").exists()
    assert (workdir / "notes" / ".gitkeep").exists()
    body = (workdir / "CLAUDE.md").read_text()
    assert "speak" in body
    assert "voice assistant" in body.lower()


def test_bootstrap_idempotent(tmp_path):
    workdir = tmp_path / "vw"
    workdir.mkdir()
    _bootstrap_workspace(workdir)
    # Tag the file; second run must not overwrite
    (workdir / "CLAUDE.md").write_text("CUSTOMIZED")
    _bootstrap_workspace(workdir)
    assert (workdir / "CLAUDE.md").read_text() == "CUSTOMIZED"
