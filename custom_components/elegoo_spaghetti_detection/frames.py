"""Keep the camera frame behind every scheduled spaghetti check, per print.

A scheduled check normally hands Obico an image URL and keeps nothing. With
frame keeping on, the detector fetches that image once itself, saves it, and
hands Obico a Home Assistant URL that serves the saved copy, so the frame kept
is exactly the frame the model scored::

    <config>/elegoo_spaghetti_detection/frames/<entry id>/check_<random>.jpg
        the frame of the check in progress, until Obico has scored it
    <config>/elegoo_spaghetti_detection/frames/<entry id>/<print id>/HHMMSS_SSSSS.jpg
    <config>/elegoo_spaghetti_detection/frames/<entry id>/<print id>/index.json

``HHMMSS`` is Home Assistant's local time and ``SSSSS`` the score times 10000.
The print id is the job id read from the configured job id entity, or
``print-<UTC yyyymmdd-hhmmss>`` for a print without one.

There is never more than one image request per check: one GET with a 10 s
timeout and no retry, the same request Obico would otherwise make. If it fails
or the frame cannot be saved, Obico fetches the original URL as before and no
frame is kept.

Limits, per detector: the last ``keep_prints`` prints; at most ``max_frames``
frames per print, past which every other frame scored below ``KEEP_SCORE`` is
dropped (a long print keeps its whole timeline at a coarser step); and
``max_bytes`` on disk, the oldest prints going first.

The folder is outside ``www``. The views below serve frames to requests Home
Assistant has already authenticated, or to requests carrying an access token
that is random per Home Assistant run and published only where signed-in users
can read it (the frames sensor's attributes) and in the URL handed to Obico.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from homeassistant.components.http import KEY_AUTHENTICATED, HomeAssistantView
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import KEY_HASS
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FRAMES_ENABLED,
    CONF_FRAMES_KEEP_PRINTS,
    CONF_FRAMES_MAX_MB,
    CONF_FRAMES_MAX_PER_PRINT,
    DEFAULT_FRAMES_ENABLED,
    DEFAULT_FRAMES_KEEP_PRINTS,
    DEFAULT_FRAMES_MAX_MB,
    DEFAULT_FRAMES_MAX_PER_PRINT,
    DOMAIN,
    RUNTIME_DATA,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

FRAMES_DIR = (DOMAIN, "frames")
VIEW_BASE = f"/api/{DOMAIN}/frames"
VIEWER_FILE = Path(__file__).parent / "static" / "frames.html"
FETCH_TIMEOUT = 10  # seconds, one try
MAX_JPEG_BYTES = 5 * 1024 * 1024  # a 1280x720 frame is about 130 KB
NEW_PRINT_GAP = 900  # seconds without a frame before a job-less frame starts a print
KEEP_SCORE = 0.1  # thinning never drops a frame scored at or above this
INDEX = "index.json"
VIEWER = "viewer"
MB = 1024 * 1024

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FRAME_NAME = re.compile(r"^\d{6}_\d{5}\.jpg$")
CHECK_NAME = re.compile(r"^check_[0-9a-f]{32}\.jpg$")
_JPEG_SOI = b"\xff\xd8"
_IGNORED_STATES = {"", "unknown", "unavailable", "none"}
# Random per Home Assistant run.
_TOKEN = secrets.token_urlsafe(24)


@dataclass(frozen=True, slots=True)
class Limits:
    """How much is kept, per detector."""

    keep_prints: int = DEFAULT_FRAMES_KEEP_PRINTS
    max_frames: int = DEFAULT_FRAMES_MAX_PER_PRINT
    max_bytes: int = DEFAULT_FRAMES_MAX_MB * MB


@dataclass(frozen=True, slots=True)
class PrintSummary:
    """What is known about one stored print without reading its index."""

    print_id: str
    job_id: str
    name: str
    start: float
    end: float
    count: int
    max_score: float
    size: int

    def as_row(self) -> dict[str, Any]:
        """Return the print as the viewer's index lists it."""
        return {
            "id": self.print_id,
            "job_id": self.job_id,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "count": self.count,
            "max": self.max_score,
        }


