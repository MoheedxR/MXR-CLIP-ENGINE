
import pytest

from clipper.jobs import MAX_LOG_ENTRIES, JobStore
from clipper.pipeline import Params


@pytest.fixture()
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


def test_create_persists_and_lists(store):
    job = store.create(Params(count=3), source_url="https://example.com/v", upload_name=None)
    assert job["status"] == "queued"
    assert store.get(job["id"])["log"][0]["msg"]
    listed = store.list_jobs()
    assert listed[0]["id"] == job["id"]
    assert listed[0]["clip_count"] == 0
    assert (store.job_dir(job["id"]) / "job.json").exists()


def test_log_is_bounded(store):
    job = store.create(Params(), source_url="https://example.com/v", upload_name=None)
    for i in range(MAX_LOG_ENTRIES + 50):
        store._append_log(job["id"], "info", f"line {i}")
    entries = store.get(job["id"])["log"]
    assert len(entries) == MAX_LOG_ENTRIES
    assert entries[-1]["msg"] == f"line {MAX_LOG_ENTRIES + 49}"


def test_clips_are_appended_one_by_one(store):
    job = store.create(Params(), source_url=None, upload_name="talk.mp4")
    store._add_clip(job["id"], {"id": "clip_01", "rank": 1})
    store._add_clip(job["id"], {"id": "clip_02", "rank": 2})
    got = store.get(job["id"])
    assert [c["id"] for c in got["clips"]] == ["clip_01", "clip_02"]
    assert store.list_jobs()[0]["clip_count"] == 2


def test_delete_blocked_while_running(store):
    job = store.create(Params(), source_url=None, upload_name="x.mp4")
    store._update(job["id"], status="running")
    with pytest.raises(RuntimeError):
        store.delete(job["id"])
    store._update(job["id"], status="done")
    assert store.delete(job["id"]) is True
    assert store.get(job["id"]) is None
    assert not store.job_dir(job["id"]).exists()


def test_restart_marks_interrupted_jobs_failed(tmp_path):
    first = JobStore(tmp_path / "jobs")
    job = first.create(Params(), source_url=None, upload_name="x.mp4")
    first._update(job["id"], status="running")
    second = JobStore(tmp_path / "jobs")
    got = second.get(job["id"])
    assert got["status"] == "failed"
    assert "restart" in got["error"].lower()
