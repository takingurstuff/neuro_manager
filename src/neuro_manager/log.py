import logging
from rich.console import Console
from rich.logging import RichHandler

console = Console()
_logfile = Console(
    file=open("run.log", "a"), color_system=None, force_terminal=False, width=128
)


def setup_logging(verbose: bool):
    """Configures the standard logging module to use Rich for formatting."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(rich_tracebacks=True, markup=True, console=console),
            RichHandler(rich_tracebacks=True, markup=True, console=_logfile),
        ],
    )
    return logging.getLogger("rich")
