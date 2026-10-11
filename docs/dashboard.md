# Dashboard Examples

These examples create a small Home Assistant dashboard section for one
spaghetti detector.

Replace entity IDs if your detector prefix, camera, printer, or automation names
are different. The examples use:

```text
camera.elegoo_centauri_carbon2_chamber_camera
sensor.elegoo_centauri_carbon2_print_status
sensor.elegoo_spaghetti_detection_status
sensor.elegoo_spaghetti_detection_next_run
sensor.elegoo_spaghetti_detection_last_run
sensor.elegoo_spaghetti_detection_last_error
sensor.elegoo_spaghetti_detection_confidence
sensor.elegoo_spaghetti_detection_raw_score
sensor.elegoo_spaghetti_detection_detections
binary_sensor.elegoo_spaghetti_detection_spaghetti_detected
button.elegoo_spaghetti_detection_test_spaghetti_detection
button.elegoo_spaghetti_detection_reset_detection_state
```

The enhanced example also references this placeholder automation:

```text
automation.elegoo_cc2_spaghetti_pause_and_notify
```

Replace it with your own notification or pause automation entity.
The toggle card in `dashboard_hacs.yaml` is only a placeholder until you make
that replacement.

## Status Labels

The dashboard examples render raw detector states as user-facing English labels:

| Raw state | Dashboard label |
| --- | --- |
| `clear` | Clear |
| `detected` | Failure detected |
| `warning` | Warning |
| `checking` | Checking |
| `waiting_for_print` | Waiting for print |
| `status_unavailable` | Print status unavailable |
| `busy` | Busy |
| `error` | Error |
| `idle` | Idle |

This avoids a mixed display where Home Assistant shows an unknown printer state
while the detector correctly reports `waiting_for_print`.

## Core Home Assistant Example

Use this when you do not want extra Lovelace dependencies:

- [examples/dashboard_core.yaml](../examples/dashboard_core.yaml)

It uses only built-in cards:

- `picture-entity`
- `markdown`
- `gauge`
- `conditional`
- `button`
- `entities`

## Enhanced HACS Example

Use this when custom Lovelace cards are allowed:

- [examples/dashboard_hacs.yaml](../examples/dashboard_hacs.yaml)

Idle state:

![Enhanced dashboard idle state](images/dashboard-hacs-waiting-for-print.png)

Detected failure state:

![Enhanced dashboard detected failure](images/dashboard-hacs-detected.png)

Recommended custom cards:

- `custom:button-card`
- `custom:mushroom-template-card`
- `card_mod`

Install those through HACS before pasting the enhanced YAML. The enhanced
version adds a compact status panel, better visual states, responsive metric
tiles, and an alert card when spaghetti is detected.

## Next Scheduled Check

The integration exposes:

```text
sensor.<prefix>_next_run
```

For the default prefix this is:

```text
sensor.elegoo_spaghetti_detection_next_run
```

The value is updated when the integration starts and every time the scheduled
interval fires. It represents the next scheduled interval tick. If the print
status is not active, the detector still waits at that tick and keeps the status
as `waiting_for_print`.

## Check frames

`sensor.<prefix>_spaghetti_frames` lists the stored prints, newest first, in
its `prints` attribute:

```yaml
prints:
  - id: 4c0b5e2a-7f0e-4d1b-9a53-0d6c1f2e9b11
    job_id: 4c0b5e2a-7f0e-4d1b-9a53-0d6c1f2e9b11
    name: ECC2_0.4_Cube_PLA_0.2_1h2m.gcode
    start: "2026-10-11T09:30:02+02:00"
    end: "2026-10-11T10:31:47+02:00"
    frames: 370
    max: 0.4512
    viewer_url: /api/elegoo_spaghetti_detection/frames/<entry_id>/viewer?token=...&print=4c0b5e2a-...
viewer_url: /api/elegoo_spaghetti_detection/frames/<entry_id>/viewer?token=...
```

`max` is the highest score of the print (0 to 1). `job_id` and `name` are
`null` when no job entity is set. The token in the URLs changes when Home
Assistant restarts, so always read the URLs from the sensor rather than copying
them into a dashboard.

### Find a print's frames by job id

With `Job ID entity` set, a print's `id` and `job_id` are the job id. This
Markdown card links to the frames of the job the printer reports now (or last
reported). Replace `sensor.my_printer_task_id` with your printer's job id
entity:

```yaml
type: markdown
content: >-
  {% set job = states('sensor.my_printer_task_id') %}
  {% set prints = state_attr('sensor.elegoo_spaghetti_detection_spaghetti_frames', 'prints') or [] %}
  {% set match = prints | selectattr('job_id', 'eq', job) | list %}
  {% if match %}
  {% set p = match[0] %}
  [Frames of this job]({{ p.viewer_url }}): {{ p.frames }} checks,
  highest score {{ (p.max * 100) | round(0) }} %
  {% else %}
  No frames for job {{ job }}.
  {% endif %}
```

The same lookup works anywhere you have a job id, for example from a print
history entity. With [button-card](https://github.com/custom-cards/button-card):

```yaml
type: custom:button-card
entity: sensor.elegoo_spaghetti_detection_spaghetti_frames
variables:
  job_id: "[[[ return states['sensor.my_printer_task_id'].state ]]]"
name: Frames
icon: mdi:filmstrip-box-multiple
show_state: false
show_label: true
label: |
  [[[
    const p = (entity.attributes.prints || []).find((x) => x.job_id === variables.job_id);
    return p ? `${p.frames} frames · max ${Math.round(p.max * 100)} %` : "No frames";
  ]]]
tap_action:
  action: url
  url_path: |
    [[[
      const p = (entity.attributes.prints || []).find((x) => x.job_id === variables.job_id);
      return p ? p.viewer_url : entity.attributes.viewer_url;
    ]]]
```

### Latest print and a delete button

The newest print is the first entry of `prints`:

```yaml
type: markdown
content: >-
  {% set p = (state_attr('sensor.elegoo_spaghetti_detection_spaghetti_frames', 'prints') or [none])[0] %}
  {% if p %}[Latest print: {{ p.name or p.id }}]({{ p.viewer_url }}) ·
  {{ p.frames }} frames · max {{ (p.max * 100) | round(0) }} %{% endif %}
```

To delete a print's frames from a dashboard, call the service with its `id`.
A script can pick the print from the attribute, for example the latest one:

```yaml
alias: Delete latest spaghetti frames
sequence:
  - action: elegoo_spaghetti_detection.delete_frames
    data:
      detector: elegoo_spaghetti_detection
      print_id: >-
        {{ (state_attr('sensor.elegoo_spaghetti_detection_spaghetti_frames', 'prints') or [{}])[0].id }}
```

and a button runs it after a confirmation:

```yaml
type: button
name: Delete latest frames
icon: mdi:delete
tap_action:
  action: perform-action
  perform_action: script.delete_latest_spaghetti_frames
  confirmation:
    text: Delete the frames of the latest print?
```
