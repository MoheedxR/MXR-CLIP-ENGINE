"""Custom vs default output settings: resolution, frame rate and aspect ratio."""
import shutil
import subprocess
from pathlib import Path

import pytest

from clipper import presets
from clipper.media import cut_clip, probe_video
from clipper.pipeline import Params, _quality_warnings


def params(**kw) -> Params:
    return Params(**kw)


@pytest.mark.parametrize("quality", list(presets.QUALITY))
def test_default_settings_reproduce_the_quality_presets(quality):
    base = presets.QUALITY[quality]
    spec = presets.resolve_output(params(quality=quality))
    assert (spec["width"], spec["height"], spec["fps"]) == (base["width"], base["height"], base["fps"])
    assert (spec["crf"], spec["preset"], spec["mp3"]) == (base["crf"], base["preset"], base["mp3"])
    assert spec["max_source"] == base["max_source"]


def test_default_aspect_is_9_16():
    spec = presets.resolve_output(params(quality="quick"))
    assert spec["aspect"] == (9, 16)
    assert spec["description"] == "1080×1920 · 30 fps · 9:16"


def test_custom_resolution_sets_short_side():
    spec = presets.resolve_output(params(quality="quick", quality_mode="custom", resolution=720))
    assert (spec["width"], spec["height"]) == (720, 1280)  # 9:16 by default
    assert spec["mp3"] is True
    assert spec["crf"] == 23 and spec["preset"] == "veryfast"


def test_custom_4k_uses_slow_high_quality_encode():
    spec = presets.resolve_output(params(quality="quick", quality_mode="custom", resolution=2160))
    assert (spec["width"], spec["height"]) == (2160, 3840)
    assert (spec["crf"], spec["preset"], spec["max_source"]) == (18, "medium", 2160)


def test_custom_fps_overrides_the_preset_frame_rate():
    spec = presets.resolve_output(params(quality="quick", fps_mode="custom", fps=24))
    assert spec["fps"] == 24
    assert (spec["width"], spec["height"]) == (1080, 1920)


def test_custom_fps_above_30_uses_fast_encode():
    spec = presets.resolve_output(params(quality="quick", fps_mode="custom", fps=60))
    assert (spec["crf"], spec["preset"]) == (20, "fast")


@pytest.mark.parametrize("aspect, expected", [
    ("4:5", (1080, 1350)),
    ("1:1", (1080, 1080)),
    ("16:9", (1920, 1080)),
    ("9:16", (1080, 1920)),
    ("4/5", (1080, 1350)),
])
def test_custom_aspect_keeps_the_short_side(aspect, expected):
    spec = presets.resolve_output(params(quality="quick", aspect_mode="custom", aspect=aspect))
    assert (spec["width"], spec["height"]) == expected
    assert spec["width"] % 2 == 0 and spec["height"] % 2 == 0


def test_custom_settings_combine():
    spec = presets.resolve_output(params(
        quality="quick", quality_mode="custom", resolution=1440,
        fps_mode="custom", fps=50, aspect_mode="custom", aspect="1:1"))
    assert (spec["width"], spec["height"], spec["fps"]) == (1440, 1440, 50)


@pytest.mark.parametrize("bad", ["", "abc", "9-16", "0:5", "10:1", "1:10", "3:1:1"])
def test_bad_aspect_text_is_rejected(bad):
    with pytest.raises(ValueError):
        presets.parse_aspect(bad)


def test_validate_rejects_unknown_custom_values():
    with pytest.raises(ValueError):
        presets.validate(params(quality_mode="custom", resolution=999))
    with pytest.raises(ValueError):
        presets.validate(params(fps_mode="custom", fps=33))
    with pytest.raises(ValueError):
        presets.validate(params(aspect_mode="custom", aspect="10:1"))
    with pytest.raises(ValueError):
        presets.validate(params(fps_mode="sometimes"))


def test_validate_accepts_valid_custom_values():
    presets.validate(params(quality_mode="custom", resolution=720,
                            fps_mode="custom", fps=24,
                            aspect_mode="custom", aspect="4:5"))


def test_warnings_use_the_custom_output_size():
    spec = presets.resolve_output(params(quality="quick", quality_mode="custom", resolution=2160))
    src = {"width": 1920, "height": 1080, "fps": 30}
    msgs = _quality_warnings(src, spec)
    assert any("4K" in m for m in msgs)
    assert any("upscaled" in m for m in msgs)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg is not installed")
def test_render_matches_custom_size_and_fps(tmp_path: Path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=4:size=1920x1080:rate=30", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    cases = [
        (presets.resolve_output(params(quality="quick", quality_mode="custom", resolution=720,
                                       fps_mode="custom", fps=24, aspect_mode="custom", aspect="1:1"))),
        (presets.resolve_output(params(quality="quick", aspect_mode="custom", aspect="4:5"))),
    ]
    for i, spec in enumerate(cases):
        out = tmp_path / f"out{i}.mp4"
        cut_clip(src, out, 0.5, 3.0, width=spec["width"], height=spec["height"], fps=spec["fps"],
                 crf=spec["crf"], preset=spec["preset"], ratio=spec["aspect"])
        info = probe_video(out)
        assert (info["width"], info["height"]) == (spec["width"], spec["height"])
        assert abs(info["fps"] - spec["fps"]) < 0.5
