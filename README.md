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
| Wind speed sensor | no | Any `sensor.*` reporting wind speed in its own unit. Powers a learned wind duty% top-up — see [Pulse heating — wind & sun](#pulse-heating--wind--sun). Without it, the wind term is always 0. |
| Sun/light-level sensor | no | Any `sensor.*` reporting light/solar intensity (lux, W/m², UV index, ...) in its own unit. Powers a learned sun duty% discount — see below. Without it, the sun term is always 0. |

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
| Setpoint floor, occupied | 24.0°C | Floor target while house mode is Occupied. Not used at all in "Room (floor as minimum)" mode — see Floor minimum temperature below. |
| Setpoint, unoccupied | 17.0°C | Shared room+floor target while house mode is Unoccupied. |
| Setpoint, guest/night | 19.0°C | Used for Guest mode, or forced night setback while the house is occupied. |
| Setpoint, frost protection | 7.0°C | Minimum target during frost protection or an open window — purely to avoid frost damage. |
| Max temperature, room | 26.0°C | Hard safety ceiling — heat is forced off if the room reaches this, regardless of everything else. |
| Max temperature, floor | 30.0°C | Same safety ceiling, for the floor. |
| Floor minimum temperature | 18.0°C | Only used in regulation mode "Room (floor as minimum)" — see [Regulation & mode](#regulation--mode). Forces a heat call whenever the floor drops below this (with the usual hysteresis), independent of the room. Ignored by every other regulation mode. |

### Regulation & mode

| Entity | Default | Description |
|---|---|---|
| Regulation mode (select) | Room and floor | Floor / Room and floor / Room / **Room (floor as minimum)** — which sensor(s) drive the heat call and dial display. |
| Priority (select) | 1 temperature satisfied | Only relevant for "Room and floor": whether ONE sensor (OR) or BOTH sensors (AND) must reach setpoint+hysteresis before heat is considered satisfied. |
| Hysteresis | 0.3°C | Deadband around the setpoint so the relay doesn't chatter on small fluctuations. |
| Local override (select) | Follow house mode | Follow house mode / Forced off / Forced frost protection / Forced night setback — overrides the shared house mode for this room only. |

**"Room (floor as minimum)"**: the room drives everything — the heat call, the dial, pulse heating, and *all* of its self-learning (adaptive bias, residual heat, the wind/sun duty terms below). The floor isn't a regulation target at all in this mode; it only forces a heat call, regardless of what the room is doing, if it ever drops below **Floor minimum temperature** (a separate entity from "Setpoint floor, occupied", only used by this mode — see [Setpoints](#setpoints)). Only while house mode is Occupied: unoccupied/night/guest/frost still use their own shared, already-reduced setpoint for both sensors, same as every other regulation mode.

### Pulse heating — basics

| Entity | Default | Description |
|---|---|---|
| Pulse heating (select) | Enabled | Enabled/Disabled. When disabled, the relay simply sits idle once the setpoint is reached. |
| Total cycle length | 40 min | Length of one ON+OFF cycle; the ON/OFF split is computed automatically from duty% below. |
| Minimum phase duration | 8 min | Neither the ON nor OFF phase is ever shorter than this — matches a thermal actuator's own travel time so it always has time to fully open/close before being told to reverse. |
| Pause after a real heating cycle | 30 min | After a *real* heating cycle ends (room fell below setpoint − hysteresis, heat ran continuously, then was satisfied again), pulse heating holds off starting a fresh ON phase for this long. Gives that cycle's residual-heat measurement (below) a clean window to plateau before pulse heating's own small ON pulses could add extra heat on top of it. Set to 0 to disable the pause. |

### Pulse heating — weather compensation

| Entity | Default | Description |
|---|---|---|
| Cold-day outdoor temperature | -10°C | At or below this outdoor "feels like" temperature, max duty is used. |
| Mild-day outdoor temperature | 15°C | At or above this, min duty is used. |
| Minimum duty | 15% | Lowest ON share of the cycle, no matter how mild it gets. |
| Maximum duty | 90% | Highest ON share of the cycle, no matter how cold it gets. |

### Pulse heating — wind & sun

Only active when the matching sensor (above) is configured; with neither set, these six entities exist but never affect anything. `vind_lav`/`vind_hoej` and `sol_lav`/`sol_hoej` are in that sensor's own unit (e.g. m/s, lux) — there's no unit conversion, so set them to match what your sensor actually reports.

| Entity | Default | Description |
|---|---|---|
| Wind factor, calm threshold | 0 (sensor unit) | At or below this wind reading, the wind term is 0. |
| Wind factor, windy threshold | 6 (sensor unit) | At or above this wind reading, the full wind duty top-up (below) applies. Linearly scaled in between. |
| Wind duty top-up | 0.0% | **Learned.** Extra duty% added while wind is in the calm..windy range. Nudged up when a cycle fell through to a real heat call while windy (wind likely cost extra heat), nudged back down when a windy cycle held fine anyway. Starts at 0 — only grows if windy conditions actually correlate with needing more heat. |
| Sun factor, overcast threshold | 2000 (sensor unit) | At or below this light reading, the sun term is 0. |
| Sun factor, bright threshold | 15000 (sensor unit) | At or above this light reading, the full sun duty discount (below) applies. Linearly scaled in between. |
| Sun duty discount | 0.0% | **Learned.** Duty% subtracted while sun/light is in the overcast..bright range. Nudged up when a cycle held fine (or better) while sunny — free solar heat likely helped — nudged back down when a sunny cycle still fell through. Starts at 0 — only grows if sun actually correlates with needing less heat. |

### Pulse heating — adaptive learning

Two more values the coordinator tunes on its own; not normally meant for routine manual editing, but they're real entities so you can watch or reset them.

| Entity | Default | Description |
|---|---|---|
| Adaptive bias | 0.0% | Added on top of the weather-curve duty% (and the wind/sun terms above). Nudged +3 when a cycle had to hand off to a full heat call (ran too cold), -1 when a cycle held comfortably on its own. |
| Learned residual heat | 0.0°C | How many degrees the primary sensor typically keeps climbing after the relay closes (most pronounced on floor slabs with a lot of thermal mass). Only measured after a *real* heating cycle ends, never from pulse heating's own near-setpoint pulses (see the pause above) — used to end a pulse ON phase early so the residual rise lands on the setpoint instead of past it. |

### Status (read-only)

| Entity | Description |
|---|---|
| Heating state | Status text, e.g. `"Beboet (Pulsvarme: ON, 52% duty)"`. Suffixes you may see: `(Max temp nået)` safety cutoff, `(Langt over sætpunkt)` too far above setpoint to keep pulsing, `(Afventer eftervarme)` waiting for residual heat to plateau before starting a new cycle. Carries a `heat_call` (true/false) attribute. |
| Active setpoint, room | The room setpoint that actually applies right now, given house mode. |
| Active setpoint, floor | The floor setpoint that actually applies right now. |

## Credits

Logic reverse-engineered from a Schneider Electric IHC "5.2.05.b" heating
function block export.