@dataclass(frozen=True, slots=True)
class StagedFrame:
    """A frame fetched for a check that Obico has not scored yet."""

    name: str
    taken: float
    size: int


@dataclass(slots=True)
class SaveResult:
    """What a save changed, to be applied to the in-memory summaries."""

    print_id: str
    frame: str
    summary: PrintSummary
    removed: list[str] = field(default_factory=list)


def limits_from_options(options: Mapping[str, Any]) -> Limits:
    """Return the limits set in a detector's options, or the defaults."""

    def number(key: str, default: int) -> int:
        try:
            return max(1, int(float(options.get(key, default))))
        except (TypeError, ValueError):
            return default

    return Limits(
        keep_prints=number(CONF_FRAMES_KEEP_PRINTS, DEFAULT_FRAMES_KEEP_PRINTS),
        max_frames=number(CONF_FRAMES_MAX_PER_PRINT, DEFAULT_FRAMES_MAX_PER_PRINT),
        max_bytes=number(CONF_FRAMES_MAX_MB, DEFAULT_FRAMES_MAX_MB) * MB,
    )


def frames_enabled(options: Mapping[str, Any]) -> bool:
    """Return whether frame keeping is on in a detector's options."""
    return bool(options.get(CONF_FRAMES_ENABLED, DEFAULT_FRAMES_ENABLED))


def safe_job_id(value: Any) -> str:
    """Return a job id usable as a folder name, or an empty string."""
    text = str(value or "").strip()
    if text.lower() in _IGNORED_STATES:
        return ""
    return text if SAFE_ID.match(text) else ""


def job_name(value: Any) -> str:
    """Return a printable job name, or an empty string."""
    text = str(value or "").strip()
    return "" if text.lower() in _IGNORED_STATES else text[:200]


# --- storage (executor) ----------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_atomically(path: Path, data: bytes) -> None:
    partial_path = path.with_name(path.name + ".part")
    partial_path.write_bytes(data)
    partial_path.replace(path)


def _write_json(path: Path, data: Any) -> None:
    _write_atomically(path, json.dumps(data, separators=(",", ":")).encode())


def _jpeg_bytes(folder: Path) -> int:
    try:
        return sum(
            entry.stat().st_size
            for entry in os.scandir(folder)
            if entry.is_file() and entry.name.endswith(".jpg")
        )
    except OSError:
        return 0


def _summary(print_id: str, meta: dict[str, Any], size: int) -> PrintSummary:
    frames = [fr for fr in meta.get("frames") or [] if isinstance(fr, dict)]
    return PrintSummary(
        print_id=print_id,
        job_id=str(meta.get("task") or ""),
        name=str(meta.get("name") or ""),
        start=float(meta.get("start") or 0),
        end=float(meta.get("end") or 0),
        count=len(frames),
        max_score=max((float(fr.get("s") or 0) for fr in frames), default=0.0),
        size=size,
    )


def load_summaries(root: Path) -> dict[str, PrintSummary]:
    """Read every stored print's index and drop frames of unfinished checks."""
    found: dict[str, PrintSummary] = {}
    if not root.is_dir():
        return found
    for path in root.iterdir():
        if path.is_file() and (
            CHECK_NAME.match(path.name) or path.name.endswith(".part")
        ):
            path.unlink(missing_ok=True)
            continue
        if not path.is_dir() or not SAFE_ID.match(path.name):
            continue
        meta = _read_json(path / INDEX)
        if not isinstance(meta, dict) or not meta.get("frames"):
            continue
        found[path.name] = _summary(path.name, meta, _jpeg_bytes(path))
    return found


def read_print_index(root: Path, print_id: str) -> dict[str, Any] | None:
    """Return a stored print's index, or None."""
    if not SAFE_ID.match(print_id):
        return None
    meta = _read_json(root / print_id / INDEX)
    return meta if isinstance(meta, dict) else None


