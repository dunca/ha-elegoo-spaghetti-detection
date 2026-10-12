# Elegoo Spaghetti Detection

Home Assistant spaghetti/failure detection with an Elegoo Centauri Carbon-first setup, designed around [`danielcherubini/elegoo-homeassistant`](https://github.com/danielcherubini/elegoo-homeassistant).
Other printers or workflows can also be used when they expose a Home Assistant camera entity, optional light entity, and user-defined automations, but they are not the primary target.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hepter&repository=ha-elegoo-spaghetti-detection&category=integration)

<p>
  <img src="docs/images/camera-spaghetti-failure.png" alt="Camera frame with spaghetti failure" width="720">
</p>

The integration turns camera snapshots into Home Assistant entities and events.
Printer actions such as pause, stop, resume, and notifications stay in your own
automations, so the same detector can be used with different printer setups.

## Quick Start

1. Run the ML server. See [ML server and logs](docs/ml-server.md).
2. Install the custom integration through HACS or manually. See
   [Installation](docs/installation.md).
3. Add `Elegoo Spaghetti Detection` from Home Assistant integrations.
4. Select the camera and optional print status sensor. See
   [Configuration](docs/configuration.md).
5. Press `Test Spaghetti Detection`.
6. Add one of the [automation examples](docs/automations.md).
7. Add one of the [dashboard examples](docs/dashboard.md).

## Documentation

- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Automation examples](docs/automations.md)
- [Dashboard examples](docs/dashboard.md)
- [ML server and logs](docs/ml-server.md)
- [Changelog](CHANGELOG.md)
- [Troubleshooting](docs/troubleshooting.md)
- [HACS publishing notes](docs/HACS_PUBLISHING.md)

## How It Works

```text
camera snapshot -> ML server -> confidence/result sensors -> Home Assistant events
```

The integration detects possible print failures and exposes Home Assistant
entities/events. It does not directly control the printer.

When a print status sensor is configured, scheduled detection only runs during
the configured active print states. Elegoo `print_status` sensors are also
guarded by the companion `current_status` sensor when it exists, which avoids
scheduled checks during homing or idle states where `print_status` can remain
`printing`.

## Features

- Works with Home Assistant camera entities, including Elegoo chamber cameras.
- Uses an Obico/TSD FDM failure model running in a local Docker/HA add-on server.
- Validates ML server health and camera image reachability during setup.
- Optional print status sensor gates scheduled detection to active print states.
- Optional chamber light control can leave the light alone, turn it on and keep
  it on, or temporarily turn it on and restore the previous state after each
  snapshot.
