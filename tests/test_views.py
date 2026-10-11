"""Serving the frames: auth, token and path validation."""

from __future__ import annotations

from http import HTTPStatus
import json
from urllib.parse import parse_qs, urlsplit

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from yarl import URL

from custom_components.elegoo_spaghetti_detection import frames

from .conftest import (
    CAMERA_URL,
    JPEG,
    OBICO_HOST,
    SetupDetector,
    mock_obico,
    runtime_of,
)

BASE = "/api/elegoo_spaghetti_detection/frames"


async def _one_frame(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    score: float = 0.25,
) -> tuple[str, str, str]:
    """Set up a detector with one kept frame; return entry, print and frame."""
    entry = await setup_detector()
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, score=score)
    runtime = runtime_of(hass, entry)
    await runtime.async_run_detection(manual=False)
    assert runtime.last_frame is not None
    return entry.entry_id, runtime.last_frame.print_id, runtime.last_frame.frame


async def test_obico_reads_the_frame_with_the_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The URL handed to Obico serves the saved frame without a login."""
    entry = await setup_detector()
    client = await hass_client_no_auth()
    fetched: list[tuple[int, bytes, str]] = []
    answer = AiohttpClientMockResponse(
        "get", URL(f"{OBICO_HOST}/p/"), json={"detections": []}
    )

    async def obico(method: str, url: URL, data: object) -> AiohttpClientMockResponse:
        img = urlsplit(parse_qs(urlsplit(str(url)).query)["img"][0])
        response = await client.get(f"{img.path}?{img.query}")
        fetched.append(
            (response.status, await response.read(), response.headers["Cache-Control"])
        )
        # the same path without the token is refused
        assert (await client.get(img.path)).status == HTTPStatus.UNAUTHORIZED
        return answer

    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, side_effect=obico)
    runtime = runtime_of(hass, entry)
    await runtime.async_run_detection(manual=False)
    assert fetched == [(HTTPStatus.OK, JPEG, "no-store")]

    # once scored, the check's URL is gone
    (img,) = [
        parse_qs(urlsplit(str(call[1])).query)["img"][0]
        for call in aioclient_mock.mock_calls
        if str(call[1]).startswith(OBICO_HOST)
    ]
    parts = urlsplit(img)
    response = await client.get(f"{parts.path}?{parts.query}")
    assert response.status == HTTPStatus.NOT_FOUND


async def test_views_refuse_requests_without_auth_or_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Every path answers 401 without a login or the right token."""
    entry_id, print_id, frame = await _one_frame(hass, aioclient_mock, setup_detector)
    client = await hass_client_no_auth()
    for path in (
        f"{BASE}/{entry_id}/viewer",
        f"{BASE}/{entry_id}/index.json?token=wrong",
        f"{BASE}/{entry_id}/{print_id}/index.json",
        f"{BASE}/{entry_id}/{print_id}/{frame}?token=",
    ):
        response = await client.get(path)
        assert response.status == HTTPStatus.UNAUTHORIZED, path

    # an old viewer link says what to do, and serves no page
    text = await (await client.get(f"{BASE}/{entry_id}/viewer")).text()
    assert "expired" in text
    assert "<script" not in text