def stage_frame(root: Path, jpeg: bytes) -> str:
    """Save the frame of a check in progress; return its file name."""
    root.mkdir(parents=True, exist_ok=True)
    name = f"check_{secrets.token_hex(16)}.jpg"
    _write_atomically(root / name, jpeg)
    return name


def discard_staged(root: Path, name: str) -> None:
    """Remove the frame of a check that produced no score."""
    if CHECK_NAME.match(name):
        try:
            (root / name).unlink(missing_ok=True)
        except OSError:
            pass


def _thin(folder: Path, frames: list[dict[str, Any]]) -> tuple[list[dict], int]:
    """Drop every other low-score frame (never the newest); return kept, freed."""
    low = [
        i
        for i, fr in enumerate(frames[:-1])
        if float(fr.get("s") or 0) < KEEP_SCORE
    ]
    drop = set(low[1::2])
    freed = 0
    for i in drop:
        path = folder / str(frames[i].get("f"))
        try:
            freed += path.stat().st_size
            path.unlink()
        except OSError:
            pass
    return [fr for i, fr in enumerate(frames) if i not in drop], freed


def _choose_print(
    root: Path,
    own: list[PrintSummary],
    known: Mapping[str, PrintSummary],
    job_id: str,
    now: float,
    period_start: float | None,
) -> tuple[str, list[str]]:
    """Return the print a frame belongs to, and any print id it replaces.

    With a job id the frame goes to that job. Without one it joins the latest
    print unless that went quiet ``NEW_PRINT_GAP`` ago or its last frame is
    older than the start of the current active period (the print status
    sensor entered an active state since). A print that started without a job
    id is renamed to the job id when one turns up while it is still current.
    """
    latest = own[-1] if own else None
    current = (
        latest is not None
        and now - latest.end < NEW_PRINT_GAP
        and (period_start is None or latest.end >= period_start)
    )
    if job_id:
        if (
            current
            and latest is not None
            and job_id not in known
            and not latest.job_id
            and (root / latest.print_id).is_dir()
            and not (root / job_id).exists()
        ):
            (root / latest.print_id).rename(root / job_id)
            return job_id, [latest.print_id]
        return job_id, []
    if current and latest is not None:
        return latest.print_id, []
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
    print_id = f"print-{stamp}"
    suffix = 1
    while print_id in known or (root / print_id).exists():
        suffix += 1
        print_id = f"print-{stamp}-{suffix}"
    return print_id, []


def _prune(
    root: Path,
    prints: Mapping[str, PrintSummary],
    current: str,
    limits: Limits,
) -> list[str]:
    """Remove the oldest prints beyond the count and size limits."""
    own = sorted(prints.values(), key=lambda p: p.start)
    removed = [
        summary.print_id
        for summary in own[: max(0, len(own) - limits.keep_prints)]
        if summary.print_id != current
    ]
    total = sum(p.size for p in own if p.print_id not in removed)
    for summary in own:
        if total <= limits.max_bytes:
            break
        if summary.print_id == current or summary.print_id in removed:
            continue
        removed.append(summary.print_id)
        total -= summary.size
    for print_id in removed:
        shutil.rmtree(root / print_id, ignore_errors=True)
    return removed


def frame_name(now: float, score: float) -> str:
    """Return a frame's file name: local time and score."""
    stamp = dt_util.as_local(dt_util.utc_from_timestamp(now)).strftime("%H%M%S")
    return f"{stamp}_{round(score * 10000):05d}.jpg"


