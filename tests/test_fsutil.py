"""atomic_write_text must survive a briefly locked target (Windows 'Access is denied')."""
import os

from clipper import fsutil


def test_writes_text_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "job.json"
    fsutil.atomic_write_text(target, '{"ok": true}')
    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert not (tmp_path / "job.json.tmp").exists()


def test_retries_when_target_is_locked(tmp_path, monkeypatch):
    target = tmp_path / "job.json"
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 3:          # simulate antivirus holding the file
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(fsutil.os, "replace", flaky_replace)
    monkeypatch.setattr(fsutil, "_DELAY", 0)
    fsutil.atomic_write_text(target, "hello")
    assert calls["n"] == 4
    assert target.read_text(encoding="utf-8") == "hello"


def test_falls_back_to_direct_write_if_lock_never_clears(tmp_path, monkeypatch):
    target = tmp_path / "job.json"

    def always_locked(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(fsutil.os, "replace", always_locked)
    monkeypatch.setattr(fsutil, "_DELAY", 0)
    fsutil.atomic_write_text(target, "saved anyway")
    assert target.read_text(encoding="utf-8") == "saved anyway"
