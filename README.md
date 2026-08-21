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
  mode: once the target is reached, the relay cycles ON/OFF on a configurable
  total cycle length instead of sitting idle, to hold the temperature closer
  to setpoint. The ON/OFF split within that cycle is **weather-compensated**
  (scaled between a mild-day and a cold-day outdoor "feels like" temperature)
  and then **self-adjusting**: a persisted bias nudges the split up if a
  cycle ever had to hand off to a full heat call (ran too cold), and eases
  it down after cycles that held comfortably — no separate weather station
  integration or ML model required, just the room's own tracking history.
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
| Outdoor "feels like" sensor | no | `sensor.*`, `device_class: temperature`. Powers the weather-compensated pulse duty cycle; without it, pulse heating falls back to the midpoint between min/max duty. |

Everything else (setpoints, hysteresis, max-temp limits, regulation mode,
priority, local override, pulse heating on/off + durations + the two
weather-compensation reference temperatures + min/max duty + the
auto-tuned adaptive bias) is created as entities on the new device and can
be changed at any time from the UI. See [Entities](#entities) below for
what each one does.

## Entities

Every room's config entry creates the same set of entities on one device.

### Climate

| Entity | Description |
|---|---|
| `climate.<room>` | The round thermostat dial. Shows/sets room or floor temperature depending on regulation mode. Dial adjustments write to the "occupied" setpoint below; the power toggle switches Local override between Forced off and Follow house mode only — frost/guest/night still need the select entity. The heating/idle label tracks the heater switch's actual state, not just the coordinator's decision. |

### Setpoints

Setpoints are always clamped down to the matching max-temperature limit if they'd otherwise exceed it.

| Entity | Default | Description |
|---|---|---|
| Setpoint room, occupied | 21.0°C | Room target while house mode is Occupied — the field the dial normally edits. |
| Setpoint floor, occupied | 24.0°C | Floor target while house mode is Occupied. |
| Setpoint, unoccupied | 17.0°C | Shared room+floor target while house mode is Unoccupied. |
| Setpoint, guest/night | 19.0°C | Used for Guest mode, or forced night setback while the house is occupied. |
| Setpoint, frost protection | 7.0°C | Minimum target during frost protection or an open window — purely to avoid frost damage. |
| Max temperature, room | 26.0°C | Hard safety ceiling — heat is forced off if the room reaches this, regardless of everything else. |
| Max temperature, floor | 30.0°C | Same safety ceiling, for the floor. |

### Regulation & mode

| Entity | Default | Description |
|---|---|---|
| Regulation mode (select) | Room and floor | Floor / Room and floor / Room — which sensor(s) drive the heat call and dial display. |
| Priority (select) | 1 temperature satisfied | Only relevant for "Room and floor": whether ONE sensor (OR) or BOTH sensors (AND) must reach setpoint+hysteresis before heat is considered satisfied. |
| Hysteresis | 0.3°C | Deadband around the setpoint so the relay doesn't chatter on small fluctuations. |
| Local override (select) | Follow house mode | Follow house mode / Forced off / Forced frost protection / Forced night setback — overrides the shared house mode for this room only. |

### Pulse heating — basics

| Entity | Default | Description |
|---|---|---|
| Pulse heating (select) | Enabled | Enabled/Disabled. When disabled, the relay simply sits idle once the setpoint is reached. |
| Total cycle length | 40 min | Length of one ON+OFF cycle; the ON/OFF split is computed automatically from duty% below. |
| Minimum phase duration | 8 min | Neither the ON nor OFF phase is ever shorter than this — matches a thermal actuator's own travel time so it always has time to fully open/close before being told to reverse. |

### Pulse heating — weather compensation

| Entity | Default | Description |
|---|---|---|
| Cold-day outdoor temperature | -10°C | At or below this outdoor "feels like" temperature, max duty is used. |
| Mild-day outdoor temperature | 15°C | At or above this, min duty is used. |
| Minimum duty | 15% | Lowest ON share of the cycle, no matter how mild it gets. |
| Maximum duty | 90% | Highest ON share of the cycle, no matter how cold it gets. |

### Pulse heating — adaptive learning

Two values the coordinator tunes on its own; not normally meant for routine manual editing, but they're real entities so you can watch or reset them.

| Entity | Default | Description |
|---|---|---|
| Adaptive bias | 0.0% | Added on top of the weather-curve duty%. Nudged +3 when a cycle had to hand off to a full heat call (ran too cold), -1 when a cycle held comfortably on its own. |
| Learned residual heat | 0.0°C | How many degrees the primary sensor typically keeps climbing after the relay closes (most pronounced on floor slabs with a lot of thermal mass). Used to end the ON phase early so the residual rise lands on the setpoint instead of past it. |

### Status (read-only)

| Entity | Description |
|---|---|
| Heating state | Status text, e.g. `"Beboet (Pulsvarme: ON, 52% duty)"`. Suffixes you may see: `(Max temp nået)` safety cutoff, `(Langt over sætpunkt)` too far above setpoint to keep pulsing, `(Afventer eftervarme)` waiting for residual heat to plateau before starting a new cycle. Carries a `heat_call` (true/false) attribute. |
| Active setpoint, room | The room setpoint that actually applies right now, given house mode. |
| Active setpoint, floor | The floor setpoint that actually applies right now. |

## Credits

Logic reverse-engineered from a Schneider Electric IHC "5.2.05.b" heating
function block export.