def save_frame(
    root: Path,
    prints: Mapping[str, PrintSummary],
    *,
    entry_id: str,
    staged: StagedFrame,
    job_id: str,
    name: str,
    score: float,
    detections: int,
    period_start: float | None,
    limits: Limits,
) -> SaveResult:
    """File a scored frame under its print and enforce the limits.

    Runs in the executor on a snapshot of the summaries; the caller applies
    the returned changes. The staged frame is moved into place with a rename
    and the index is written to ``.part`` and renamed.
    """
    now = staged.taken
    own = sorted(prints.values(), key=lambda p: p.end)
    print_id, replaced = _choose_print(root, own, prints, job_id, now, period_start)
    folder = root / print_id
    folder.mkdir(parents=True, exist_ok=True)
    meta = _read_json(folder / INDEX)
    if not isinstance(meta, dict):
        meta = {"start": round(now, 1), "frames": []}
    frames: list[dict[str, Any]] = [
        fr for fr in meta.get("frames") or [] if isinstance(fr, dict)
    ]

    file_name = frame_name(now, score)
    # sizes are tracked as frames come and go, not by listing the folder
    previous = prints.get(replaced[0] if replaced else print_id)
    size = previous.size if previous is not None else _jpeg_bytes(folder)
    if any(fr.get("f") == file_name for fr in frames):
        existing = folder / file_name
        size -= existing.stat().st_size if existing.is_file() else 0
        frames = [fr for fr in frames if fr.get("f") != file_name]
    (root / staged.name).replace(folder / file_name)
    size += staged.size
    frames.append({"t": round(now, 1), "f": file_name, "s": score, "n": detections})
    if len(frames) > limits.max_frames:
        frames, freed = _thin(folder, frames)
        size -= freed

    meta.update({"entry_id": entry_id, "end": round(now, 1), "frames": frames})
    if job_id:
        meta["task"] = job_id
    if name:
        meta["name"] = name
    _write_json(folder / INDEX, meta)

    summary = _summary(print_id, meta, max(0, size))
    working = {k: v for k, v in prints.items() if k not in replaced}
    working[print_id] = summary
    removed = [*replaced, *_prune(root, working, print_id, limits)]
    return SaveResult(print_id, file_name, summary, removed)


def _delete_print(root: Path, print_id: str) -> None:
    shutil.rmtree(root / print_id, ignore_errors=True)


def remove_all(root: Path) -> None:
    """Remove every frame kept for a detector."""
    shutil.rmtree(root, ignore_errors=True)


# --- the image request -----------------------------------------------------


async def async_fetch_jpeg(hass: HomeAssistant, url: str) -> bytes | None:
    """GET one JPEG: one try, ``FETCH_TIMEOUT`` at most, ``MAX_JPEG_BYTES``."""
    session = async_get_clientsession(hass)
    body = bytearray()
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        ) as response:
            if response.status != HTTPStatus.OK:
                LOGGER.debug("Frame fetch: HTTP %s", response.status)
                return None
            async for chunk in response.content.iter_chunked(64 * 1024):
                body += chunk
                if len(body) > MAX_JPEG_BYTES:
                    LOGGER.debug("Frame fetch: image larger than %s bytes", MAX_JPEG_BYTES)
                    return None
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        LOGGER.debug("Frame fetch failed: %r", err)
        return None
    if not body.startswith(_JPEG_SOI):
        LOGGER.debug("Frame fetch: not a JPEG")
        return None
    return bytes(body)


# --- one detector's frames (event loop) ------------------------------------


