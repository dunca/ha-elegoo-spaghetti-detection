# Changelog

All notable changes to this project are documented in this file.

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
