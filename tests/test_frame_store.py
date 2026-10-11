"""Grouping frames into prints, thinning and the limits (executor side)."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.elegoo_spaghetti_detection import frames
from custom_components.elegoo_spaghetti_detection.frames import (
    NEW_PRINT_GAP,
    Limits,
    PrintSummary,
    SaveResult,
    StagedFrame,
    limits_from_options,
    load_summaries,
    save_frame,
    stage_frame,
)

from .conftest import JPEG

ENTRY = "01JTESTENTRY0000000000000A"
T0 = 1_791_000_000.0  # a fixed instant in 2026


class Store:
    """The executor functions with the summaries kept the way FrameStore does."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.prints: dict[str, PrintSummary] = {}

    def save(
        self,
        now: float,
        *,
        job_id: str = "",
        name: str = "",
        score: float = 0.0,
        limits: Limits | None = None,
        period_start: float | None = None,
    ) -> SaveResult:
        staged = StagedFrame(stage_frame(self.root, JPEG), now, len(JPEG))
        result = save_frame(
            self.root,
            dict(self.prints),
            entry_id=ENTRY,
            staged=staged,
            job_id=job_id,
            name=name,
            score=score,
            detections=1 if score else 0,
            period_start=period_start,
            limits=limits or Limits(),
        )
        for print_id in result.removed:
            self.prints.pop(print_id, None)
        self.prints[result.print_id] = result.summary
        return result

    def index(self, print_id: str) -> dict:
        return json.loads((self.root / print_id / "index.json").read_text())


def test_a_frame_is_filed_under_its_job(tmp_path: Path) -> None:
    store = Store(tmp_path)
    result = store.save(T0, job_id="job-1", name="cube.gcode", score=0.1234)
    assert result.print_id == "job-1"
    assert result.frame.endswith("_01234.jpg")
    assert (tmp_path / "job-1" / result.frame).read_bytes() == JPEG
    assert not list(tmp_path.glob("check_*"))
    assert store.index("job-1") == {
        "start": T0,
        "end": T0,
        "entry_id": ENTRY,
        "task": "job-1",
        "name": "cube.gcode",
        "frames": [{"t": T0, "f": result.frame, "s": 0.1234, "n": 1}],
    }
    summary = store.prints["job-1"]
    assert (summary.count, summary.max_score, summary.size) == (1, 0.1234, 100)