- Optional light gate: scheduled checks only while the chamber light is on.
- `Detection` switch to pause scheduled checks, remembered across restarts.
- Manual `Test Spaghetti Detection` button.
- Check frames: the exact camera frame behind every scheduled check is kept per
  print, with a viewer, a Media panel folder and a `Spaghetti Frames` sensor.
  See [Check frames](#check-frames).
- Confidence, raw score, detection count, status, last result, peak confidence,
  last run, next run, and last error sensors.
- `binary_sensor.<prefix>_spaghetti_detected` and
  `binary_sensor.<prefix>_detection_stalled`.
- Events for every result and for detected failures.
- ML server web dashboard, JSON status, recent request logs, and image debug
  endpoint.
- CPU-first ML startup by default to avoid CUDA timeout failures on systems
  without a working GPU runtime.

## Entities

`<prefix>` is the entity prefix chosen at setup, for example
`elegoo_spaghetti_detection`.

| Entity | What it shows |
| --- | --- |
| `sensor.<prefix>_status` | What the detector is doing right now. See [Status values](#status-values). |
| `sensor.<prefix>_last_result` | The outcome of the last completed check: `clear`, `warning`, `detected` or `error`; `none` before the first check, after a reset and when a print starts. Unlike Status it does not pass through `checking` every interval, so a dashboard showing it stays steady. Attributes: `at` (when that check finished) and `confidence` (%, scored checks only). Manual checks count. |
| `sensor.<prefix>_peak_confidence` | The highest confidence (%) of a scheduled check since the current or last print started, with an `at` attribute for when it was seen. It goes to 0 when a print starts and is kept across Home Assistant restarts. Manual checks do not count. Unknown until the first print start after installation. |
| `binary_sensor.<prefix>_detection_stalled` | A problem sensor: on when detection is switched on and should be running (printing, and the chamber light on if required) but no check has finished, with a score or an error, for longer than three detection intervals (at least 90 seconds). Catches a hung ML server or camera. Never on while switched off, not printing or held back by the light. |
| `switch.<prefix>_detection` | Turns scheduled checks on and off. The state is remembered across restarts. While off, Status shows `disabled`; the Test button still runs a check. |
| `binary_sensor.<prefix>_spaghetti_detected` | On when the last check was above the failure threshold. |
| `sensor.<prefix>_confidence`, `_raw_score`, `_detections` | The score of the last check. |
| `sensor.<prefix>_last_run`, `_next_run`, `_last_error` | When the last check ran, when the next tick is, and the last error. |
| `sensor.<prefix>_spaghetti_frames` | Stored prints with check frames. See [Check frames](#check-frames). |
| `button.<prefix>_test_spaghetti_detection`, `_reset_detection_state` | Run one check now; clear the result. |

### Status values

`sensor.<prefix>_status` is an enum sensor: Home Assistant shows a readable,
translated label, while templates and automations keep seeing the raw value.

| Value | Label |
| --- | --- |
| `idle` | Idle |
| `waiting_for_print` | Standby – not printing |
| `status_unavailable` | Waiting for printer |
| `light_off` | Off – chamber light is off |
| `disabled` | Off – detection switched off |
| `checking` | Checking frame… |
| `clear` | Watching – all clear |
| `warning` | Watching – possible issue |
| `detected` | Spaghetti detected |
| `error` | Error |
| `busy` | Busy |

### Only check while the chamber light is on

With this option on (it is off by default) and a `Chamber light` selected,
scheduled checks run only while that light is `on`. Otherwise the check is
skipped and Status shows `light_off`. The option is checked after the print
status and before `Light control`, so a skipped check never turns the light on.
Without a chamber light the option has no effect. The Test button, and the
`run_detection` service with `force: true`, ignore it.

## Screenshots

Integration setup:

<p>
  <img src="docs/images/config-flow-add-hub.png" alt="Elegoo Spaghetti Detection setup form" width="720">
</p>

Enhanced dashboard in idle state:

<p>
  <img src="docs/images/dashboard-hacs-waiting-for-print.png" alt="Enhanced dashboard idle state" width="720">
</p>

Enhanced dashboard after a detected failure:

<p>
  <img src="docs/images/dashboard-hacs-detected.png" alt="Enhanced dashboard detected failure" width="720">
</p>

## Typical Elegoo CC2 Entities

Your entity IDs depend on the printer/device name in Home Assistant. With a
default-ish Elegoo Centauri Carbon 2 setup they often look like:

```text
camera.elegoo_centauri_carbon2_chamber_camera
sensor.elegoo_centauri_carbon2_print_status
light.elegoo_centauri_carbon2_chamber_light
button.elegoo_centauri_carbon2_pause_print
button.elegoo_centauri_carbon2_resume_print
button.elegoo_centauri_carbon2_stop_print
```

The integration setup uses only camera, optional print status, and optional
light. Pause/stop/resume are shown only in automation examples.

## Events

Every detection result fires:

```text
elegoo_spaghetti_detection_result
```

Detected failures fire:

```text
elegoo_spaghetti_detection_detected
```

When a print status sensor is configured, scheduled detected events are emitted
once per active print window. If the printer leaves the configured active states
and later returns to an active state, a new detected failure can emit one new
event.

Use this event for notification, pause, and stop automations.

`elegoo_spaghetti_detection_result` still fires for every completed check. Use
that event for logging, dashboards, or advanced automations only; notification
automations based on the result event can repeat every detection interval.

Event data includes:

```text
config_entry
detector
name
camera
manual
printer_state
confidence
raw_score
detected
detections
image_url
last_error
last_run
next_run
status
```

When a scheduled check kept its frame (see [Check frames](#check-frames)), the
event data also has:

```text
frame       file name of the kept frame, for example 142233_04512.jpg
print_id    the print it was filed under (the job id when one is known)
```

Use these fields in notifications and advanced automations.

## Check frames

After a failed print it helps to see what the model saw. With `Keep check
frames` on (the default), every scheduled check keeps its camera frame, filed
per print, so you can step through a print's checks afterwards.

How a scheduled check works with frames on:

```text
light on (if configured) -> fetch the camera image once (10 s, no retry)
-> save it -> Obico reads the saved copy from Home Assistant -> score
-> file the frame under its print, named by time and score
```

The ML server is handed a Home Assistant URL for the saved copy, built from
`Home Assistant Host`, so the frame kept is exactly the frame that was scored.
The printer still sees one image request per check. If the image cannot be
fetched or saved, the check falls back to the old behaviour (the ML server
fetches the camera URL itself) and no frame is kept for that check. Manual
checks are never kept. With frames off, checks run exactly as before.

- **Prints:** with a `Job ID entity` (for example a printer's task id sensor),
  frames are filed under the job id. Without one, a new print starts when the
  print status sensor becomes active after being inactive, or after 15 minutes
  without a frame. A print that started without a job id is renamed to the job
  id if one shows up while it is still current.
- **Limits:** the last 10 prints, 1,500 frames per print (past that every other
  frame scored below 10 % is dropped, so a long print keeps its whole timeline
  at a coarser step) and 1,500 MB per detector, oldest prints first. All are
  options.
- **Storage:** `<config>/elegoo_spaghetti_detection/frames/<entry_id>/<print_id>/`,
  outside `www`, with an `index.json` per print. Removing a detector removes
  its frames.
- **Sensor:** `sensor.<prefix>_spaghetti_frames` counts the stored prints. Its
  `prints` attribute lists them newest first, each with a `viewer_url`; see
  [Dashboard examples](docs/dashboard.md#check-frames).
- **Viewer:** the frame with its time and score, a clickable score strip with
  the detector's warning and failure lines, a slider, play, "Highest",
  ←/→/space, a picker for the other prints; it follows a print that is still
  running.
- **Media panel:** *Media -> Spaghetti frames -> (detector) -> (print)*.
- **Delete:** `elegoo_spaghetti_detection.delete_frames` with a `print_id`.
- **Access:** frames are served only to signed-in users, or with an access
  token that is random per Home Assistant run. The token is only in the
  sensor's attributes (which are not written to the recorder database), the
  viewer and the URL handed to the ML server. Viewer links stop working when
  Home Assistant restarts; open them from the dashboard rather than
  bookmarking them.

Details and options: [Configuration](docs/configuration.md#check-frames).

## Credits

This project started as an Elegoo-focused adaptation of
[`nberktumer/ha-bambu-lab-p1-spaghetti-detection`](https://github.com/nberktumer/ha-bambu-lab-p1-spaghetti-detection).
The original project provided the Obico ML workflow and Home Assistant
integration foundation.
