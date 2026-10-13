"""Tests for the v1.1 features: presets, captions, watch-rate labels and collections."""
import pytest

from clipper import presets
from clipper.captions import build_ass, group_phrases
from clipper.collections_store import CollectionError, CollectionStore, NotFound
from clipper.models import Segment
from clipper.pipeline import Params, watch_rate_for
from clipper.models import Candidate


def _units():
    words = [(0.0, 0.4, "Hello"), (0.5, 0.9, "there"), (1.0, 1.4, "friends."),
             (1.6, 2.0, "Today"), (2.1, 2.5, "we"), (2.6, 3.0, "build")]
    return [Segment(0.0, 1.4, "Hello there friends.", words[:3]),
            Segment(1.6, 3.0, "Today we build", words[3:])]


def test_presets_validate_defaults_and_reject_unknown():
    presets.validate(Params())
    with pytest.raises(ValueError):
        presets.validate(Params(platform="myspace"))
    with pytest.raises(ValueError):
        presets.validate(Params(quality="8k"))
    with pytest.raises(ValueError):
        presets.validate(Params(caption_color="magenta"))


@pytest.mark.parametrize("value,label", [(10, "Weak"), (40, "Fair"), (60, "Good"), (90, "Excellent"),
                                         (None, None)])
def test_rating_labels(value, label):
    assert presets.rating_label(value) == label


def test_watch_rate_prefers_ai_then_falls_back_to_local():
    c = Candidate(0, 40, "x", heuristic=44.0)
    assert watch_rate_for(c) == (44.0, "local")
    c.llm = {"watch_rate": 63}
    assert watch_rate_for(c) == (63.0, "ai")


def test_group_phrases_limits_length_and_breaks_on_sentences():
    words = [(i * 0.3, i * 0.3 + 0.25, w) for i, w in enumerate(
        ["one", "two", "three", "four.", "five", "six"])]
    phrases = group_phrases(words, max_words=3)
    assert [len(p) for p in phrases] == [3, 1, 2]


def test_ass_highlight_has_word_events_and_accent_colour():
    ass = build_ass(_units(), 0.0, 3.0, width=1080, height=1920, style="highlight",
                    position="bottom", accent="cyan", bottom_margin=360)
    assert "[Events]" in ass
    dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(dialogue) >= 6          # one event per spoken word
    assert "{\\c&H00EED322&}" in ass    # cyan #22D3EE in ASS BGR order


def test_ass_respects_window_and_rebases_time():
    ass = build_ass(_units(), 1.6, 3.0, width=1080, height=1920, style="classic")
    dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert dialogue and dialogue[0].split(",")[1] == "0:00:00.00"
    assert "Hello" not in ass


def test_ass_uppercase_and_top_position_alignment():
    ass = build_ass(_units(), 0.0, 3.0, width=2160, height=3840, style="box",
                    position="top", uppercase=True)
    assert "HELLO" in ass
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    assert style_line.split(",")[18] == "8"  # top-centre alignment


def test_ass_survives_missing_word_timings():
    units = [Segment(0.0, 4.0, "No word timings here at all")]
    ass = build_ass(units, 0.0, 4.0, width=1080, height=1920)
    assert "Dialogue" in ass


# --- collections -------------------------------------------------------------

SNAP = {"job_id": "j1", "clip_id": "clip_01", "title": "Hook", "video": "j1/clips/clip_01.mp4",
        "start": 1.0, "end": 40.0, "duration": 39.0, "watch_rate": 62, "rating": "Good"}


@pytest.fixture()
def cstore(tmp_path):
    return CollectionStore(tmp_path / "collections.json")


def test_collection_crud_and_persistence(cstore, tmp_path):
    col = cstore.create("Summer launch", status="Draft", label="Promo", label_color="pink")
    cstore.update(col["id"], {"name": "Launch week", "status": "Ready", "notes": "Post Monday"})
    again = CollectionStore(tmp_path / "collections.json")
    got = again.get(col["id"])
    assert got["name"] == "Launch week" and got["status"] == "Ready" and got["notes"] == "Post Monday"
    assert again.list()[0]["clip_count"] == 0


def test_collection_rejects_bad_input(cstore):
    with pytest.raises(CollectionError):
        cstore.create("   ")
    with pytest.raises(CollectionError):
        cstore.create("ok", status="Launched")
    with pytest.raises(CollectionError):
        cstore.create("ok", label_color="magenta")


def test_entries_order_notes_and_duplicates(cstore):
    col = cstore.create("Set")
    a = cstore.add_entry(col["id"], {**SNAP, "clip_id": "clip_01"})
    b = cstore.add_entry(col["id"], {**SNAP, "clip_id": "clip_02", "title": "Second"})
    with pytest.raises(CollectionError):
        cstore.add_entry(col["id"], {**SNAP, "clip_id": "clip_01"})
    cstore.move_entry(col["id"], b["id"], direction="up")
    order = [e["clip_id"] for e in cstore.get(col["id"])["entries"]]
    assert order == ["clip_02", "clip_01"]
    cstore.update_entry(col["id"], a["id"], {"note": "Cut the intro", "status": "In review",
                                             "title": "Renamed hook"})
    entry = next(e for e in cstore.get(col["id"])["entries"] if e["id"] == a["id"])
    assert entry["note"] == "Cut the intro" and entry["status"] == "In review"
    assert entry["title"] == "Renamed hook"
    cstore.move_entry(col["id"], b["id"], to_index=5)  # clamped to the end
    assert [e["clip_id"] for e in cstore.get(col["id"])["entries"]] == ["clip_01", "clip_02"]


def test_deleting_a_project_removes_its_entries(cstore):
    col = cstore.create("Mixed")
    cstore.add_entry(col["id"], {**SNAP, "job_id": "keep", "clip_id": "clip_01"})
    cstore.add_entry(col["id"], {**SNAP, "job_id": "gone", "clip_id": "clip_01"})
    assert cstore.remove_job("gone") == 1
    entries = cstore.get(col["id"])["entries"]
    assert [e["job_id"] for e in entries] == ["keep"]
    assert entries[0]["order"] == 0


def test_missing_ids_raise_not_found(cstore):
    with pytest.raises(NotFound):
        cstore.get("nope")
    col = cstore.create("x")
    with pytest.raises(NotFound):
        cstore.remove_entry(col["id"], "nope")
