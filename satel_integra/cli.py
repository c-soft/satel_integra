# -*- coding: utf-8 -*-

"""Console script for satel_integra."""

import logging
import click
from satel_integra import demo


@click.command()
@click.option('--command', default="demo", help='Command on what to do.')
@click.option('--ip', default='192.168.2.230',
              help='Ip address of the ETHM module for SATEL Integra alarm.')
@click.option('--port', default=7094, help='Port number of the Satel Integra.')
@click.option('--loglevel', default='DEBUG', help='Logging level (python names).')
def main(port, ip, command, loglevel):
    """Console script for satel_integra."""
    numeric_level = getattr(logging, loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % loglevel)
        
    logging.basicConfig(level=numeric_level)

    click.echo("Demo of satel_integra library")
    if command == "demo":
        demo(ip, port)


if __name__ == "__main__":
    main()
