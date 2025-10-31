import asyncio
from satel_integra import AsyncSatel


import logging


async def main(host: str, port: int, integration_key: str) -> None:
    """Basic demo of the connection. using encryption"""
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

    satel.register_callbacks(alarm_status_callback=alarm_status_callback)

    await satel.start(enable_monitoring=False)

    # Arm partition 1
    await satel.arm("3333", [1])

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await satel.close()


if __name__ == "__main__":
    asyncio.run(main("192.168.2.230", 7094, "mykey"))
