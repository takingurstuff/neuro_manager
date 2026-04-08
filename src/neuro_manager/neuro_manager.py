import typer
import logging
import asyncio
from pathlib import Path
from rich.table import Table
from neuro_manager.state import State
from neuro_manager.log import setup_logging, console
from neuro_manager.downloader import NeuroKaraokeFolder

app = typer.Typer(
    help="Nwero Manager: Downloads the Neuro Karaoke Archive and Use [bold][red]PROPER[/bold][/red] ID3v2.4",
    rich_markup_mode="rich",
)


@app.callback()
def main(
    ctx: typer.Context,
    concurrent: int = typer.Option(
        5, "--threads", "-t", help="Number of concurrent downloads"
    ),
    max_download_retries: int = typer.Option(
        5, "--retries", "-r", help="Number of times to retry for each download"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable detailed debug logging."
    ),
    library: Path = typer.Option(
        Path.cwd(),
        "-l",
        "--library-path",
        exists=False,
        file_okay=False,
        dir_okay=True,
        readable=True,
        writable=True,
        resolve_path=True,
        help="Where the library should be saved",
    ),
    creds: Path = typer.Option(
        Path.cwd() / "creds.json",
        "-s",
        "--service-account",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Google service accounts credential json",
    ),
):
    """
    [bold]Settings & Auth[/bold]
    General Settings.
    """
    logger = setup_logging(verbose)
    ctx.obj = State(
        verbose=verbose,
        concurrent_downloads=concurrent,
        retries=max_download_retries,
        library_path=library,
        credentials_path=creds,
    )

    logger.debug(f"⚙️  Verbose mode [green]ON[/green].")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("Credentials Path", f"[green]{creds}[/green]")
    table.add_row("Verbose", "[cyan]Yes[/cyan]" if ctx.obj.verbose else "[dim]No[/dim]")
    table.add_row(
        "Threads", f"[bold yellow]{ctx.obj.concurrent_downloads}[/bold yellow]"
    )

    logger.info("🚀 [bold magenta]Initializing App State[/bold magenta]")
    console.print(table)


@app.command()
def create(ctx: typer.Context):
    """
    🏗️  [bold]Setup Library[/bold]
    Creates a new library and generates initial artifact files.
    """
    logger = logging.getLogger("rich")
    logger.info("🏗️  [bold cyan]Starting full library setup...[/bold cyan]")

    async def _main():
        async with NeuroKaraokeFolder(ctx) as folder:
            await folder.setup_library()
            logger.info("🎉 [bold green]Library setup complete![/bold green]")

    asyncio.run(_main())


@app.command()
def update(
    ctx: typer.Context, only_check_last: bool = typer.Option(True, "--only-check-last")
):
    """
    🔄 [bold]Sync Updates[/bold]
    Compares local checksums and downloads only what is necessary.
    """
    logger = logging.getLogger("rich")
    logger.info(
        "🔄 [bold cyan]Scanning local cache and checking for remote updates...[/bold cyan]"
    )

    async def _main():
        async with NeuroKaraokeFolder(ctx) as folder:
            await folder.sync_library()
            logger.info("✨ [bold green]Library synchronization complete![/bold green]")

    asyncio.run(_main())


@app.command()
def download_extras(ctx: typer.Context):
    """
    [bold]Sync Extras[/bold]
    Re-Downloads the extra contents if needed
    """
    logger = logging.getLogger("rich")
    logger.info(
        "📦 [bold cyan]Checking and syncing extra library contents...[/bold cyan]"
    )

    async def _main():
        async with NeuroKaraokeFolder(ctx) as folder:
            await folder.sync_extras()
            logger.info("✅ [bold green]Extras synchronized successfully![/bold green]")

    asyncio.run(_main())


if __name__ == "__main__":
    app()
