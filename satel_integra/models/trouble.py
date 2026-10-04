"""Structured troubles reported by the Satel Integra panel."""

from dataclasses import dataclass
from enum import Enum, unique


@unique
class SatelTroubleType(Enum):
    """Stable identifiers for system trouble conditions."""

    OUTPUT = "output"
    KEYPAD_POWER_SUPPLY = "keypad_power_supply"
    EXPANDER_POWER_SUPPLY = "expander_power_supply"
    PANEL_BATTERY = "panel_battery"
    PANEL_AC = "panel_ac"
    DT1 = "dt1"
    DT2 = "dt2"
    DTM = "dtm"
    RTC = "rtc"
    EXTERNAL_DEVICE_DTR = "external_device_dtr"
    PANEL_BATTERY_MISSING = "panel_battery_missing"
    EXTERNAL_MODEM_INITIALIZATION = "external_modem_initialization"
    EXTERNAL_MODEM_COMMAND = "external_modem_command"
    TELEPHONE_LINE_VOLTAGE_OR_AUXILIARY_PROCESSOR = (
        "telephone_line_voltage_or_auxiliary_processor"
    )
    TELEPHONE_LINE_BAD_SIGNAL = "telephone_line_bad_signal"
    TELEPHONE_LINE_NO_SIGNAL = "telephone_line_no_signal"
    MONITORING_STATION = "monitoring_station"
    EEPROM_OR_RTC_ACCESS = "eeprom_or_rtc_access"
    RAM = "ram"
    PANEL_RESTART_MEMORY = "panel_restart_memory"


@dataclass(frozen=True)
class SatelTrouble:
    """One active panel trouble and, where applicable, its source."""

    trouble_type: SatelTroubleType
    source_id: int | None = None
