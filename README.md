# IHC-style Room Thermostat

A Home Assistant custom integration that reproduces the heating logic of a
Schneider Electric IHC dual-sensor thermostat function block (room + floor
regulation, selectable priority, hysteresis, pulse/duty-cycle heating,
frost/night/guest setback, and max-temperature safety) — configured entirely
through the UI, no YAML.

## Features

- Room and/or floor temperature regulation, with a selectable priority
  ("one sensor satisfied" vs. "both sensors satisfied") when using both.
- Hysteresis-based heat calls with an optional **pulse (duty-cycle) heating**
  mode: once the target is reached, the relay cycles ON/OFF on configurable
  durations instead of sitting idle, to hold the temperature closer to
  setpoint.
- Frost protection, night setback, and guest setpoints, driven by a shared
  "house mode" `select`/`input_select` entity you already have (or none —
  the room then just runs in "occupied" mode).
- Hard max-temperature safety cutoff for both room and floor, independent
  of every other setting.
- Local override per room: follow house mode, force off, force frost
  protection, or force night setback.
- Optional window/door contact with a configurable delayed heat cutoff.
- One config entry per room, added via **Settings → Devices & services →
  Add integration**. All setpoints, durations, and modes are created as
  ordinary `number`/`select` entities you can edit from their more-info
  dialog or put on a dashboard — no helpers to pre-create.

## Installation (HACS)

1. HACS → the three-dot menu → **Custom repositories**.
2. Add this repository URL, category **Integration**.
3. Install **IHC-style Room Thermostat**, restart Home Assistant.
4. **Settings → Devices & services → Add integration**, search for
   "IHC-style Room Thermostat", and add one entry per room.

## Configuration

Each config entry wires up:

| Field | Required | Notes |
|---|---|---|
| Room name | yes | Used as the entry title and entity name prefix |
| Room temperature sensor | yes | `sensor.*`, `device_class: temperature` |
| Floor temperature sensor | no | Same, for floor regulation |
| Heater relay switch | yes | `switch.*` — must be unique per config entry |
| Window/door contact | no | `binary_sensor.*` |
| House mode entity | no | Any `select`/`input_select` with values like `Beboet`/`Ubeboet`/`Frostsikring`/`Gæster` |

Everything else (setpoints, hysteresis, max-temp limits, regulation mode,
priority, local override, pulse heating on/off + durations) is created as
entities on the new device and can be changed at any time from the UI.

## Credits

Logic reverse-engineered from a Schneider Electric IHC "5.2.05.b" heating
function block export.
