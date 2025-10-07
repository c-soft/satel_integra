import asyncio
from satel_integra import AsyncSatel


async def main(host, port) -> None:
    """Basic demo of the monitoring capabilities."""
    loop = asyncio.get_event_loop()

    zones = [1, 2, 5]
    outputs = [3, 9]
    partitions = [1]

    satel = AsyncSatel(
        host,
        port,
        loop,
        monitored_zones=zones,
        monitored_outputs=outputs,
        partitions=partitions,
    )

    loop.run_until_complete(satel.connect())
    loop.create_task(satel.arm("3333", (1,)))
    loop.create_task(satel.disarm("3333", (1,)))
    loop.create_task(satel.keep_alive())
    loop.create_task(satel.monitor_status())

    loop.run_forever()
    loop.close()


if __name__ == "__main__":
    asyncio.run(main("localhost", 7094))
