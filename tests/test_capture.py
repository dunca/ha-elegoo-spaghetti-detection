"""A scheduled check keeps the exact frame Obico scored."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.elegoo_spaghetti_detection import frames
from custom_components.elegoo_spaghetti_detection.const import (
    EVENT_DETECTION_RESULT,
    EVENT_SPAGHETTI_DETECTED,
)

from .conftest import (
    CAMERA_URL,
    HA_HOST,
    JPEG,
    OBICO_HOST,
    SetupDetector,
    calls_to,
    mock_obico,
    obico_img,
    runtime_of,
)

FRAMES_ROOT = ("elegoo_spaghetti_detection", "frames")


def _root(hass: HomeAssistant, entry_id: str) -> Path:
    return Path(hass.config.path(*FRAMES_ROOT, entry_id))


def _event_keys() -> set[str]:
    return {
        "config_entry",
        "detector",
        "name",
        "camera",
        "manual",
        "printer_state",
        "confidence",
        "raw_score",
        "detected",
        "detections",
        "image_url",
        "last_error",
        "last_run",
        "next_run",
        "status",
    }


async def test_scheduled_check_keeps_the_exact_frame(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The image is fetched once, saved, and Obico reads the saved copy."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    root = _root(hass, entry.entry_id)
    seen_by_obico: list[bytes] = []
    answer = AiohttpClientMockResponse(
        "get",
        URL(f"{OBICO_HOST}/p/"),
        json={"detections": [["failure", 0.4567, [0, 0, 1, 1]]]},
    )

    async def obico(method: str, url: URL, data: object) -> AiohttpClientMockResponse:
        # While Obico works, the frame it was handed is on disk.
        img = parse_qs(urlsplit(str(url)).query)["img"][0]
        name = urlsplit(img).path.rsplit("/", 1)[-1]
        seen_by_obico.append((root / name).read_bytes())
        return answer

    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, side_effect=obico)
    events = async_capture_events(hass, EVENT_DETECTION_RESULT)

    result = await runtime.async_run_detection(manual=False)
    await hass.async_block_till_done()

    # one image request, to the URL Obico used to fetch itself
    assert [str(call[1]) for call in calls_to(aioclient_mock, HA_HOST)] == [CAMERA_URL]
    assert seen_by_obico == [JPEG]

    (img,) = obico_img(aioclient_mock)
    parts = urlsplit(img)
    assert f"{parts.scheme}://{parts.netloc}" == HA_HOST
    assert parts.path.startswith(
        f"/api/elegoo_spaghetti_detection/frames/{entry.entry_id}/check_"
    )
    assert parse_qs(parts.query)["token"] == [frames._TOKEN]

    # the frame was moved under its print, named by time and score
    (summary,) = runtime.frames.prints.values()
    assert summary.count == 1
    assert summary.max_score == 0.4567
    folder = root / summary.print_id
    jpgs = sorted(p.name for p in folder.glob("*.jpg"))
    assert len(jpgs) == 1
    assert jpgs[0].endswith("_04567.jpg")
    assert (folder / jpgs[0]).read_bytes() == JPEG
    assert not list(root.glob("check_*"))
    assert not list(root.rglob("*.part"))
    meta = json.loads((folder / "index.json").read_text())
    assert meta["entry_id"] == entry.entry_id
    assert meta["frames"][0]["f"] == jpgs[0]
    assert meta["frames"][0]["s"] == 0.4567
    assert meta["frames"][0]["n"] == 1

    # the event keeps its keys and gains frame and print_id
    (event,) = events
    assert set(event.data) == _event_keys() | {"frame", "print_id"}
    assert event.data["frame"] == jpgs[0]
    assert event.data["print_id"] == summary.print_id
    # image_url stays the camera URL: the frames token never reaches events
    assert event.data["image_url"] == CAMERA_URL
    assert frames._TOKEN not in json.dumps(event.data)
    assert result["image_url"] == CAMERA_URL


async def test_the_detected_event_carries_the_frame_too(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """Both events of a check describe the same kept frame."""
    entry = await setup_detector()
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, score=0.9)
    detected = async_capture_events(hass, EVENT_SPAGHETTI_DETECTED)
    await runtime_of(hass, entry).async_run_detection(manual=False)
    await hass.async_block_till_done()
    (event,) = detected
    assert event.data["frame"].endswith("_09000.jpg")


@pytest.mark.parametrize(
    "camera_response",
    [
        {"status": 503},
        {"content": b"<html>not a picture</html>"},
        {"exc": TimeoutError()},
        {"exc": aiohttp.ClientConnectionError("refused")},
        {"content": JPEG + b"\x00" * frames.MAX_JPEG_BYTES},
    ],
    ids=["http-error", "not-jpeg", "timeout", "connection", "too-large"],
)
async def test_a_failed_fetch_falls_back_to_the_original_url(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    camera_response: dict,
) -> None:
    """No retry; Obico fetches the camera URL itself; no frame is kept."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    aioclient_mock.get(CAMERA_URL, **camera_response)
    mock_obico(aioclient_mock)
    events = async_capture_events(hass, EVENT_DETECTION_RESULT)

    await runtime.async_run_detection(manual=False)
    await hass.async_block_till_done()

    assert len(calls_to(aioclient_mock, HA_HOST)) == 1
    assert obico_img(aioclient_mock) == [CAMERA_URL]
    assert runtime.frames.prints == {}
    (event,) = events
    assert set(event.data) == _event_keys()
    assert event.data["status"] == "clear"