async def test_views_serve_the_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The token opens the viewer, the indexes and the frames."""
    entry_id, print_id, frame = await _one_frame(hass, aioclient_mock, setup_detector)
    client = await hass_client_no_auth()
    token = f"token={frames._TOKEN}"

    index = await client.get(f"{BASE}/{entry_id}/index.json?{token}")
    assert index.status == HTTPStatus.OK
    assert index.headers["Cache-Control"] == "no-store"
    body = await index.json()
    assert [(r["id"], r["count"], r["max"]) for r in body["prints"]] == [
        (print_id, 1, 0.25)
    ]
    assert (body["warning"], body["failure"]) == (0.3, 0.5)

    meta = await client.get(f"{BASE}/{entry_id}/{print_id}/index.json?{token}")
    assert (await meta.json())["frames"][0]["f"] == frame

    image = await client.get(f"{BASE}/{entry_id}/{print_id}/{frame}?{token}")
    assert image.status == HTTPStatus.OK
    assert image.headers["Content-Type"] == "image/jpeg"
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    assert await image.read() == JPEG

    viewer = await client.get(f"{BASE}/{entry_id}/viewer?{token}&print={print_id}")
    assert viewer.status == HTTPStatus.OK
    assert viewer.content_type == "text/html"
    assert "frame-ancestors 'self'" in viewer.headers["Content-Security-Policy"]
    assert viewer.headers["Referrer-Policy"] == "no-referrer"
    assert viewer.headers["Cache-Control"] == "no-store"
    assert "Spaghetti frames" in await viewer.text()


async def test_views_serve_authenticated_requests(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    hass_client: ClientSessionGenerator,
) -> None:
    """A request Home Assistant authenticated needs no token."""
    entry_id, print_id, frame = await _one_frame(hass, aioclient_mock, setup_detector)
    client = await hass_client()
    for path in (
        f"{BASE}/{entry_id}/viewer",
        f"{BASE}/{entry_id}/index.json",
        f"{BASE}/{entry_id}/{print_id}/index.json",
        f"{BASE}/{entry_id}/{print_id}/{frame}",
    ):
        response = await client.get(path)
        assert response.status == HTTPStatus.OK, path


@pytest.mark.parametrize(
    ("entry", "print_id", "name"),
    [
        ("{entry}", "{print}", "..%2F..%2F..%2Fsecrets.yaml"),
        ("{entry}", "{print}", "notes.txt"),
        ("{entry}", "{print}", "123456_00000.jpg"),  # well formed, not stored
        ("{entry}", "..", "index.json"),
        ("{entry}", "print-missing", "index.json"),
        ("{entry}", "{print}", f"check_{'0' * 32}.jpg"),
        ("01JOTHERENTRY000000000000X", "{print}", "index.json"),
        ("..", "{print}", "index.json"),
    ],
)
async def test_frame_view_only_serves_stored_files(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    hass_client: ClientSessionGenerator,
    entry: str,
    print_id: str,
    name: str,
) -> None:
    """Anything but a stored print's index or frame is a 404."""
    entry_id, stored, _frame = await _one_frame(hass, aioclient_mock, setup_detector)
    client = await hass_client()
    path = "/".join(
        (
            BASE,
            entry.format(entry=entry_id),
            print_id.format(print=stored),
            name,
        )
    )
    response = await client.get(path)
    assert response.status in (HTTPStatus.NOT_FOUND, HTTPStatus.BAD_REQUEST), path


@pytest.mark.parametrize(
    "name", ["other", f"check_{'0' * 32}.jpg", "..%2Findex.json", "secrets.yaml"]
)
async def test_detector_view_only_serves_known_names(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    hass_client: ClientSessionGenerator,
    name: str,
) -> None:
    """Only the viewer, the index and the frame being checked are served."""
    entry_id, _print, _frame = await _one_frame(hass, aioclient_mock, setup_detector)
    client = await hass_client()
    response = await client.get(f"{BASE}/{entry_id}/{name}")
    assert response.status in (HTTPStatus.NOT_FOUND, HTTPStatus.BAD_REQUEST)


async def test_viewer_index_lists_newest_first(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    hass_client: ClientSessionGenerator,
) -> None:
    """The index the viewer reads has the newest print first."""
    entry = await setup_detector(options={"job_id_entity": "sensor.job"})
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    runtime = runtime_of(hass, entry)
    for job, start in (("job-a", 100.0), ("job-b", 200.0)):
        hass.states.async_set("sensor.job", job)
        await runtime.async_run_detection(manual=False)
        # make the start times distinct without waiting
        summary = runtime.frames.prints[job]
        runtime.frames.prints[job] = frames.PrintSummary(
            **{**{f: getattr(summary, f) for f in summary.__slots__}, "start": start}
        )
    client = await hass_client()
    body = json.loads(await (await client.get(f"{BASE}/{entry.entry_id}/index.json")).text())
    assert [row["id"] for row in body["prints"]] == ["job-b", "job-a"]
