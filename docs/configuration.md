# Configuration

Open:

```text
Settings -> Devices & services -> Add integration -> Elegoo Spaghetti Detection
```

![Elegoo Spaghetti Detection setup form](images/config-flow-add-hub.png)

The setup form creates one detector. Add another detector for another camera.
Existing detector settings are reused as defaults to reduce repeated server
entry.

## Fields

| Field | Notes |
| --- | --- |
| `Detector name` | Display name for this camera/detector. |
| `Entity prefix` | Stable entity ID prefix, for example `elegoo_spaghetti_detection` or `elegoo_cc2_left`. |
| `Home Assistant Host` | URL reachable by the ML server. For Docker on another LAN host, prefer the HA LAN URL, for example `http://192.168.1.90:8123`. Do not use `homeassistant.local` unless the Docker host can resolve mDNS. |
| `Obico ML API Host` | Base URL of this project's ML server, for example `http://192.168.1.100:3333`. Do not enter `/hc/` or `/p/`. |
| `Obico ML API Auth Token` | Must match `ML_API_TOKEN` / `obico_api_secret` configured on the ML server. |
| `Camera` | Any HA camera entity. Example: `camera.elegoo_centauri_carbon2_chamber_camera`. Your device name may differ. |
| `Direct snapshot URL` | Optional. Leave empty to use the selected camera's HA camera proxy image. Use this only for unusual camera integrations. |
| `Print status sensor` | Optional but recommended. Example: `sensor.elegoo_centauri_carbon2_print_status`. Scheduled detection runs only when this entity is in an active print state. For Elegoo-style entity names, a matching `sensor.<printer>_current_status` is used automatically as an extra guard; you do not select it separately. |
| `Active print states` | Comma-separated states that mean printing. For Elegoo CC2, `printing` is usually enough. You can use `printing,printing_recovery`. |
| `Chamber light` | Optional. Example: `light.elegoo_centauri_carbon2_chamber_light`. |
| `Only check while the chamber light is on` | Off by default. With a chamber light selected, scheduled checks run only while it is `on`; otherwise they are skipped and the status shows `light_off`. Checked before `Light control`, so a skipped check never turns the light on. No effect without a chamber light. Manual checks ignore it. |
| `Light control` | `Do not control light`, `Turn on before detection and leave on`, or `Restore previous state after detection`. Restore mode only turns the light off again when it was off before this detection cycle. |
| `Light settle delay` | Seconds to wait after the integration turns on an off light before taking the snapshot. Default is `3`; useful for camera exposure/focus. |
| `Run scheduled detection without print status` | Keep off unless this detector is camera-only and has no print status entity. |
| `Detection interval` | Seconds between scheduled checks while the print status is active. Examples: `600` for 10 minutes, `900` for 15 minutes. |
| `Sensitivity` | High, normal, low, or custom thresholds. |
| `Warning/Failure threshold` | Used when sensitivity is `Custom thresholds`. |
| `Detected event cooldown` | Minimum seconds between detected events. Default is `900` seconds. Scheduled checks with a print status sensor use the active print window limit instead, so this is mainly a fallback for camera-only setups and manual testing. |
| `Keep check frames` | On by default. Keeps the camera frame behind every scheduled check, per print. See [Check frames](#check-frames). |
| `Job ID entity` | Optional. An entity whose state is the current job id, for example a printer's task id sensor. Frames of one job are filed together under that id. |
| `Job name entity` | Optional. An entity whose state is the current job or file name, shown in the viewer and the Media panel. |
| `Frames: prints to keep` | Default `10`. The oldest prints are removed first. |
| `Frames: frames per print` | Default `1500`. Past this, every other frame scored below 10 % is dropped. |
| `Frames: total size` | Default `1500` MB per detector. The oldest prints are removed first. |

## Print-State Guarding

The setup form asks for one status entity: `Print status sensor`.

For Elegoo printers, select the `print_status` entity:

```text
sensor.elegoo_centauri_carbon2_print_status
```

If the selected entity ends with `_print_status`, the integration automatically
checks for a sibling `_current_status` entity:

```text
sensor.elegoo_centauri_carbon2_current_status
```

There is no separate field for `current_status`. It is an automatic fallback
guard. Scheduled detection runs only when the selected `print_status` is active
and the inferred `current_status`, when present, is also active.

Example:

```text
print_status = printing
current_status = idle
result: scheduled detection does not run
```

This protects against printer/integration states where `print_status` remains
`printing` during homing, idle, or other non-print movement states.

## Repeated Notification Guarding

When a `Print status sensor` is selected, scheduled detection emits at most one
`elegoo_spaghetti_detection_detected` event while the printer remains in one of
the configured `Active print states`. If the selected status sensor or the
automatic `current_status` guard leaves the active states and later returns to
an active state, a new failure can emit one new detected event.

This is separate from the fallback guard above. The fallback guard decides
whether scheduled detection should run. The repeated-notification guard decides
whether a detected result should fire another notification/action event.

## Example Elegoo CC2 Values

These are examples from one `elegoo-homeassistant` install. Your entity IDs may
change if the Home Assistant device name differs.

```text
Entity prefix: elegoo_spaghetti_detection
Home Assistant Host: http://192.168.1.90:8123
Obico ML API Host: http://192.168.1.100:3333
Obico ML API Auth Token: obico_api_secret
Camera: camera.elegoo_centauri_carbon2_chamber_camera
Print status sensor: sensor.elegoo_centauri_carbon2_print_status
Active print states: printing
Chamber light: light.elegoo_centauri_carbon2_chamber_light
Light control: Restore previous state after detection
Light settle delay: 3
```

With restore mode, a scheduled check does this:

```text
light was off -> turn on -> wait -> snapshot/detect -> turn off
light was on  -> snapshot/detect -> keep on
```

## Created Entities

With prefix `elegoo_spaghetti_detection`, the integration creates:

```text
binary_sensor.elegoo_spaghetti_detection_spaghetti_detected
binary_sensor.elegoo_spaghetti_detection_detection_stalled
sensor.elegoo_spaghetti_detection_confidence
sensor.elegoo_spaghetti_detection_raw_score
sensor.elegoo_spaghetti_detection_detections
sensor.elegoo_spaghetti_detection_status
sensor.elegoo_spaghetti_detection_last_result
sensor.elegoo_spaghetti_detection_peak_confidence
sensor.elegoo_spaghetti_detection_last_error
sensor.elegoo_spaghetti_detection_last_run
sensor.elegoo_spaghetti_detection_next_run
sensor.elegoo_spaghetti_detection_spaghetti_frames
button.elegoo_spaghetti_detection_test_spaghetti_detection
button.elegoo_spaghetti_detection_reset_detection_state
switch.elegoo_spaghetti_detection_detection
```

What the newer entities show is described in the
[README](../README.md#entities).

Press `Test Spaghetti Detection` to run one check immediately from the current
camera image, even if the printer is not printing.

## Check frames

With `Keep check frames` on, a scheduled check:

1. prepares the chamber light, as before;
2. builds the image URL, as before (`Direct snapshot URL`, or the camera's Home
   Assistant camera proxy URL);
3. fetches that image once with Home Assistant's shared HTTP session (10 s
   timeout, no retry, at most 5 MB, must be a JPEG);
4. saves it under the detector's frames folder;
5. asks the ML server to score
   `<Home Assistant Host>/api/elegoo_spaghetti_detection/frames/<entry_id>/check_<random>.jpg?token=<token>`,
   the saved copy;
6. files the frame under its print as `HHMMSS_SSSSS.jpg` (local time, score
   times 10000) and adds `frame` and `print_id` to the result event.

If step 3 or 4 fails, the ML server is given the original image URL, as
before, and no frame is kept for that check. If the ML server fails, the saved
copy is deleted. Manual checks (the Test button, or `run_detection` with
`force: true`) are not kept. With `Keep check frames` off, checks are exactly
as before.

The ML server must be able to reach `Home Assistant Host`. With the camera
proxy this was already needed; with a `Direct snapshot URL`, make sure the ML
server can reach Home Assistant too, or turn `Keep check frames` off.

### Which print a frame belongs to

| Situation | Print |
| --- | --- |
| `Job ID entity` has a job id (letters, digits, `-`, `_`, up to 64; not `unknown`/`unavailable`) | The job id. |
| No job id, and the latest print had a frame in the last 15 minutes and since the print status sensor last became active | That print. |
| Otherwise | A new print, `print-<UTC yyyymmdd-hhmmss>`. |

"Became active" means the selected `Print status sensor` changed from a
non-active state (for example `idle` or `paused`) to one of the `Active print
states`. A change from `unknown` or `unavailable` (a restart or reconnect) does
not start a new print. Without a job id entity a pause and resume therefore
starts a new print; set a `Job ID entity` to keep a whole job together.

A print that started without a job id is renamed to the job id when one shows
up while that print is still current, so its first frames are not orphaned.

### Limits

Per detector: the last `Frames: prints to keep` prints; at most `Frames: frames
per print` frames per print, past which every other frame scored below 10 % is
dropped (never the newest), so a long print keeps its whole timeline at a
coarser step; and `Frames: total size` on disk, oldest prints first. The print
being written is never removed.

### Storage

```text
<config>/elegoo_spaghetti_detection/frames/<entry_id>/<print_id>/HHMMSS_SSSSS.jpg
<config>/elegoo_spaghetti_detection/frames/<entry_id>/<print_id>/index.json
```

`index.json` holds `start`, `end`, `entry_id`, `task` (the job id), `name` and
`frames`, a list of `{"t": time, "f": file, "s": score, "n": detections}`.
The folder is outside `www`. Removing the detector removes its frames.

### Viewing and deleting

- `sensor.<prefix>_spaghetti_frames`: state is the number of stored prints.
  Attributes (not written to the recorder database):
  - `prints`: newest first, each
    `{id, job_id, name, start, end, frames, max, viewer_url}`;
  - `viewer_url`: the viewer for all prints.
- Viewer: open a `viewer_url`. See [Dashboard examples](dashboard.md#check-frames).
- Media panel: *Media -> Spaghetti frames*.
- `elegoo_spaghetti_detection.delete_frames` deletes one print:

  ```yaml
  action: elegoo_spaghetti_detection.delete_frames
  data:
    detector: elegoo_spaghetti_detection
    print_id: print-20261011-093000
  ```

Frames are served to signed-in users, or with an access token that is random
per Home Assistant run and appears only in the sensor's attributes, the viewer
and the URL handed to the ML server. A viewer link stops working when Home
Assistant restarts; open it from the dashboard again.