async def test_a_failed_save_falls_back_to_the_original_url(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """A disk error while saving leaves the check as it was."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    with patch.object(frames, "stage_frame", side_effect=OSError("disk full")):
        await runtime.async_run_detection(manual=False)
    assert len(calls_to(aioclient_mock, HA_HOST)) == 1
    assert obico_img(aioclient_mock) == [CAMERA_URL]
    assert runtime.frames.prints == {}
    assert runtime.status == "clear"


async def test_a_failed_obico_call_keeps_no_frame(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """Without a score the saved frame is thrown away."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    root = _root(hass, entry.entry_id)
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, exc=aiohttp.ClientConnectionError("down"))
    await runtime.async_run_detection(manual=False)
    assert runtime.status == "error"
    assert runtime.frames.prints == {}
    assert runtime.frames.staged is None
    assert not list(root.glob("check_*"))


async def test_manual_checks_are_not_kept(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """A manual check behaves as before: Obico gets the camera URL."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock)
    events = async_capture_events(hass, EVENT_DETECTION_RESULT)
    await runtime.async_run_detection(manual=True)
    await hass.async_block_till_done()
    assert calls_to(aioclient_mock, HA_HOST) == []
    assert obico_img(aioclient_mock) == [CAMERA_URL]
    assert runtime.frames.prints == {}
    assert set(events[0].data) == _event_keys()


async def test_frames_off_is_the_old_behaviour(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """With frames off, the only request is Obico's, with the camera URL."""
    entry = await setup_detector(options={"frames_enabled": False})
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.6)
    events = async_capture_events(hass, EVENT_DETECTION_RESULT)
    result = await runtime.async_run_detection(manual=False)
    await hass.async_block_till_done()

    assert len(aioclient_mock.mock_calls) == 1
    method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert method.upper() == "GET"
    assert str(url.with_query(None)) == f"{OBICO_HOST}/p/"
    assert obico_img(aioclient_mock) == [CAMERA_URL]
    assert headers == {"Authorization": "Bearer test-obico-token"}
    assert runtime.frames.prints == {}
    assert not _root(hass, entry.entry_id).exists()
    (event,) = events
    assert set(event.data) == _event_keys()
    assert result["image_url"] == CAMERA_URL
    assert result["status"] == "detected"


