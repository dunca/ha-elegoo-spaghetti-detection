# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

These replace Home Assistant automations and template sensors that were needed
around the detector, so a dashboard and a stall alert work out of the box.

### Added

- Option `Only check while the chamber light is on` (off by default). With a
  chamber light selected, scheduled checks run only while it is on, because
  frames of a dark chamber give meaningless scores. A skipped check shows the
  new status `light_off` and never turns the light on, whatever `Light
  control` is set to. Manual checks still run.
- `switch.<prefix>_detection` to pause scheduled checks, for example during a
  print you are watching yourself, without disabling the integration. It is
  remembered across restarts. While off, the status is the new value
  `disabled`; the Test button still works.
- `sensor.<prefix>_last_result`: the outcome of the last completed check
  (`clear`, `warning`, `detected`, `error`, or `none` before the first one),
  with `at` and `confidence` attributes. The Status sensor passes through
  `checking` on every interval, which made dashboards flicker; this one only
  changes when a check finishes. It goes back to `none` on reset and when a
  print starts.
- `sensor.<prefix>_peak_confidence`: the highest confidence of a scheduled
  check since the print started, with the time it was seen, to tell after a
  print how close it came to a warning. It goes to 0 when a print starts and
  survives Home Assistant restarts; it is unknown until the first print start
  after updating.
- `binary_sensor.<prefix>_detection_stalled`: on when detection should be
  running but no check has finished for longer than three detection intervals
  (at least 90 seconds), for example when the ML server or the camera hangs.
  It stays off while detection is switched off, held back by the light or
  not printing, and does not fire at the start of a print.

### Changed

- `sensor.<prefix>_status` is now an enum sensor with translated labels, so
  Home Assistant shows "Standby – not printing" or "Watching – all clear"
  instead of raw values. The entity id and the raw values used by templates
  and automations are unchanged; `light_off` and `disabled` are new.
- `run_detection` with `force: false` now also respects the Detection switch
  and the light option, like a scheduled check.

## [1.1.0] - 2026-10-11

### Added

- Check frames: every scheduled check keeps the exact camera frame the model
  scored, filed per print under
  `<config>/elegoo_spaghetti_detection/frames/<entry_id>/<print_id>/`. The
  detector fetches the image once (10 s timeout, no retry, 5 MB cap, JPEG
  only), saves it, and hands the ML server a Home Assistant URL for the saved
  copy, so the printer still sees one image request per check. If the fetch
  or the save fails, the check runs as before and keeps no frame. Manual
  checks are not kept.
- Options: `Keep check frames` (on), `Job ID entity`, `Job name entity`,
  `Frames: prints to keep` (10), `Frames: frames per print` (1500; past that
  every other frame scored below 10 % is dropped) and `Frames: total size`
  (1500 MB).
- Prints are grouped by the job id entity when set; otherwise a new print
  starts when the print status sensor becomes active or after 15 minutes
  without a frame.
- `sensor.<prefix>_spaghetti_frames`: the number of stored prints, with a
  `prints` attribute (newest first: `id`, `job_id`, `name`, `start`, `end`,
  `frames`, `max`, `viewer_url`) and a `viewer_url` attribute. The attributes
  are not recorded.
- A frame viewer (score strip, slider, playback, "Highest", keyboard keys) and
  a *Spaghetti frames* folder in the Media panel.
- `elegoo_spaghetti_detection.delete_frames` service.
- `frame` and `print_id` in the `elegoo_spaghetti_detection_result` and
  `elegoo_spaghetti_detection_detected` event data when a frame was kept.
- Tests (pytest with pytest-homeassistant-custom-component) and a CI job that
  runs them.

### Changed

- The integration now depends on `http` (for the frame views) and loads after
  `media_source`.
- With `Keep check frames` on, the ML server must be able to reach `Home
  Assistant Host`, also when a `Direct snapshot URL` is set. Turn the option
  off to keep the previous behaviour exactly.
