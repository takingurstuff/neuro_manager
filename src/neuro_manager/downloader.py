import re
import json
import random
import asyncio
from pathlib import Path
from typer import Context
from rich.table import Table
from datetime import timezone
from aiopath import AsyncPath
from datetime import datetime
from dataclasses import dataclass
from neuro_manager.fstree import *
from neuro_manager.state import State
from typing import Callable, Iterator
from collections.abc import Awaitable
from neuro_manager.retag import tag_mp3
from aiogoogle import Aiogoogle, GoogleAPI
from neuro_manager.log import setup_logging, console
from aiogoogle.excs import AiogoogleError, HTTPError
from aiogoogle.auth.creds import ServiceAccountCreds
from neuro_manager.dbutils import save_entries, SongDB, SongEntry, Seeker

NEURO_FOLDER_ID = "1B1VaWp-mCKk15_7XpFnImsTdBJPOGx7a"


def extract_disc_id(name: str) -> int | None:
    """Extracts the first contiguous integer from a 'DISC' string."""
    if not name.startswith("DISC"):
        return None
    match = re.search(r"\d+", name)
    return int(match.group()) if match else None


def extract_track_id(name: str) -> int | None:
    match = re.search(r"(\d+)\. ", name)
    return int(match.group(1)) if match else None


@dataclass
class DownloadTask:
    file_id: str
    callback_func: Callable[[bytes], Awaitable[None] | None]
    max_retries: int = 5


