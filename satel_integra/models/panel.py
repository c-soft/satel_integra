"""Structured panel information."""

import logging
from dataclasses import dataclass

from .firmware import SatelFirmwareVersion

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SatelPanelModel:
    """Known INTEGRA panel model."""

    name: str
    max_zones: int
    max_outputs: int


PANEL_MODEL_BY_TYPE_CODE: dict[int, SatelPanelModel] = {
    0: SatelPanelModel(
        name="INTEGRA 24",
        max_zones=24,
        max_outputs=20,
    ),
    1: SatelPanelModel(
        name="INTEGRA 32",
        max_zones=32,
        max_outputs=32,
    ),
    2: SatelPanelModel(
        name="INTEGRA 64",
        max_zones=64,
        max_outputs=64,
    ),
    3: SatelPanelModel(
        name="INTEGRA 128",
        max_zones=128,
        max_outputs=128,
    ),
    4: SatelPanelModel(
        name="INTEGRA 128-WRL SIM300",
        max_zones=128,
        max_outputs=128,
    ),
    66: SatelPanelModel(
        name="INTEGRA 64 Plus",
        max_zones=64,
        max_outputs=64,
    ),
    67: SatelPanelModel(
        name="INTEGRA 128 Plus",
        max_zones=128,
        max_outputs=128,
    ),
    72: SatelPanelModel(
        name="INTEGRA 256 Plus",
        max_zones=256,
        max_outputs=256,
    ),
    132: SatelPanelModel(
        name="INTEGRA 128-WRL LEON",
        max_zones=128,
        max_outputs=128,
    ),
}


@dataclass(frozen=True)
class SatelPanelInfo:
    """Information returned by the INTEGRA panel version command."""

    type_code: int
    model: SatelPanelModel | None
    firmware: SatelFirmwareVersion
    language_code: int
    settings_stored_in_flash: bool

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelPanelInfo":
        """Parse the 14-byte 0x7E INTEGRA version response payload."""
        type_code = payload[0]
        model = PANEL_MODEL_BY_TYPE_CODE.get(type_code)
        if model is None:
            _LOGGER.warning("Unknown INTEGRA panel type code: %s", type_code)

        return cls(
            type_code=type_code,
            model=model,
            firmware=SatelFirmwareVersion._from_payload(payload[1:12]),
            language_code=payload[12],
            settings_stored_in_flash=payload[13] == 0xFF,
        )
