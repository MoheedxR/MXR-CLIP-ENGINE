"""Download speed limit: validated, stored, and passed to yt-dlp."""
import sys
import types

import pytest

from clipper import download as dl  # noqa: F401
from clipper.pipeline import Params
from clipper.presets import DOWNLOAD_LIMITS, validate


def test_presets_have_expected_rates():
    assert DOWNLOAD_LIMITS["full"]["bytes_per_sec"] is None
    assert DOWNLOAD_LIMITS["medium"]["bytes_per_sec"] == 4_000_000
    assert DOWNLOAD_LIMITS["gentle"]["bytes_per_sec"] == 1_000_000


def test_default_is_full_speed():
    assert Params().download_limit == "full"


def test_validate_accepts_known_and_rejects_unknown():
    for key in DOWNLOAD_LIMITS:
        validate(Params(download_limit=key))
    with pytest.raises(ValueError):
        validate(Params(download_limit="turbo"))


class _FakeYDL:
    seen: list = []

    def __init__(self, opts):
        _FakeYDL.seen.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        return {"title": "t"}


def test_audio_download_passes_ratelimit(tmp_path, monkeypatch):
    _FakeYDL.seen = []
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_FakeYDL))
    try:
        dl.download_audio("https://example.com/v", tmp_path, rate_limit=1_000_000)
    except Exception:
        pass  # no file is written by the fake; only the options matter here
    assert _FakeYDL.seen and _FakeYDL.seen[0].get("ratelimit") == 1_000_000


def test_no_ratelimit_key_when_full_speed(tmp_path, monkeypatch):
    _FakeYDL.seen = []
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_FakeYDL))
    try:
        dl.download_audio("https://example.com/v", tmp_path, rate_limit=None)
    except Exception:
        pass
    assert _FakeYDL.seen and "ratelimit" not in _FakeYDL.seen[0]
