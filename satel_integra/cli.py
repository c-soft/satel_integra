# -*- coding: utf-8 -*-

"""Console script for satel_integra."""

import click
from .satel_integra import demo


@click.command()
@click.option('--command', default="demo", help='Command on what to do.')
@click.option('--ip', default='192.168.2.220',
              help='Ip address of the ETHM module for SATEL Integra alarm.')
@click.option('--port', default=7094, help='Port number of the Satel Integra.')
def main(port, ip, command):
    """Console script for satel_integra."""
    click.echo("Demo of satel_integra library")
    if command == "demo":
        demo(ip, port)


if __name__ == "__main__":
    main()