def test_frames_of_one_job_stay_together_whatever_the_gap(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save(T0, job_id="job-1")
    store.save(T0 + 3600, job_id="job-1", period_start=T0 + 1800)
    assert list(store.prints) == ["job-1"]
    assert store.prints["job-1"].count == 2


def test_without_a_job_the_gap_starts_a_new_print(tmp_path: Path) -> None:
    store = Store(tmp_path)
    first = store.save(T0)
    assert first.print_id.startswith("print-")
    assert store.save(T0 + 600).print_id == first.print_id
    third = store.save(T0 + 600 + NEW_PRINT_GAP + 1)
    assert third.print_id != first.print_id
    assert store.prints[first.print_id].count == 2
    assert store.prints[third.print_id].count == 1


def test_without_a_job_a_new_active_period_starts_a_new_print(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    first = store.save(T0)
    # the status sensor went inactive and active again after the first frame
    second = store.save(T0 + 60, period_start=T0 + 30)
    assert second.print_id != first.print_id
    # later frames of that period stay together
    assert store.save(T0 + 90, period_start=T0 + 30).print_id == second.print_id


def test_a_job_less_print_is_renamed_when_the_job_id_turns_up(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    first = store.save(T0)
    result = store.save(T0 + 10, job_id="job-2")
    assert result.removed == [first.print_id]
    assert list(store.prints) == ["job-2"]
    assert not (tmp_path / first.print_id).exists()
    meta = store.index("job-2")
    assert len(meta["frames"]) == 2
    assert meta["task"] == "job-2"
    assert meta["start"] == T0
    assert store.prints["job-2"].size == 200


def test_no_rename_after_the_gap_or_in_a_new_period(tmp_path: Path) -> None:
    store = Store(tmp_path)
    first = store.save(T0)
    store.save(T0 + NEW_PRINT_GAP + 1, job_id="job-2")
    assert sorted(store.prints) == sorted([first.print_id, "job-2"])

    other = Store(tmp_path / "other")
    early = other.save(T0)
    other.save(T0 + 60, job_id="job-3", period_start=T0 + 30)
    assert sorted(other.prints) == sorted([early.print_id, "job-3"])


def test_a_new_job_inside_the_gap_starts_its_own_print(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save(T0, job_id="job-1")
    store.save(T0 + 10, job_id="job-2")
    assert sorted(store.prints) == ["job-1", "job-2"]


def test_a_frame_without_a_job_joins_the_latest_job_inside_the_gap(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    store.save(T0, job_id="job-1")
    assert store.save(T0 + 10).print_id == "job-1"


def test_job_less_prints_started_in_the_same_second_get_distinct_ids(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    first = store.save(T0)
    # new active periods, each starting after the latest frame
    second = store.save(T0 + 0.2, period_start=T0 + 0.1)
    third = store.save(T0 + 0.4, period_start=T0 + 0.3)
    assert len({first.print_id, second.print_id, third.print_id}) == 3


def test_long_prints_drop_every_other_low_score_frame(tmp_path: Path) -> None:
    store = Store(tmp_path)
    limits = Limits(max_frames=4)
    scores = [0.01, 0.02, 0.6, 0.03, 0.04]
    for i, score in enumerate(scores):
        store.save(T0 + 10 * i, job_id="job-1", score=score, limits=limits)
    kept = [fr["s"] for fr in store.index("job-1")["frames"]]
    # low scores before the newest frame: 0.01, 0.02, 0.03; every other goes
    assert kept == [0.01, 0.6, 0.03, 0.04]
    assert len(list((tmp_path / "job-1").glob("*.jpg"))) == 4
    summary = store.prints["job-1"]
    assert (summary.count, summary.max_score, summary.size) == (4, 0.6, 400)


def test_thinning_never_drops_a_scored_frame_or_the_newest(tmp_path: Path) -> None:
    store = Store(tmp_path)
    limits = Limits(max_frames=2)
    for i, score in enumerate([0.3, 0.5, 0.2, 0.0]):
        result = store.save(T0 + 10 * i, job_id="job-1", score=score, limits=limits)
    assert [fr["s"] for fr in store.index("job-1")["frames"]] == [0.3, 0.5, 0.2, 0.0]
    assert (tmp_path / "job-1" / result.frame).is_file()


def test_only_the_last_prints_are_kept(tmp_path: Path) -> None:
    store = Store(tmp_path)
    limits = Limits(keep_prints=2)
    for i in range(3):
        store.save(T0 + 3600 * i, job_id=f"job-{i}", limits=limits)
    assert sorted(store.prints) == ["job-1", "job-2"]
    assert not (tmp_path / "job-0").exists()


def test_the_total_size_cap_drops_the_oldest_prints(tmp_path: Path) -> None:
    store = Store(tmp_path)
    limits = Limits(max_bytes=250)  # room for two 100-byte frames
    for i in range(3):
        store.save(T0 + 3600 * i, job_id=f"job-{i}", limits=limits)
    assert sorted(store.prints) == ["job-1", "job-2"]
    assert not (tmp_path / "job-0").exists()


def test_the_size_cap_never_removes_the_print_being_written(tmp_path: Path) -> None:
    store = Store(tmp_path)
    limits = Limits(max_bytes=150)
    store.save(T0, job_id="old", limits=limits)
    for i in range(3):
        store.save(T0 + 3600 + 10 * i, job_id="live", limits=limits)
    assert list(store.prints) == ["live"]
    assert store.prints["live"].count == 3


def test_stored_prints_are_found_again(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save(T0, job_id="job-1", score=0.4)
    store.save(T0 + 10, job_id="job-1", score=0.2)
    (tmp_path / "not a print").mkdir()
    (tmp_path / "check_" f"{'a' * 32}.jpg").write_bytes(JPEG)
    assert load_summaries(tmp_path) == store.prints
    assert not list(tmp_path.glob("check_*"))


def test_limits_come_from_the_options() -> None:
    assert limits_from_options({}) == Limits(
        keep_prints=10, max_frames=1500, max_bytes=1500 * 1024 * 1024
    )
    assert limits_from_options(
        {"frames_keep_prints": 3.0, "frames_max_per_print": 200, "frames_max_mb": 50}
    ) == Limits(keep_prints=3, max_frames=200, max_bytes=50 * 1024 * 1024)


def test_job_values_are_cleaned() -> None:
    assert frames.safe_job_id("abc-123_X") == "abc-123_X"
    for bad in ("", "unknown", "unavailable", "../x", "a/b", "x" * 65, None):
        assert frames.safe_job_id(bad) == ""
    assert frames.job_name("unknown") == ""
    assert frames.job_name(" cube.gcode ") == "cube.gcode"
    assert len(frames.job_name("n" * 500)) == 200