class Downloader:
    def __init__(self, aiogoogle: Aiogoogle, drive_service: GoogleAPI, state: State):
        self.aiogoogle = aiogoogle
        self.drive = drive_service
        self.logger = setup_logging(state.verbose)
        self.queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
        self.download_worker: asyncio.Task | None = None
        self.state = state

    async def _download_worker(
        self,
        file_id: str,
        callback: Callable[[bytes], Awaitable[None] | None],
        max_retries=5,
    ):
        delay = 1

        for attempt in range(max_retries):
            try:
                self.logger.info(
                    f"⏳ [bold white]Attempt {attempt + 1}:[/bold white] Downloading [cyan]{file_id}[/cyan]"
                )

                content: bytes | str = await self.aiogoogle.as_service_account(
                    self.drive.files.get(
                        fileId=file_id, acknowledgeAbuse=True, alt="media"
                    )
                )
                if isinstance(content, str):
                    content = content.encode("utf-8")

                res = callback(content)
                if isinstance(
                    res, Awaitable
                ):  # Fixed: logic was isinstance(Awaitable, res)
                    await res
                self.logger.info(
                    f"✅ [bold green]Success:[/bold green] Downloaded [cyan]{file_id}[/cyan]"
                )
                return

            except HTTPError as e:
                status = e.res.status

                if 400 <= status < 500:
                    self.logger.critical(
                        f"❌ [bold red]FATAL:[/bold red] Client error {status} for [cyan]{file_id}[/cyan]. Aborting."
                    )
                    raise SystemExit(f"Non-retryable HTTP {status} error: {e}")

                if 500 <= status < 600 or status == 429:
                    if attempt == max_retries - 1:
                        self.logger.error(
                            f"💥 [bold red]Max retries reached[/bold red] for [cyan]{file_id}[/cyan]"
                        )
                        raise e

                    sleep_time = (delay * (2**attempt)) + random.uniform(0, 1)
                    self.logger.warning(
                        f"⚠️ [bold yellow]Server Error {status}:[/bold yellow] Retrying in [bold]{sleep_time:.2f}s[/bold]..."
                    )
                    await asyncio.sleep(sleep_time)
                    continue
                raise e

            except AiogoogleError as e:
                self.logger.error(f"🛑 [bold red]Library Error:[/bold red] {e}")
                raise e

    async def _download_controller(self):
        running = True
        active_downloads = set()
        self.logger.info("🚀 [bold magenta]Download Controller Started[/bold magenta]")

        while running or active_downloads:
            while len(active_downloads) < self.state.concurrent_downloads and running:
                if self.queue.empty() and active_downloads:
                    break
                item = await self.queue.get()
                if item is None:
                    self.logger.info(
                        "🏁 [bold]Drain signal received.[/bold] Finishing active tasks..."
                    )
                    running = False
                    self.queue.task_done()
                    break

                task = asyncio.create_task(
                    self._download_worker(
                        item.file_id, item.callback_func, item.max_retries
                    )
                )
                active_downloads.add(task)
                task.add_done_callback(lambda t: self.queue.task_done())

                self.logger.info(
                    f"➕ [bold blue]Worker Spawned:[/bold blue] [cyan]{item.file_id}[/cyan] "
                    f"({len(active_downloads)}/{self.state.concurrent_downloads})"
                )

            if active_downloads:
                done, pending = await asyncio.wait(
                    active_downloads, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    active_downloads.remove(t)
                    self.logger.info(
                        f"➖ [bold green]Worker Finished:[/bold green] "
                        f"({len(active_downloads)}/{self.state.concurrent_downloads} slots free)"
                    )

            elif running:
                self.logger.debug("💤 [dim]Controller idling...[/dim]")
                if (item := await self.queue.get()) is not None:
                    task = asyncio.create_task(
                        self._download_worker(
                            item.file_id, item.callback_func, item.max_retries
                        )
                    )
                    active_downloads.add(task)
                else:
                    running = False
                self.queue.task_done()

        self.logger.info("🛑 [bold magenta]Download Controller Exited[/bold magenta]")

    def start_dl(self):
        task = asyncio.create_task(self._download_controller())
        self.download_worker = task

    async def stop_dl(self):
        if self.download_worker is None:
            return

        await self.queue.put(None)
        try:
            await self.download_worker
        finally:
            self.download_worker = None


class NeuroKaraokeFolder:
    def __init__(self, context: Context):
        state: State = context.obj
        json_creds = json.loads(state.credentials_path.read_text())
        creds = ServiceAccountCreds(
            **json_creds,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        self.logger = setup_logging(state.verbose)
        self.aiogoogle = Aiogoogle(service_account_creds=creds)
        self.discs: list[DriveFolder] = []
        self.extras: list[DriveNode] = []
        self.state = state
        self.song_entries = []

        self.logger.info(
            f"📂 [bold blue]NeuroKaraoke Drive Manager Initialized[/bold blue]"
        )
        self.logger.info(f"📍 Root Folder: [magenta]{NEURO_FOLDER_ID}[/magenta]")
        self.logger.debug(f"🔑 Identity: [yellow]{json_creds['client_email']}[/yellow]")

    async def __aenter__(self):
        self.logger.info(
            "📡 [bold white]Connecting to Google Drive API...[/bold white]"
        )
        self.aiogoogle = await self.aiogoogle.__aenter__()
        self.drive = await self.aiogoogle.discover("drive", "v3")
        self.downloader = Downloader(self.aiogoogle, self.drive, self.state)
        self.downloader.start_dl()

        self.logger.info("✨ [bold green]Connection Established[/bold green]")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.logger.info("🔌 [bold white]Closing Google Drive session...[/bold white]")
        await self.downloader.stop_dl()
        await self.aiogoogle.__aexit__(exc_type, exc_val, exc_tb)

    async def build_drive_map(
        self,
        folder_id=NEURO_FOLDER_ID,
        only_extras=False,
        only_discs=False,
        last_disc_only=False,
        disc_filter: int | list[int] | range | None = None,
    ):

        async def _fetch_recursive(
            folder_id: str, current_path: str = "root"
        ) -> list[DriveNode]:
            nodes = []
            page_token = None
            self.logger.info(
                f"🔍 [bold white]Crawling:[/bold white] [cyan]{current_path}[/cyan]"
            )

            while True:
                request = self.drive.files.list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="files(id, name, mimeType, modifiedTime), nextPageToken",
                    pageSize=1000,
                    pageToken=page_token,
                )
                response = await self.aiogoogle.as_service_account(request)
                items = response.get("files", [])

                for item in items:
                    name, drive_id = item["name"], item["id"]
                    mod_time = datetime.strptime(
                        item["modifiedTime"], "%Y-%m-%dT%H:%M:%S.%fZ"
                    )

                    if item["mimeType"] == "application/vnd.google-apps.folder":
                        folder_node = DriveFolder(name, drive_id, mod_time)
                        folder_node.children = await _fetch_recursive(
                            drive_id, f"{current_path}/{name}"
                        )
                        nodes.append(folder_node)
                    else:
                        nodes.append(DriveFile(name, drive_id, mod_time))

                page_token = response.get("nextPageToken")
                if not page_token or not items:
                    break
            return nodes

        with console.status(
            "[bold yellow]Mapping Drive ...", spinner="hearts"
        ) as status:
            self.logger.info(
                "🗺️  [bold cyan]Fetching metadata from remote Google Drive...[/bold cyan]"
            )
            # 1. Fetch Top Level
            request = self.drive.files.list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="files(id, name, mimeType, modifiedTime)",
            )
            response = await self.aiogoogle.as_service_account(request)
            raw_items = response.get("files", [])

            # 2. Advanced Filtering Logic
            filtered_items = []

            # Separate Discs and Extras
            discs_metadata = []
            extras_metadata = []

            for item in raw_items:
                disc_id = extract_disc_id(item["name"])
                if disc_id is not None:
                    discs_metadata.append((disc_id, item))
                else:
                    extras_metadata.append(item)

            # Sort Discs by numeric ID
            discs_metadata.sort(key=lambda x: x[0])

            if only_extras:
                filtered_items = extras_metadata
            elif last_disc_only:
                filtered_items = [discs_metadata[-1][1]] if discs_metadata else []
            elif only_discs:
                if disc_filter is not None:
                    # Normalize filter to a set for O(1) lookup
                    if isinstance(disc_filter, int):
                        allowed = {disc_filter}
                    else:
                        allowed = set(disc_filter)

                    filtered_items = [
                        item for d_id, item in discs_metadata if d_id in allowed
                    ]
                else:
                    filtered_items = [item for _, item in discs_metadata]
            else:
                # Default: Crawl everything
                filtered_items = extras_metadata + [item for _, item in discs_metadata]

            # 3. Execution
            all_nodes = []
            for item in filtered_items:
                name, drive_id = item["name"], item["id"]
                mod_time = datetime.strptime(
                    item["modifiedTime"], "%Y-%m-%dT%H:%M:%S.%fZ"
                )

                if item["mimeType"] == "application/vnd.google-apps.folder":
                    node = DriveFolder(name, drive_id, mod_time)
                    node.children = await _fetch_recursive(drive_id, f"root/{name}")
                    all_nodes.append(node)
                else:
                    all_nodes.append(DriveFile(name, drive_id, mod_time))

            status.update("[bold green]Finalizing Sort...")
            self.extras = [n for n in all_nodes if not n.name.startswith("DISC")]
            self.discs = [n for n in all_nodes if n.name.startswith("DISC")]
            self.song_entries = self.entries_from_scanned_songs()

        # --- Summary Table ---
        summary = Table(
            title="\n📊 [bold]Drive Mapping Summary[/bold]", box=None, padding=(0, 2)
        )
        summary.add_column("Category", style="bold white")
        summary.add_column("Count", justify="right", style="green")
        summary.add_row("💽 Discs", str(len(self.discs)))
        summary.add_row("📦 Extras", str(len(self.extras)))
        console.print(summary)

    def clone_skeleton(self):
        self.logger.info(
            "🗂️  [bold cyan]Cloning remote directory skeleton to local disk...[/bold cyan]"
        )

        def _recurse(node: DriveNode, current_path: Path) -> Iterator[Path]:
            # 1. Safety check: we only care about folders for a skeleton
            if isinstance(node, DriveFile):
                return

            target_path = current_path / node.name

            # 2. Yield THIS directory so it gets created
            yield target_path

            # 3. Recurse into children to find sub-directories
            for child in node.children:
                yield from _recurse(child, target_path)

        leaf_paths = _recurse(
            DriveFolder(
                self.state.library_path.name,
                "bogus",
                datetime.now(),
                self.discs + self.extras,
            ),
            self.state.library_path.parent,
        )

        count = 0
        for leaf in leaf_paths:
            self.logger.debug(f"Creating {leaf}")
            leaf.mkdir(parents=True, exist_ok=True)
            count += 1

        self.logger.debug(f"📁 [dim]Created {count} directories[/dim]")

    async def download_extras(self):
        self.logger.info(f"📥 [bold cyan]Queuing extras for download...[/bold cyan]")

        async def _save_local(data: bytes, path: AsyncPath):
            await path.write_bytes(data)

        def _recurse(
            node: DriveNode, current_path: Path
        ) -> Iterator[tuple[DriveNode, Path]]:
            """
            Recursively yields the full path of every file found in the tree.
            """
            target_path = current_path / node.name

            # Base Case: If the node is a file, yield its full path and stop recursing
            if isinstance(node, DriveFile):
                yield node, target_path
                return

            # Recursive Step: If it's a folder, dive into all children
            # This works regardless of whether the folder is a 'leaf' or not
            for child in node.children:
                yield from _recurse(child, target_path)

        for node in self.extras:
            for drive_info, path in _recurse(node, self.state.library_path):
                await self.downloader.queue.put(
                    DownloadTask(
                        drive_info.drive_id,
                        # FIX: Bind the current loop value 'path' to 'p'
                        lambda x, p=path: _save_local(x, AsyncPath(p)),
                        self.state.retries,
                    )
                )
        self.logger.debug(f"✅ [dim]Finished enqueuing extra files[/dim]")

    async def download_all_songs(self):
        self.logger.info(
            f"🎵 [bold cyan]Queuing songs for download & retagging (Total {len(self.discs)} discs to download) ...[/bold cyan]"
        )

        def _recurse(
            node: DriveNode, current_path: Path
        ) -> Iterator[tuple[DriveNode, Path]]:
            """
            Recursively yields the full path of every file found in the tree.
            """
            target_path = current_path / node.name

            # Base Case: If the node is a file, yield its full path and stop recursing
            if isinstance(node, DriveFile):
                yield node, target_path
                return

            # Recursive Step: If it's a folder, dive into all children
            # This works regardless of whether the folder is a 'leaf' or not
            for child in node.children:
                yield from _recurse(child, target_path)

        for disc in self.discs:
            for drive_info, path in _recurse(disc, self.state.library_path):
                await self.downloader.queue.put(
                    DownloadTask(
                        drive_info.drive_id,
                        # FIX: Bind the current loop values to default arguments
                        lambda x, p=path: tag_mp3(
                            x,
                            AsyncPath(p),
                            self.logger,
                        ),
                        self.state.retries,
                    )
                )
        self.logger.debug(f"✅ [dim]Finished queueing songs[/dim]")

    def save_db(self):
        self.logger.info(
            "💾 [bold green]Saving local track database (info.db)...[/bold green]"
        )
        db_file = self.state.library_path / "info.db"
        for e in self.song_entries[:5]:  # Check the first 5
            self.logger.debug(f"DEBUG: Vol {e.volume}, Trk {e.track}, TS {e.timestamp}")
        save_entries(db_file, self.song_entries)

    async def setup_library(self):
        self.logger.info(
            "🛠️  [bold yellow]Beginning initial library download[/bold yellow]"
        )
        await self.build_drive_map()
        # self.clone_skeleton()
        # await self.download_all_songs()
        # await self.download_extras()
        self.save_db()

    def entries_from_scanned_songs(self):
        self.logger.debug("🧮 [dim]Extracting song entries from mapped nodes...[/dim]")
        entries = []
        for disc in self.discs:
            disc_id = extract_disc_id(disc.name)
            for track in disc.children:
                if track.name.endswith(".mp3"):
                    track_id = extract_track_id(track.name)
                    self.logger.debug(f"Adding Vol: {disc_id}, Track: {track_id}")
                    entries.append(
                        SongEntry(
                            disc_id,
                            track_id,
                            int(
                                track.modified_timestamps.replace(
                                    tzinfo=timezone.utc
                                ).timestamp()
                            ),
                        )
                    )
        return entries

    async def sync_library(self, folder_id=NEURO_FOLDER_ID):
        self.logger.info(
            "🔍 [bold cyan]Validating local database against remote Drive...[/bold cyan]"
        )
        song_db = SongDB(self.state.library_path / "info.db")

        request = self.drive.files.list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="files(id, name, mimeType, modifiedTime)",
        )
        response = await self.aiogoogle.as_service_account(request)
        top_level_items = response.get("files", [])

        last_disc = [
            sorted(top_level_items, key=lambda x: extract_disc_id(x["name"]))[-1]
        ]
        last_disc_no = extract_disc_id(
            last_disc[0]["name"]
        )  # Note: I also applied the fix here for your list indexing error

        new_disc = last_disc_no > len(song_db.volumes)

        if new_disc:
            self.logger.info(
                f"💿 [bold magenta]Detected missing volumes! Fetching up to Volume {last_disc_no}...[/bold magenta]"
            )
            await self.build_drive_map(
                only_discs=True, disc_filter=[last_disc_no, last_disc_no - 1]
            )
            await self.clone_skeleton()
            await self.download_all_songs()
            song_db.update(self.entries_from_scanned_songs())
        else:
            self.logger.info(
                f"💿 [bold cyan]Checking latest volume (Volume {last_disc_no}) for newly added tracks...[/bold cyan]"
            )
            await self.build_drive_map(only_discs=True, last_disc_only=True)
            vol_id = extract_disc_id(self.discs[0].name)

            # original_count = len(self.discs[0].children)
            self.discs[0].children = [
                song
                for song in self.discs[0].children
                if Seeker(vol_id, extract_track_id(song.name)) not in song_db
                or song.modified_timestamps.replace(tzinfo=timezone.utc).timestamp()
                - song_db[Seeker(vol_id, extract_track_id(song.name))].timestamp
                > 3
            ]

            diff = len(self.discs[0].children)
            if diff > 0:
                self.logger.info(
                    f"📥 [bold yellow]Found {diff} new tracks in existing volume. Synchronizing...[/bold yellow]"
                )
                await self.download_all_songs()
                song_db.update(self.entries_from_scanned_songs())
            else:
                self.logger.info(
                    "👍 [bold green]Volume is fully up-to-date. No new tracks found.[/bold green]"
                )

        self.logger.info(
            "💾 [bold green]Writing updates to local database...[/bold green]"
        )
        song_db.save()

    async def sync_extras(self):
        self.logger.info(
            "📦 [bold cyan]Re-syncing extra library contents...[/bold cyan]"
        )
        await self.build_drive_map(only_extras=True)
        await self.clone_skeleton()
        await self.download_extras()
