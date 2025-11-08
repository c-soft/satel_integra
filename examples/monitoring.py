import asyncio
from satel_integra import AsyncSatel


import logging


async def main(host: str, port: int, integration_key: str | None = None) -> None:
    """Basic demo of the monitoring capabilities."""
    logging.getLogger().setLevel(logging.DEBUG)

    zones = [1, 2, 5]
    outputs = [3, 9]
    partitions = [1]

    satel = AsyncSatel(
        host,
        port,
        monitored_zones=zones,
        monitored_outputs=outputs,
        partitions=partitions,
        integration_key=integration_key,
    )

    await satel.connect()

    def alarm_status_callback() -> None:
        logging.info(f"Alarm status changed: {satel.partition_states}")

    def zone_status_callback(status) -> None:
        logging.info(f"Zone status changed: {status}")

    def output_status_callback(status) -> None:
        logging.info(f"Output status changed: {status}")

    satel.register_callbacks(
        alarm_status_callback=alarm_status_callback,
        zone_changed_callback=zone_status_callback,
        output_changed_callback=output_status_callback,
    )

    await satel.start(enable_monitoring=True)

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await satel.close()


if __name__ == "__main__":
    asyncio.run(main("192.168.2.230", 7094, "1234"))
