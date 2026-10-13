"""Blank or invalid .env values must fall back to defaults instead of crashing startup."""
import importlib
import sys


def _reload_with(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("clipper.config", None)
    return importlib.import_module("clipper.config")


def test_blank_values_use_defaults(monkeypatch):
    cfg = _reload_with(monkeypatch, WHISPER_THREADS="", WHISPER_BEAM_SIZE="", MAX_DOWNLOAD_HEIGHT="")
    assert 1 <= cfg.WHISPER_THREADS <= 16
    assert cfg.WHISPER_BEAM_SIZE == 1
    assert cfg.MAX_DOWNLOAD_HEIGHT == 1080


def test_invalid_values_use_defaults(monkeypatch):
    cfg = _reload_with(monkeypatch, WHISPER_THREADS="many", WHISPER_BEAM_SIZE="x", MAX_DOWNLOAD_HEIGHT="tall")
    assert cfg.WHISPER_BEAM_SIZE == 1
    assert cfg.MAX_DOWNLOAD_HEIGHT == 1080


def test_valid_values_are_used(monkeypatch):
    cfg = _reload_with(monkeypatch, WHISPER_THREADS="3", WHISPER_BEAM_SIZE="5", MAX_DOWNLOAD_HEIGHT="720")
    assert cfg.WHISPER_THREADS == 3
    assert cfg.WHISPER_BEAM_SIZE == 5
    assert cfg.MAX_DOWNLOAD_HEIGHT == 720
