"""Structured troubles reported by the Satel Integra panel."""

from dataclasses import dataclass
from enum import Enum, unique

from satel_integra.utils import decode_bitmask_le


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


# "System troubles" bits in wire order, least significant bit first.
_SYSTEM_TROUBLES_BY_BIT = (
    SatelTrouble(SatelTroubleType.OUTPUT, source_id=1),
    SatelTrouble(SatelTroubleType.OUTPUT, source_id=2),
    SatelTrouble(SatelTroubleType.OUTPUT, source_id=3),
    SatelTrouble(SatelTroubleType.OUTPUT, source_id=4),
    SatelTrouble(SatelTroubleType.KEYPAD_POWER_SUPPLY),
    SatelTrouble(SatelTroubleType.EXPANDER_POWER_SUPPLY),
    SatelTrouble(SatelTroubleType.PANEL_BATTERY),
    SatelTrouble(SatelTroubleType.PANEL_AC),
    SatelTrouble(SatelTroubleType.DT1),
    SatelTrouble(SatelTroubleType.DT2),
    SatelTrouble(SatelTroubleType.DTM),
    SatelTrouble(SatelTroubleType.RTC),
    SatelTrouble(SatelTroubleType.EXTERNAL_DEVICE_DTR),
    SatelTrouble(SatelTroubleType.PANEL_BATTERY_MISSING),
    SatelTrouble(SatelTroubleType.EXTERNAL_MODEM_INITIALIZATION),
    SatelTrouble(SatelTroubleType.EXTERNAL_MODEM_COMMAND),
    # The meaning depends on the panel model; retain both interpretations.
    SatelTrouble(SatelTroubleType.TELEPHONE_LINE_VOLTAGE_OR_AUXILIARY_PROCESSOR),
    SatelTrouble(SatelTroubleType.TELEPHONE_LINE_BAD_SIGNAL),
    SatelTrouble(SatelTroubleType.TELEPHONE_LINE_NO_SIGNAL),
    SatelTrouble(SatelTroubleType.MONITORING_STATION, source_id=1),
    SatelTrouble(SatelTroubleType.MONITORING_STATION, source_id=2),
    SatelTrouble(SatelTroubleType.EEPROM_OR_RTC_ACCESS),
    SatelTrouble(SatelTroubleType.RAM),
    SatelTrouble(SatelTroubleType.PANEL_RESTART_MEMORY),
)


@dataclass(frozen=True)
class SatelSystemTroubles:
    """System troubles reported by the Part 1 trouble response (0x1B)."""

    troubles: frozenset[SatelTrouble]

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelSystemTroubles":
        """Decode the three system bytes within the 47-byte Part 1 payload."""
        # Technical zones and expander troubles occupy the first 40 bytes.
        active_bits = decode_bitmask_le(payload[40:43], 3)
        return cls(
            troubles=frozenset(_SYSTEM_TROUBLES_BY_BIT[bit - 1] for bit in active_bits)
        )
