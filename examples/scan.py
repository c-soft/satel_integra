"""Scan panel metadata without enabling state monitoring or controlling resources."""

import asyncio
import logging

from satel_integra import AsyncSatel

_LOGGER = logging.getLogger(__name__)


def report_progress(completed: int, total: int) -> None:
    """Report address-read progress for the current resource type."""
    _LOGGER.info("Progress: %s/%s", completed, total)


async def main(host: str, port: int, integration_key: str | None = None) -> None:
    """Read partitions, zones, and outputs sequentially, then close the connection."""
    satel = AsyncSatel(host, port, integration_key=integration_key)

    try:
        await satel.connect(raise_exceptions=True)
        # Startup caches panel capacities needed by the scan methods.
        await satel.start(enable_monitoring=False)

        for resource_type, scan in (
            ("partitions", satel.scan_partitions),
            ("zones", satel.scan_zones),
            ("outputs", satel.scan_outputs),
        ):
            _LOGGER.info("Scanning %s", resource_type)
            devices = await scan(on_progress=report_progress)
            for device in devices:
                _LOGGER.info("%s", device)
    finally:
        # Also release resources if connecting, starting, or scanning fails.
        await satel.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Replace this documentation address with your panel's address.
    # Pass integration_key to main() if the connection requires encryption.
    asyncio.run(main("192.168.150.201", 7094))
