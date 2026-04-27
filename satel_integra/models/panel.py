"""Structured panel information."""

import logging
from dataclasses import dataclass

from .firmware import SatelFirmwareVersion

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SatelPanelModel:
    """Known INTEGRA panel model."""

    name: str


PANEL_MODEL_BY_TYPE_CODE: dict[int, SatelPanelModel] = {
    0: SatelPanelModel("INTEGRA 24"),
    1: SatelPanelModel("INTEGRA 32"),
    2: SatelPanelModel("INTEGRA 64"),
    3: SatelPanelModel("INTEGRA 128"),
    4: SatelPanelModel("INTEGRA 128-WRL SIM300"),
    66: SatelPanelModel("INTEGRA 64 Plus"),
    67: SatelPanelModel("INTEGRA 128 Plus"),
    72: SatelPanelModel("INTEGRA 256 Plus"),
    132: SatelPanelModel("INTEGRA 128-WRL LEON"),
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
