"""Constants for the IHC-style Room Thermostat integration."""

DOMAIN = "ihc_style_thermostat"

CONF_ROOM_TEMP_SENSOR = "room_temp_sensor"
CONF_FLOOR_TEMP_SENSOR = "floor_temp_sensor"
CONF_HEATER_SWITCH = "heater_switch"
CONF_WINDOW_SENSOR = "window_sensor"
CONF_HOUSE_MODE_ENTITY = "house_mode_entity"
CONF_OUTDOOR_TEMP_ENTITY = "outdoor_temp_entity"

OPTION_WINDOW_DELAY_MINUTES = "window_delay_minutes"
DEFAULT_WINDOW_DELAY_MINUTES = 10

MODE_OCCUPIED = "Beboet"
MODE_UNOCCUPIED = "Ubeboet"
MODE_FROST = "Frostsikring"
MODE_GUEST = "Gæster"
MODE_NIGHT = "Nedsænkning"
MODE_OFF = "Slukket"
MODE_ALARM = "Alarm"
MODE_WINDOW_OPEN = "Dør/vindue åben"

HOUSE_MODE_MAP = {
    "Beboet": MODE_OCCUPIED,
    "Ubeboet": MODE_UNOCCUPIED,
    "Frostsikring": MODE_FROST,
    "Gæster": MODE_GUEST,
}

REGULATION_OPTIONS = ["Gulv", "Rum og gulv", "Rum"]
PRIORITY_OPTIONS = ["1 temperatur opfyldt", "Begge temperaturer opfyldt"]
LOCAL_MODE_OPTIONS = [
    "Følger husets tilstand",
    "Tvunget fra",
    "Tvunget frostsikring",
    "Tvunget nedsænkning",
]
PULSVARME_OPTIONS = ["Aktiveret", "Ikke Aktiveret"]

# key -> (default, min, max, step, unit, icon)
NUMBER_DEFS = {
    "setpunkt_rum_beboet": (21.0, 10.0, 30.0, 0.5, "°C", "mdi:home-thermometer"),
    "setpunkt_gulv_beboet": (24.0, 10.0, 35.0, 0.5, "°C", "mdi:heating-coil"),
    "setpunkt_ubeboet": (17.0, 5.0, 25.0, 0.5, "°C", "mdi:home-minus"),
    "setpunkt_gaest_nat": (19.0, 5.0, 25.0, 0.5, "°C", "mdi:weather-night"),
    "setpunkt_frost": (7.0, 0.0, 15.0, 0.5, "°C", "mdi:snowflake"),
    "max_temp_rum": (26.0, 20.0, 35.0, 0.5, "°C", "mdi:thermometer-alert"),
    "max_temp_gulv": (30.0, 20.0, 40.0, 0.5, "°C", "mdi:thermometer-alert"),
    "hysterese": (0.3, 0.1, 3.0, 0.1, "°C", "mdi:sine-wave"),
    "pulsvarme_varme_on_min": (20.0, 1.0, 120.0, 1.0, "min", "mdi:timer-play"),
    "pulsvarme_varme_off_min": (20.0, 1.0, 120.0, 1.0, "min", "mdi:timer-pause"),
    # Weather-compensated + self-adjusting pulse duty cycle (see coordinator.py
    # _compute_duty_percent / _adjust_bias). "pulsvarme_adaptiv_bias" is not
    # meant for routine manual editing -- the coordinator reads/writes it
    # itself, but it must be a real, restorable entity to persist across
    # HA restarts.
    "ude_temp_lav": (-10.0, -30.0, 10.0, 0.5, "°C", "mdi:snowflake-thermometer"),
    "ude_temp_hoej": (15.0, 0.0, 25.0, 0.5, "°C", "mdi:thermometer"),
    "pulsvarme_duty_min": (15.0, 0.0, 100.0, 1.0, "%", "mdi:percent-outline"),
    "pulsvarme_duty_max": (90.0, 0.0, 100.0, 1.0, "%", "mdi:percent"),
    "pulsvarme_adaptiv_bias": (0.0, -30.0, 30.0, 0.1, "%", "mdi:tune"),
}

# key -> (options, default, icon)
SELECT_DEFS = {
    "regulering": (REGULATION_OPTIONS, "Rum og gulv", "mdi:thermostat"),
    "prioritet": (PRIORITY_OPTIONS, "1 temperatur opfyldt", "mdi:priority-high"),
    "lokal_tilstand": (LOCAL_MODE_OPTIONS, "Følger husets tilstand", "mdi:home-switch"),
    "pulsvarme": (PULSVARME_OPTIONS, "Aktiveret", "mdi:pulse"),
}
