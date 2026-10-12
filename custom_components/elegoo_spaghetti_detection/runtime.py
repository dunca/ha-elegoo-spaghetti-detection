"""Runtime detection logic for Elegoo spaghetti detection."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CONFIDENCE,
    ATTR_DETECTED,
    ATTR_DETECTIONS,
    ATTR_FRAME,
    ATTR_IMAGE_URL,
    ATTR_LAST_ERROR,
    ATTR_LAST_RUN,
    ATTR_NEXT_RUN,
    ATTR_PRINT_ID,
    ATTR_RAW_SCORE,
    ATTR_STATUS,
    CONF_ACTIVE_PRINT_STATES,
    CONF_CAMERA,
    CONF_CHAMBER_LIGHT,
    CONF_COOLDOWN_SECONDS,
    CONF_DETECTION_INTERVAL,
    CONF_FAILURE_THRESHOLD,
    CONF_HOME_ASSISTANT_HOST,
    CONF_INSTANCE_ID,
    CONF_JOB_ID_ENTITY,
    CONF_JOB_NAME_ENTITY,
    CONF_LIGHT_CONTROL_MODE,
    CONF_LIGHT_SETTLE_SECONDS,
    CONF_OBICO_AUTH_TOKEN,
    CONF_OBICO_HOST,
    CONF_PRINT_STATUS_SENSOR,
    CONF_REQUIRE_LIGHT,
    CONF_RUN_WITHOUT_PRINTING,
    CONF_SENSITIVITY,
    CONF_SNAPSHOT_URL,
    CONF_WARNING_THRESHOLD,
    DEFAULT_ACTIVE_PRINT_STATES,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DETECTION_INTERVAL,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_LIGHT_CONTROL_MODE,
    DEFAULT_LIGHT_SETTLE_SECONDS,
    DEFAULT_REQUIRE_LIGHT,
    DEFAULT_SENSITIVITY,
    DEFAULT_WARNING_THRESHOLD,
    DOMAIN,
    EVENT_DETECTION_RESULT,
    EVENT_SPAGHETTI_DETECTED,
    LAST_RESULT_NONE,
    LIGHT_CONTROL_LEAVE_ON,
    LIGHT_CONTROL_OFF,
    LIGHT_CONTROL_RESTORE,
    RUNTIME_ML_LOCK,
    SENSITIVITY_THRESHOLDS,
    STALL_INTERVALS,
    STALL_MIN_SECONDS,
    STATUS_BUSY,
    STATUS_CHECKING,
    STATUS_CLEAR,
    STATUS_DETECTED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_LIGHT_OFF,
    STATUS_UNAVAILABLE,
    STATUS_WAITING_FOR_PRINT,
    STATUS_WARNING,
)
from .frames import (
    FrameStore,
    SaveResult,
    StagedFrame,
    frames_enabled,
    job_name,
    limits_from_options,
    safe_job_id,
)

LOGGER = logging.getLogger(__name__)

UNKNOWN_STATES = frozenset({"unknown", "unavailable"})

# Internal gate result: the inferred current_status guard is not active. It
# shows as waiting_for_print, or keeps status_unavailable if already shown.
_GUARD_INACTIVE = "guard_inactive"


def _parse_states(value: str | None) -> set[str]:
    """Parse a comma-separated list of states."""
    if not value:
        value = DEFAULT_ACTIVE_PRINT_STATES
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def _normalize_state(value: Any) -> str:
    """Normalize a Home Assistant state string for comparisons."""
    return str(value).strip().lower()


def _score_detections(result: dict[str, Any]) -> tuple[float, int]:
    """Return a simple confidence score from the Obico detection payload."""
    score = 0.0
    detections = result.get("detections") or []
    for detection in detections:
        try:
            score += float(detection[1])
        except (TypeError, ValueError, IndexError):
            continue
    return min(1.0, max(0.0, score)), len(detections)


class SpaghettiDetectorRuntime:
    """Manage one camera/detector target."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.data = {**entry.data, **entry.options}
        self.detector_id: str = self.data[CONF_INSTANCE_ID]
        self.name = entry.title
        self.listeners: list[Callable[[], None]] = []
        self.unsubscribers: list[CALLBACK_TYPE] = []

        self.enabled = True
        self.running = False
        self.status = STATUS_IDLE
        self.printer_state: str | None = None
        self.confidence = 0.0
        self.raw_score = 0.0
        self.detection_count = 0
        self.detected = False
        self.warning = False
        self.last_run: datetime | None = None
        self.last_detected: datetime | None = None
        self.next_run: datetime | None = None
        self.last_error: str | None = None
        self.last_image_url: str | None = None
        self.last_result: dict[str, Any] = {"detections": []}
        self.lifetime_frames = 0
        self.detected_event_sent_for_active_period = False
        self.frames = FrameStore(
            hass,
            entry.entry_id,
            enabled=frames_enabled(self.data),
            limits=limits_from_options(self.data),
        )
        # When the print status sensor last entered an active state from a
        # known inactive one; a job-less frame after it starts a new print.
        self.frames_period_start: float | None = None
        self.last_frame: SaveResult | None = None
        # The outcome of the last completed check (a score or an error), so
        # the Last result sensor does not pass through checking every time.
        self.last_outcome: str = LAST_RESULT_NONE
        self.last_outcome_at: datetime | None = None
        self.last_outcome_confidence: float | None = None
        # The highest confidence of a scheduled check since the print
        # started. None until the first print start after installation; the
        # sensor restores it across restarts.
        self.peak_confidence: float | None = None
        self.peak_confidence_at: datetime | None = None
        # When a check last completed. Unlike last_outcome_at, reset() keeps
        # it, so pressing Reset during a print does not look like a stall.
        self.last_completed: datetime | None = None
        # Since when scheduled detection has been due without a break; None
        # while it is not due (switched off, not printing, light off).
        self.due_since: datetime | None = None
        self.stalled = False

    @property
    def active_states(self) -> set[str]:
        """Return states that mean the printer is actively printing."""
        return _parse_states(self.data.get(CONF_ACTIVE_PRINT_STATES))

    @property
    def warning_threshold(self) -> float:
        """Return warning threshold for this detector."""
        sensitivity = self.data.get(CONF_SENSITIVITY, DEFAULT_SENSITIVITY)
        default_warning, _ = SENSITIVITY_THRESHOLDS.get(
            sensitivity,
            SENSITIVITY_THRESHOLDS[DEFAULT_SENSITIVITY],
        )
        if sensitivity != "custom":
            return float(default_warning)
        return float(self.data.get(CONF_WARNING_THRESHOLD, default_warning))

    @property
    def failure_threshold(self) -> float:
        """Return failure threshold for this detector."""
        sensitivity = self.data.get(CONF_SENSITIVITY, DEFAULT_SENSITIVITY)
        _, default_failure = SENSITIVITY_THRESHOLDS.get(
            sensitivity,
            SENSITIVITY_THRESHOLDS[DEFAULT_SENSITIVITY],
        )
        if sensitivity != "custom":
            return float(default_failure)
        return float(self.data.get(CONF_FAILURE_THRESHOLD, default_failure))

    @property
    def cooldown(self) -> timedelta:
        """Return notification/action cooldown."""
        return timedelta(
            seconds=int(self.data.get(CONF_COOLDOWN_SECONDS, DEFAULT_COOLDOWN_SECONDS))
        )

    @property
    def detection_interval(self) -> timedelta:
        """Return scheduled detection interval."""
        return timedelta(
            seconds=int(
                self.data.get(CONF_DETECTION_INTERVAL, DEFAULT_DETECTION_INTERVAL)
            )
        )

    @property
    def light_control_mode(self) -> str:
        """Return how the detector should manage the configured light."""
        mode = self.data.get(CONF_LIGHT_CONTROL_MODE, DEFAULT_LIGHT_CONTROL_MODE)
        if mode in {LIGHT_CONTROL_OFF, LIGHT_CONTROL_LEAVE_ON, LIGHT_CONTROL_RESTORE}:
            return mode
        return DEFAULT_LIGHT_CONTROL_MODE

    @property
    def light_settle_seconds(self) -> int:
        """Return seconds to wait after turning on a light before snapshot."""
        try:
            seconds = int(
                float(
                    self.data.get(
                        CONF_LIGHT_SETTLE_SECONDS,
                        DEFAULT_LIGHT_SETTLE_SECONDS,
                    )
                )
            )
        except (TypeError, ValueError):
            seconds = DEFAULT_LIGHT_SETTLE_SECONDS
        return max(0, seconds)

    @property
    def light_gate_entity(self) -> str | None:
        """Return the light that must be on for scheduled checks, if any."""
        if not bool(self.data.get(CONF_REQUIRE_LIGHT, DEFAULT_REQUIRE_LIGHT)):
            return None
        return self.data.get(CONF_CHAMBER_LIGHT) or None

    @property
    def stall_after(self) -> timedelta:
        """Return how long detection may go without a completed check."""
        return max(
            timedelta(seconds=STALL_MIN_SECONDS),
            STALL_INTERVALS * self.detection_interval,
        )

    async def async_setup(self) -> None:
        """Start scheduled detection."""
        await self.frames.async_load()
        interval = self.detection_interval
        self.next_run = dt_util.utcnow() + interval
        self.unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._async_interval_update,
                interval,
            )
        )

        if status_entity := self.data.get(CONF_PRINT_STATUS_SENSOR):
            status_entities = [status_entity]
            if guard_entity := self._inferred_guard_entity(status_entity):
                status_entities.append(guard_entity)
            self.unsubscribers.append(
                async_track_state_change_event(
                    self.hass,
                    status_entities,
                    self._async_status_changed,
                )
            )

        if light_entity := self.light_gate_entity:
            self.unsubscribers.append(
                async_track_state_change_event(
                    self.hass,
                    [light_entity],
                    self._async_light_changed,
                )
            )

    async def async_unload(self) -> None:
        """Stop scheduled detection."""
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()
        self.listeners.clear()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Add a listener for runtime state changes."""
        self.listeners.append(listener)

        @callback
        def remove_listener() -> None:
            self.listeners.remove(listener)

        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Notify entities that runtime state changed."""
        self._update_stalled()
        for listener in list(self.listeners):
            listener()

    @callback
    def _update_stalled(self) -> None:
        """Work out whether scheduled detection is due but not completing.

        Reads the gates without touching the status. The clock starts at the
        later of when detection became due and the last completed check, so
        a print that just started, the light coming on or switching detection
        back on never counts as a stall.
        """
        if not self.enabled or self._schedule_gate()[0] is not None:
            self.due_since = None
            self.stalled = False
            return
        now = dt_util.utcnow()
        if self.due_since is None:
            self.due_since = now
        since = self.due_since
        if self.last_completed is not None and self.last_completed > since:
            since = self.last_completed
        self.stalled = now - since > self.stall_after

    @callback
    def async_set_enabled(self, enabled: bool) -> None:
        """Switch scheduled detection on or off."""
        self.enabled = enabled
        if not enabled:
            self.status = STATUS_DISABLED
            self._notify_listeners()
            return
        if self.status == STATUS_DISABLED:
            self.status = STATUS_IDLE
        # show a gate that holds detection back right away, not next tick
        self._should_run_scheduled()

    async def _async_interval_update(self, now: datetime) -> None:
        """Run detection on interval if the target is active."""
        self.next_run = now + self.detection_interval
        if not self.enabled:
            self.status = STATUS_DISABLED
            self._notify_listeners()
            return
        self._notify_listeners()
        if self._should_run_scheduled():
            await self.async_run_detection(manual=False)

    @callback
    def _async_status_changed(self, event) -> None:
        """Reset state when a new print starts."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        was_active = (
            old_state is not None
            and _normalize_state(old_state.state) in self.active_states
        )
        is_active = _normalize_state(new_state.state) in self.active_states
        # From a known inactive state: a print started. From no state,
        # unknown or unavailable it may be a restart or a reconnect instead.
        from_known_state = (
            old_state is not None
            and _normalize_state(old_state.state) not in UNKNOWN_STATES
        )
        if (
            is_active
            and not was_active
            and from_known_state
            and event.data.get("entity_id") == self.data.get(CONF_PRINT_STATUS_SENSOR)
        ):
            self.frames_period_start = dt_util.utcnow().timestamp()
        if was_active and not is_active:
            self.detected_event_sent_for_active_period = False
        if is_active and not was_active:
            if from_known_state:
                # A new print. From no state, unknown or unavailable it is the
                # printer coming back after a restart or a reconnect, and the
                # (restored) peak of the running print stays.
                self.peak_confidence = 0.0
                self.peak_confidence_at = None
            self.reset()
        else:
            # detection may have stopped or become due: re-evaluate the stall
            self._notify_listeners()

    @callback
    def _async_light_changed(self, event) -> None:
        """Re-evaluate the stall when the light that gates detection changes."""
        self._notify_listeners()

    def _should_run_scheduled(self) -> bool:
        """Return if scheduled detection should run.

        When it should not, the status says why.
        """
        if self.status == STATUS_UNAVAILABLE:
            # the guard's inactive state does not clear status_unavailable
            guard_status = STATUS_UNAVAILABLE
        else:
            guard_status = STATUS_WAITING_FOR_PRINT
        blocked, self.printer_state = self._schedule_gate()
        if blocked == _GUARD_INACTIVE:
            blocked = guard_status
        if blocked is not None:
            self.status = blocked
        self._notify_listeners()
        return blocked is None

    def _schedule_gate(self) -> tuple[str | None, str | None]:
        """Return why scheduled detection is held back, and the printer state.

        The reason is None when detection may run. Nothing is changed, so the
        stall check can call this on every update.
        """
        status_entity = self.data.get(CONF_PRINT_STATUS_SENSOR)
        printer_state: str | None = None
        if not status_entity:
            if not bool(self.data.get(CONF_RUN_WITHOUT_PRINTING, False)):
                return STATUS_WAITING_FOR_PRINT, None
        else:
            state = self.hass.states.get(status_entity)
            if state is None:
                return STATUS_UNAVAILABLE, None

            printer_state = str(state.state)
            normalized_state = _normalize_state(state.state)
            if normalized_state not in self.active_states:
                if normalized_state in UNKNOWN_STATES:
                    return STATUS_UNAVAILABLE, printer_state
                return STATUS_WAITING_FOR_PRINT, printer_state

            guard, printer_state = self._inferred_guard_gate(
                status_entity, printer_state
            )
            if guard is not None:
                return guard, printer_state

        # Checked after the print status and before any light control, so a
        # check held back here never turns the light on.
        if (light_entity := self.light_gate_entity) is not None:
            light = self.hass.states.get(light_entity)
            if light is None or _normalize_state(light.state) != "on":
                return STATUS_LIGHT_OFF, printer_state

        return None, printer_state

    def _inferred_guard_gate(
        self, status_entity: str, printer_state: str
    ) -> tuple[str | None, str]:
        """Check the inferred companion status, when there is one."""
        guard_entity = self._inferred_guard_entity(status_entity)
        if guard_entity is None:
            return None, printer_state

        state = self.hass.states.get(guard_entity)
        if state is None:
            return None, printer_state

        printer_state = f"{printer_state}; {guard_entity}={state.state}"
        normalized_state = _normalize_state(state.state)
        if normalized_state in UNKNOWN_STATES:
            return STATUS_UNAVAILABLE, printer_state
        if normalized_state not in self.active_states:
            return _GUARD_INACTIVE, printer_state
        return None, printer_state

    def _inferred_guard_entity(self, status_entity: str) -> str | None:
        """Infer an Elegoo companion current-status sensor when available."""
        suffix = "_print_status"
        if not status_entity.endswith(suffix):
            return None
        candidate = f"{status_entity[: -len(suffix)]}_current_status"
        if candidate == status_entity:
            return None
        return candidate

    def reset(self) -> None:
        """Reset detection state."""
        self.status = STATUS_IDLE if self.enabled else STATUS_DISABLED
        self.confidence = 0.0
        self.raw_score = 0.0
        self.detection_count = 0
        self.detected = False
        self.warning = False
        self.last_detected = None
        self.last_error = None
        self.last_result = {"detections": []}
        self.lifetime_frames = 0
        self.detected_event_sent_for_active_period = False
        self.last_outcome = LAST_RESULT_NONE
        self.last_outcome_at = None
        self.last_outcome_confidence = None
        self._notify_listeners()

    async def async_run_detection(self, *, manual: bool) -> dict[str, Any]:
        """Run one detection request."""
        if self.running:
            self.status = STATUS_BUSY
            self._notify_listeners()
            return self._service_result()

        if not manual and not self.enabled:
            self.status = STATUS_DISABLED
            self._notify_listeners()
            return self._service_result()

        if not manual and not self._should_run_scheduled():
            return self._service_result()

        self.running = True
        self.status = STATUS_CHECKING
        self.last_run = dt_util.utcnow()
        self.last_error = None
        self._notify_listeners()

        restore_light: str | None = None
        staged: StagedFrame | None = None
        job: tuple[str, str] = ("", "")
        try:
            restore_light = await self._async_prepare_light(manual=manual)
            if not manual and not self._should_run_scheduled():
                return self._service_result()

            image_url = self._build_image_url()
            if not image_url:
                self._set_error("camera_image_unavailable")
                return self._service_result()

            self.last_image_url = image_url
            self.last_frame = None

            predict_url = image_url
            if not manual and self.frames.enabled:
                job = self._current_job()
                staged = await self.frames.async_stage(
                    image_url, dt_util.utcnow().timestamp()
                )
                if staged is not None:
                    predict_url = self.frames.check_url(
                        self.data[CONF_HOME_ASSISTANT_HOST], staged
                    )

            try:
                ml_lock = self.hass.data[DOMAIN][RUNTIME_ML_LOCK]
                async with ml_lock:
                    result = await self._async_predict(predict_url)
            except (aiohttp.ClientError, TimeoutError) as err:
                self._set_error(str(err))
                LOGGER.warning(
                    "Obico ML request failed for %s: %s",
                    self.detector_id,
                    err,
                )
                return self._service_result()

            self.last_result = result
            self.raw_score, self.detection_count = _score_detections(result)
            self.confidence = self.raw_score
            self.warning = self.confidence >= self.warning_threshold
            self.detected = self.confidence >= self.failure_threshold
            self.status = (
                STATUS_DETECTED
                if self.detected
                else STATUS_WARNING
                if self.warning
                else STATUS_CLEAR
            )
            self.lifetime_frames += 1
            self._record_outcome(self.status, self.confidence)
            if not manual:
                self._raise_peak()

            if staged is not None:
                self.last_frame = await self.frames.async_commit(
                    staged,
                    job_id=job[0],
                    name=job[1],
                    score=self.raw_score,
                    detections=self.detection_count,
                    period_start=self.frames_period_start,
                )
                staged = None

            self._fire_result_event(manual)
            if self.detected and self._can_fire_detected_event(manual):
                self.last_detected = dt_util.utcnow()
                if not manual and self.data.get(CONF_PRINT_STATUS_SENSOR):
                    self.detected_event_sent_for_active_period = True
                self._fire_detected_event(manual)

            self._notify_listeners()
            return self._service_result()
        finally:
            if staged is not None:
                # no score for this frame: the check failed or was cancelled
                await self.frames.async_discard(staged)
            if restore_light is not None:
                await self._async_restore_light(restore_light)
            self.running = False

    def _record_outcome(self, outcome: str, confidence: float | None) -> None:
        """Remember the result of a check that completed."""
        now = dt_util.utcnow()
        self.last_outcome = outcome
        self.last_outcome_at = now
        self.last_outcome_confidence = confidence
        self.last_completed = now

    def _raise_peak(self) -> None:
        """Raise this print's peak confidence with a scheduled check's score.

        The peak stays unknown until a print start was seen.
        """
        if self.peak_confidence is None:
            return
        if self.peak_confidence_at is None or self.confidence > self.peak_confidence:
            self.peak_confidence = max(self.peak_confidence, self.confidence)
            self.peak_confidence_at = self.last_outcome_at

    def _current_job(self) -> tuple[str, str]:
        """Return the job id and job name from the configured entities."""
        values: list[str] = []
        for key in (CONF_JOB_ID_ENTITY, CONF_JOB_NAME_ENTITY):
            entity_id = self.data.get(key)
            state = self.hass.states.get(entity_id) if entity_id else None
            values.append(state.state if state is not None else "")
        return safe_job_id(values[0]), job_name(values[1])

    async def async_delete_frames(self, print_id: str) -> bool:
        """Delete the frames kept for one print."""
        deleted = await self.frames.async_delete(print_id)
        if deleted:
            self._notify_listeners()
        return deleted

    async def _async_predict(self, image_url: str) -> dict[str, Any]:
        """Call the Obico ML API."""
        session = async_get_clientsession(self.hass)
        async with session.get(
            f"{self.data[CONF_OBICO_HOST].rstrip('/')}/p/",
            params={"img": image_url},
            headers={"Authorization": f"Bearer {self.data[CONF_OBICO_AUTH_TOKEN]}"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status >= 400:
                error_message = await _response_error_message(response)
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message=error_message,
                    headers=response.headers,
                )
            response.raise_for_status()
            result = await response.json()
            if not isinstance(result, dict):
                return {"detections": []}
            return result

    async def _async_prepare_light(self, *, manual: bool) -> str | None:
        """Prepare the configured light before detection.

        Returns the entity ID to restore when the integration turned an off
        light on and the selected mode wants the previous state restored.
        """
        if self.light_control_mode == LIGHT_CONTROL_OFF:
            return None
        if not manual and self.light_gate_entity is not None:
            # The light gate holds scheduled checks back while the light is
            # off; such a check must never be the one to switch it on.
            return None

        light_entity = self.data.get(CONF_CHAMBER_LIGHT)
        if not light_entity:
            return None
        state = self.hass.states.get(light_entity)
        if state is None or _normalize_state(state.state) != "off":
            return None

        try:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": light_entity},
                blocking=True,
            )
        except HomeAssistantError as err:
            LOGGER.warning("Could not turn on light %s: %s", light_entity, err)
            return None

        restore_light = (
            light_entity if self.light_control_mode == LIGHT_CONTROL_RESTORE else None
        )

        try:
            if self.light_settle_seconds:
                await asyncio.sleep(self.light_settle_seconds)
        except asyncio.CancelledError:
            if restore_light is not None:
                await self._async_restore_light(restore_light)
            raise

        return restore_light

    async def _async_restore_light(self, light_entity: str) -> None:
        """Restore a light that the detector temporarily turned on."""
        state = self.hass.states.get(light_entity)
        if state is not None and _normalize_state(state.state) == "off":
            return

        try:
            await self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": light_entity},
                blocking=True,
            )
        except HomeAssistantError as err:
            LOGGER.warning("Could not restore light %s: %s", light_entity, err)

    def _build_image_url(self) -> str | None:
        """Build a snapshot URL for the configured camera."""
        if snapshot_url := self.data.get(CONF_SNAPSHOT_URL):
            return snapshot_url

        camera_entity = self.data.get(CONF_CAMERA)
        state = self.hass.states.get(camera_entity)
        if state is None:
            return None
        entity_picture = state.attributes.get("entity_picture")
        if not entity_picture:
            return None
        return f"{self.data[CONF_HOME_ASSISTANT_HOST].rstrip('/')}{entity_picture}"

    def _cooldown_elapsed(self) -> bool:
        """Return whether a detected event can be fired."""
        if self.last_detected is None:
            return True
        return dt_util.utcnow() - self.last_detected >= self.cooldown

    def _can_fire_detected_event(self, manual: bool) -> bool:
        """Return whether the detected event should be emitted."""
        if not manual and self.data.get(CONF_PRINT_STATUS_SENSOR):
            return not self.detected_event_sent_for_active_period
        return self._cooldown_elapsed()

    def _event_data(self, manual: bool) -> dict[str, Any]:
        """Return event payload."""
        data: dict[str, Any] = {
            "config_entry": self.entry.entry_id,
            "detector": self.detector_id,
            "name": self.name,
            "camera": self.data.get(CONF_CAMERA),
            "manual": manual,
            "printer_state": self.printer_state,
            ATTR_CONFIDENCE: self.confidence,
            ATTR_RAW_SCORE: self.raw_score,
            ATTR_DETECTED: self.detected,
            ATTR_DETECTIONS: self.detection_count,
            ATTR_IMAGE_URL: self.last_image_url,
            ATTR_LAST_ERROR: self.last_error,
            ATTR_LAST_RUN: self.last_run.isoformat() if self.last_run else None,
            ATTR_NEXT_RUN: self.next_run.isoformat() if self.next_run else None,
            ATTR_STATUS: self.status,
        }
        if self.last_frame is not None:
            data[ATTR_FRAME] = self.last_frame.frame
            data[ATTR_PRINT_ID] = self.last_frame.print_id
        return data

    def _fire_result_event(self, manual: bool) -> None:
        """Fire an event for every detection result."""
        self.hass.bus.async_fire(EVENT_DETECTION_RESULT, self._event_data(manual))

    def _fire_detected_event(self, manual: bool) -> None:
        """Fire an event when spaghetti is detected."""
        self.hass.bus.async_fire(EVENT_SPAGHETTI_DETECTED, self._event_data(manual))

    def _service_result(self) -> dict[str, Any]:
        """Return service response payload."""
        return {
            "result": self.last_result,
            ATTR_CONFIDENCE: self.confidence,
            ATTR_RAW_SCORE: self.raw_score,
            ATTR_DETECTED: self.detected,
            ATTR_DETECTIONS: self.detection_count,
            ATTR_IMAGE_URL: self.last_image_url,
            ATTR_LAST_ERROR: self.last_error,
            ATTR_NEXT_RUN: self.next_run.isoformat() if self.next_run else None,
            ATTR_STATUS: self.status,
        }

    def _set_error(self, error: str) -> None:
        """Set a runtime error and notify listeners."""
        self.status = STATUS_ERROR
        self.last_error = error
        self.detected = False
        self.warning = False
        self.confidence = 0.0
        self.raw_score = 0.0
        self.detection_count = 0
        self._record_outcome(STATUS_ERROR, None)
        self._notify_listeners()


async def _response_error_message(response: aiohttp.ClientResponse) -> str:
    """Return a useful error message from an ML server error response."""
    try:
        payload = await response.json()
    except (aiohttp.ContentTypeError, ValueError):
        return await response.text()
    if not isinstance(payload, dict):
        return str(payload)
    if error := payload.get("error"):
        message = payload.get("message")
        return f"{error}: {message}" if message else str(error)
    return str(payload)