class FrameStore:
    """The frames kept for one detector."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        enabled: bool,
        limits: Limits,
    ) -> None:
        """Initialize the store; call ``async_load`` before use."""
        self.hass = hass
        self.entry_id = entry_id
        self.enabled = enabled
        self.limits = limits
        self.root = Path(hass.config.path(*FRAMES_DIR, entry_id))
        self.prints: dict[str, PrintSummary] = {}
        self.staged: StagedFrame | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Learn which prints are stored."""
        self.prints = await self.hass.async_add_executor_job(
            load_summaries, self.root
        )

    async def async_stage(self, image_url: str, now: float) -> StagedFrame | None:
        """Fetch the check's image once and save it; None when that failed."""
        jpeg = await async_fetch_jpeg(self.hass, image_url)
        if jpeg is None:
            return None
        try:
            name = await self.hass.async_add_executor_job(stage_frame, self.root, jpeg)
        except OSError as err:
            LOGGER.warning("Could not save a spaghetti check frame: %s", err)
            return None
        self.staged = StagedFrame(name=name, taken=now, size=len(jpeg))
        return self.staged

    def check_url(self, base_url: str, staged: StagedFrame) -> str:
        """Return the URL Obico fetches the staged frame from."""
        path = f"{VIEW_BASE}/{self.entry_id}/{staged.name}"
        return f"{base_url.rstrip('/')}{path}?{urlencode({'token': _TOKEN})}"

    async def async_discard(self, staged: StagedFrame) -> None:
        """Forget the frame of a check that produced no score."""
        if self.staged == staged:
            self.staged = None
        await self.hass.async_add_executor_job(discard_staged, self.root, staged.name)

    async def async_commit(
        self,
        staged: StagedFrame,
        *,
        job_id: str,
        name: str,
        score: float,
        detections: int,
        period_start: float | None,
    ) -> SaveResult | None:
        """File a scored frame under its print; None when that failed."""
        if self.staged == staged:
            self.staged = None
        save = partial(
            save_frame,
            self.root,
            entry_id=self.entry_id,
            staged=staged,
            job_id=safe_job_id(job_id),
            name=job_name(name),
            score=round(min(1.0, max(0.0, float(score))), 4),
            detections=max(0, int(detections)),
            period_start=period_start,
            limits=self.limits,
        )
        async with self._lock:
            try:
                result = await self.hass.async_add_executor_job(save, dict(self.prints))
            except OSError as err:
                LOGGER.warning("Could not save a spaghetti check frame: %s", err)
                await self.hass.async_add_executor_job(
                    discard_staged, self.root, staged.name
                )
                return None
            for print_id in result.removed:
                self.prints.pop(print_id, None)
            self.prints[result.print_id] = result.summary
        for print_id in result.removed:
            if print_id != result.print_id:
                LOGGER.debug("Removed spaghetti frames of %s", print_id)
        return result

    async def async_delete(self, print_id: str) -> bool:
        """Delete a stored print; return whether there was one."""
        if not SAFE_ID.match(print_id):
            return False
        async with self._lock:
            known = self.prints.pop(print_id, None) is not None
            if known:
                await self.hass.async_add_executor_job(
                    _delete_print, self.root, print_id
                )
        return known

    async def async_read_print(self, print_id: str) -> dict[str, Any] | None:
        """Return a stored print's index, or None."""
        if print_id not in self.prints:
            return None
        return await self.hass.async_add_executor_job(
            read_print_index, self.root, print_id
        )

    def newest_first(self) -> list[PrintSummary]:
        """Return the stored prints, newest first."""
        return sorted(self.prints.values(), key=lambda p: p.start, reverse=True)

    def viewer_url(self, print_id: str | None = None) -> str:
        """Return the viewer's URL, carrying the access token."""
        query = {"token": _TOKEN}
        if print_id:
            query["print"] = print_id
        return f"{VIEW_BASE}/{self.entry_id}/{VIEWER}?{urlencode(query)}"

    def frame_url(self, print_id: str, name: str, *, token: bool) -> str:
        """Return a frame's URL; without the token it needs a signed-in request."""
        url = f"{VIEW_BASE}/{self.entry_id}/{print_id}/{name}"
        return f"{url}?{urlencode({'token': _TOKEN})}" if token else url

    def attributes(self) -> dict[str, Any]:
        """Return the frames sensor's attributes."""
        return {
            "prints": [
                {
                    "id": summary.print_id,
                    "job_id": summary.job_id or None,
                    "name": summary.name or None,
                    "start": _iso(summary.start),
                    "end": _iso(summary.end),
                    "frames": summary.count,
                    "max": summary.max_score,
                    "viewer_url": self.viewer_url(summary.print_id),
                }
                for summary in self.newest_first()
            ],
            "viewer_url": self.viewer_url(),
        }


def _iso(timestamp: float) -> str | None:
    if not timestamp:
        return None
    return dt_util.as_local(dt_util.utc_from_timestamp(timestamp)).isoformat(
        timespec="seconds"
    )


def runtime_for(hass: HomeAssistant, entry_id: str) -> Any:
    """Return a loaded detector's runtime, or None."""
    if not SAFE_ID.match(entry_id):
        return None
    return hass.data.get(DOMAIN, {}).get(RUNTIME_DATA, {}).get(entry_id)


