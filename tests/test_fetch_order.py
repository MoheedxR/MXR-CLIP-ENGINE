"""Audio is fetched first and transcription starts before the full video has finished downloading."""
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from clipper import pipeline
from clipper.media import MediaError
from clipper.models import Segment
from clipper.pipeline import Params, Reporter

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


class Recorder(Reporter):
    def __init__(self):
        self.events = []

    def stage(self, stage, progress, message):
        self.events.append(("stage", stage))

    def log(self, message, level="info"):
        self.events.append(("log", message))

    def planned(self, count):
        pass

    def clip(self, record):
        pass


def _make_media(path: Path, seconds: int = 2) -> Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=navy:s=320x240:d={seconds}", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


def test_transcription_starts_before_full_video_finishes(tmp_path, monkeypatch):
    src_media = _make_media(tmp_path / "media.mp4")
    calls = []

    def fake_audio(url, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "audio_src.mp4"
        shutil.copy(src_media, target)
        calls.append("audio")
        return target, "Fast title"

    def fake_video(url, out_dir, on_progress=None, max_height=None, prefer_fps=False):
        time.sleep(1.0)                      # a slow full download
        target = out_dir / "source.mp4"
        shutil.copy(src_media, target)
        calls.append("video-finished")
        return target, "Fast title"

    def fake_transcribe(audio_path, duration, on_progress=None):
        calls.append("transcribe")
        return [Segment(0.0, 1.0, "Hello there.")]

    monkeypatch.setattr(pipeline, "download_audio", fake_audio)
    monkeypatch.setattr(pipeline, "download", fake_video)
    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)

    job = tmp_path / "job"
    with pytest.raises(MediaError):          # 2 s of speech is too short for a clip; that's fine here
        pipeline.run_pipeline(job, Params(use_llm=False), source_url="https://example.com/v",
                              reporter=Recorder())
    assert calls.index("transcribe") < calls.index("video-finished")
    assert calls[0] == "audio"


def test_falls_back_to_full_download_when_audio_fails(tmp_path, monkeypatch):
    src_media = _make_media(tmp_path / "media.mp4")
    calls = []

    def broken_audio(url, out_dir):
        raise RuntimeError("audio not available")

    def fake_video(url, out_dir, on_progress=None, max_height=None, prefer_fps=False):
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "source.mp4"
        shutil.copy(src_media, target)
        calls.append("video")
        return target, "Full title"

    monkeypatch.setattr(pipeline, "download_audio", broken_audio)
    monkeypatch.setattr(pipeline, "download", fake_video)
    monkeypatch.setattr(pipeline, "transcribe", lambda a, d, on_progress=None: [Segment(0, 1, "Hi.")])

    job = tmp_path / "job"
    with pytest.raises(MediaError):
        pipeline.run_pipeline(job, Params(use_llm=False), source_url="https://example.com/v",
                              reporter=Recorder())
    assert calls == ["video"]
