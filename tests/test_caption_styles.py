"""Every caption style builds valid ASS, the animated ones use their effect tags, and the UI knows them all."""
import pytest

from clipper.captions import STYLES, build_ass
from clipper.models import Segment
from clipper.presets import CAPTION_STYLES

UNITS = [Segment(0.0, 3.2, "This is the secret nobody tells you.", words=[
    (0.0, 0.4, "This"), (0.45, 0.8, "is"), (0.85, 1.2, "the"), (1.25, 1.9, "secret"),
    (1.95, 2.2, "nobody"), (2.25, 2.6, "tells"), (2.65, 3.2, "you.")])]


def _events(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


@pytest.mark.parametrize("style", sorted(STYLES))
def test_every_style_builds_dialogue(style):
    ass = build_ass(UNITS, 0.0, 3.2, width=1080, height=1920, style=style)
    assert "[Events]" in ass
    assert len(_events(ass)) >= 1


def test_ui_and_engine_agree_on_styles():
    assert set(STYLES) == set(CAPTION_STYLES)


def test_karaoke_uses_fill_tags():
    ass = build_ass(UNITS, 0.0, 3.2, width=1080, height=1920, style="karaoke")
    assert "\\kf" in "\n".join(_events(ass))


def test_pop_animates_each_word():
    ass = build_ass(UNITS, 0.0, 3.2, width=1080, height=1920, style="pop")
    text = "\n".join(_events(ass))
    assert text.count("\\t(") >= 3 * len(UNITS[0].words) - 3


def test_typewriter_builds_word_by_word():
    ass = build_ass(UNITS, 0.0, 3.2, width=1080, height=1920, style="typewriter")
    events = _events(ass)
    assert len(events) == len(UNITS[0].words)      # one event per word
    assert events[0].endswith("This")
    assert events[2].endswith("This is the")       # phrases hold up to three words


def test_caps_forces_uppercase():
    ass = build_ass(UNITS, 0.0, 3.2, width=1080, height=1920, style="caps")
    text = "\n".join(_events(ass))
    assert "THIS IS THE" in text and "SECRET" in text
    assert "This" not in text


def test_fade_and_bounce_have_effects():
    assert "\\fad(" in "\n".join(_events(build_ass(UNITS, 0, 3.2, width=1080, height=1920, style="fade")))
    assert "\\t(0,140" in "\n".join(_events(build_ass(UNITS, 0, 3.2, width=1080, height=1920, style="bounce")))