async def test_a_snapshot_url_is_fetched_as_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The direct snapshot URL is the one image request."""
    snapshot = "http://printer.example.test:8080/snapshot"
    entry = await setup_detector(snapshot_url=snapshot)
    aioclient_mock.get(snapshot, content=JPEG)
    mock_obico(aioclient_mock)
    await runtime_of(hass, entry).async_run_detection(manual=False)
    assert [str(c[1]) for c in aioclient_mock.mock_calls][0] == snapshot
    assert len(calls_to(aioclient_mock, "http://printer.example.test")) == 1
    assert obico_img(aioclient_mock)[0].startswith(
        f"{HA_HOST}/api/elegoo_spaghetti_detection/frames/"
    )


async def test_job_entities_name_the_print(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """With a job id entity the folder is the job id."""
    entry = await setup_detector(
        options={
            "job_id_entity": "sensor.test_printer_task_id",
            "job_name_entity": "sensor.test_printer_file_name",
        }
    )
    hass.states.async_set("sensor.test_printer_task_id", "a1b2c3-job")
    hass.states.async_set("sensor.test_printer_file_name", "cube.gcode")
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    runtime = runtime_of(hass, entry)
    await runtime.async_run_detection(manual=False)
    summary = runtime.frames.prints["a1b2c3-job"]
    assert (summary.job_id, summary.name) == ("a1b2c3-job", "cube.gcode")
    meta = json.loads(
        (_root(hass, entry.entry_id) / "a1b2c3-job" / "index.json").read_text()
    )
    assert (meta["task"], meta["name"]) == ("a1b2c3-job", "cube.gcode")


@pytest.mark.parametrize("value", ["unknown", "unavailable", "../../etc", "a b"])
async def test_unusable_job_ids_are_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    value: str,
) -> None:
    """A job id that is not a safe folder name is treated as no job id."""
    entry = await setup_detector(options={"job_id_entity": "sensor.test_printer_task_id"})
    hass.states.async_set("sensor.test_printer_task_id", value)
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    runtime = runtime_of(hass, entry)
    await runtime.async_run_detection(manual=False)
    (print_id,) = runtime.frames.prints
    assert print_id.startswith("print-")


async def test_status_becoming_active_starts_a_new_print(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without a job id, an inactive -> active change starts a new print."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    status = "sensor.test_printer_print_status"

    await runtime.async_run_detection(manual=False)
    assert len(runtime.frames.prints) == 1

    # unavailable -> printing is not a new print (e.g. a reconnect)
    freezer.tick(10)
    hass.states.async_set(status, "unavailable")
    hass.states.async_set(status, "printing")
    await hass.async_block_till_done()
    await runtime.async_run_detection(manual=False)
    assert len(runtime.frames.prints) == 1

    # idle -> printing is
    freezer.tick(10)
    hass.states.async_set(status, "idle")
    hass.states.async_set(status, "printing")
    await hass.async_block_till_done()
    await runtime.async_run_detection(manual=False)
    assert len(runtime.frames.prints) == 2
    assert sorted(p.count for p in runtime.frames.prints.values()) == [1, 2]


async def test_frames_are_found_again_after_a_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The store reads the indexes back and drops unfinished checks."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, score=0.3)
    await runtime.async_run_detection(manual=False)
    before = dict(runtime.frames.prints)
    leftover = _root(hass, entry.entry_id) / f"check_{'0' * 32}.jpg"
    leftover.write_bytes(JPEG)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert runtime_of(hass, entry).frames.prints == before
    assert not leftover.exists()


async def test_removing_the_detector_removes_its_frames(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """Deleting the config entry deletes its frames folder."""
    entry = await setup_detector()
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    await runtime_of(hass, entry).async_run_detection(manual=False)
    assert _root(hass, entry.entry_id).is_dir()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not _root(hass, entry.entry_id).exists()