def store_for(hass: HomeAssistant, entry_id: str) -> FrameStore | None:
    """Return a loaded detector's frame store, or None."""
    runtime = runtime_for(hass, entry_id)
    return getattr(runtime, "frames", None)


# --- views -----------------------------------------------------------------


def authorized(request: web.Request) -> bool:
    """Allow a request Home Assistant authenticated, or the access token."""
    if request.get(KEY_AUTHENTICATED):
        return True
    token = request.query.get("token", "")
    return bool(token) and secrets.compare_digest(token, _TOKEN)


_NO_STORE = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_VIEWER_HEADERS = {
    **_NO_STORE,
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self'; connect-src 'self'; "
        "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "frame-ancestors 'self'"
    ),
}
_EXPIRED = (
    "This link has expired or is incomplete (the access token changes when "
    "Home Assistant restarts). Open the frames again from the dashboard."
)
_VIEWER_CACHE: dict[str, str] = {}


def _json(data: Any) -> web.Response:
    return web.Response(
        text=json.dumps(data, separators=(",", ":")),
        content_type="application/json",
        headers=_NO_STORE,
    )


async def _viewer_html(hass: HomeAssistant) -> str:
    if "html" not in _VIEWER_CACHE:
        _VIEWER_CACHE["html"] = await hass.async_add_executor_job(
            partial(VIEWER_FILE.read_text, encoding="utf-8")
        )
    return _VIEWER_CACHE["html"]


class FramesView(HomeAssistantView):
    """The viewer, a detector's list of prints, and the frame being checked."""

    url = VIEW_BASE + "/{entry_id}/{name}"
    name = f"api:{DOMAIN}:frames"
    # checked in the handler: an authenticated request or the access token
    requires_auth = False

    async def get(
        self, request: web.Request, entry_id: str, name: str
    ) -> web.StreamResponse:
        """Return the viewer, the index of stored prints, or the checked frame."""
        if not authorized(request):
            if name == VIEWER:  # opened from an old link: say what to do
                return web.Response(status=401, text=_EXPIRED, headers=_NO_STORE)
            return web.Response(status=401)
        hass = request.app[KEY_HASS]
        runtime = runtime_for(hass, entry_id)
        store: FrameStore | None = getattr(runtime, "frames", None)
        if store is None:
            return web.Response(status=404)
        if name == VIEWER:
            return web.Response(
                text=await _viewer_html(hass),
                content_type="text/html",
                headers=_VIEWER_HEADERS,
            )
        if name == INDEX:
            return _json(
                {
                    "prints": [p.as_row() for p in store.newest_first()],
                    "warning": runtime.warning_threshold,
                    "failure": runtime.failure_threshold,
                }
            )
        staged = store.staged
        if CHECK_NAME.match(name) and staged is not None and staged.name == name:
            path = store.root / name
            if await hass.async_add_executor_job(path.is_file):
                return web.FileResponse(
                    path,
                    headers={"Content-Type": "image/jpeg", **_NO_STORE},
                )
        return web.Response(status=404)


class FrameFileView(HomeAssistantView):
    """One print's index, and its frames."""

    url = VIEW_BASE + "/{entry_id}/{print_id}/{name}"
    name = f"api:{DOMAIN}:frames:file"
    requires_auth = False

    async def get(
        self, request: web.Request, entry_id: str, print_id: str, name: str
    ) -> web.StreamResponse:
        """Return the index or a frame of a stored print."""
        if not authorized(request):
            return web.Response(status=401)
        hass = request.app[KEY_HASS]
        store = store_for(hass, entry_id)
        if store is None or not SAFE_ID.match(print_id) or print_id not in store.prints:
            return web.Response(status=404)
        if name == INDEX:
            meta = await store.async_read_print(print_id)
            return web.Response(status=404) if meta is None else _json(meta)
        if not FRAME_NAME.match(name):
            return web.Response(status=404)
        path = store.root / print_id / name
        if not await hass.async_add_executor_job(path.is_file):
            return web.Response(status=404)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "image/jpeg",
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )
