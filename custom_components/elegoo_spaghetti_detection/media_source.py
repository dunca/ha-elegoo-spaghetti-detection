"""Browse the spaghetti check frames in the Media panel.

``<entry_id>`` lists a detector's stored prints, ``<entry_id>/<print_id>``
that print's frames, and ``<entry_id>/<print_id>/<file>`` is one frame.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, RUNTIME_DATA
from .frames import FRAME_NAME, PrintSummary, runtime_for, store_for

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TITLE = "Spaghetti frames"
# ECC2_0.4_<model>_<filament>_<layer height>_<slicer estimate>.gcode
_SLICER_NAME = re.compile(r"^ECC2_[\d.]+_(?P<model>.+?)_[^_]*_[\d.]+_[\dhms]+$")


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up the spaghetti frames media source."""
    return SpaghettiFramesSource(hass)


def model_name(file_name: str) -> str:
    """Return the model's name from a sliced file's name."""
    stem = file_name.removesuffix(".gcode")
    match = _SLICER_NAME.match(stem)
    return (match.group("model") if match else stem).replace("+", " ")


def print_title(summary: PrintSummary) -> str:
    """Return a stored print's name, start time, frames and highest score."""
    started = dt_util.as_local(dt_util.utc_from_timestamp(summary.start))
    if summary.name:
        name = model_name(summary.name)
    else:
        name = summary.job_id or "Print"
    return (
        f"{name} · {started:%d %b %H:%M} · {summary.count} frames"
        f" · max {round(summary.max_score * 100)} %"
    )


def frame_title(frame: dict[str, Any]) -> str:
    """Return a frame's time and score."""
    taken = dt_util.as_local(dt_util.utc_from_timestamp(float(frame.get("t") or 0)))
    return f"{taken:%H:%M:%S} · {round(float(frame.get('s') or 0) * 100)} %"


def _directory(
    identifier: str | None,
    title: str,
    children: list[BrowseMediaSource] | None = None,
    children_media_class: str = MediaClass.DIRECTORY,
) -> BrowseMediaSource:
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier,
        media_class=MediaClass.DIRECTORY,
        media_content_type=MediaType.IMAGE,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=children_media_class,
    )


class SpaghettiFramesSource(MediaSource):
    """The frames kept for every detector."""

    name = TITLE

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Hand out the URL of one stored frame (Home Assistant signs it)."""
        parts = (item.identifier or "").split("/")
        if len(parts) != 3:  # <entry_id>/<print_id>/<file>
            raise Unresolvable("Not a frame")
        entry_id, print_id, name = parts
        store = store_for(self.hass, entry_id)
        if store is None or not FRAME_NAME.match(name):
            raise Unresolvable("Not a frame")
        meta = await store.async_read_print(print_id)
        if meta is None or not any(
            isinstance(fr, dict) and fr.get("f") == name
            for fr in meta.get("frames") or []
        ):
            raise Unresolvable("That frame is gone")
        return PlayMedia(store.frame_url(print_id, name, token=False), "image/jpeg")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """List detectors, then their prints newest first, then frames."""
        if not item.identifier:
            return self._detectors()
        parts = item.identifier.split("/")
        runtime = runtime_for(self.hass, parts[0])
        store = getattr(runtime, "frames", None)
        if store is None:
            raise BrowseError("Unknown detector")
        if len(parts) == 1:
            return _directory(
                item.identifier,
                runtime.name,
                [
                    _directory(
                        f"{store.entry_id}/{summary.print_id}", print_title(summary)
                    )
                    for summary in store.newest_first()
                ],
            )
        if len(parts) != 2:  # <entry_id>/<print_id>
            raise BrowseError("A frame cannot be browsed into")
        print_id = parts[1]
        meta = await store.async_read_print(print_id)
        if meta is None:
            raise BrowseError("No frames are kept for that print")
        summary = store.prints.get(print_id)
        return _directory(
            item.identifier,
            print_title(summary) if summary is not None else print_id,
            [
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{item.identifier}/{frame['f']}",
                    media_class=MediaClass.IMAGE,
                    media_content_type="image/jpeg",
                    title=frame_title(frame),
                    can_play=True,
                    can_expand=False,
                    thumbnail=store.frame_url(print_id, frame["f"], token=True),
                )
                for frame in meta.get("frames") or []
                if isinstance(frame, dict) and FRAME_NAME.match(str(frame.get("f")))
            ],
            MediaClass.IMAGE,
        )

    def _detectors(self) -> BrowseMediaSource:
        runtimes = self.hass.data.get(DOMAIN, {}).get(RUNTIME_DATA, {})
        return _directory(
            None,
            TITLE,
            [
                _directory(entry_id, runtime.name)
                for entry_id, runtime in runtimes.items()
            ],
        )
