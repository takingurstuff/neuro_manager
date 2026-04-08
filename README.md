# Neuro Manager

Neuro Manager is a specialized CLI utility designed to archive and manage the Neuro-sama karaoke collections from Google Drive. It focuses on maintaining a synchronized local library with accurate metadata and high-integrity downloads.

## Features

- **Asynchronous Downloads**: Utilizes `aiogoogle` and `aiopath`, concurrent file transfers.
- **Smart Syncing**: Compares local timestamps against remote files to ensure only new or modified tracks are downloaded.
- **Better Tagging (Opinionated)**: re-tags the file using a new scheme, filling in more fields while also utilizing ID3v2.4 native multi value tags (do note that this may cause compatibility issues), pulls from the same COMM::ved frame for base metadata.
- **Extra Content**: Manages extra content including the readme file, the cover images, and other files.
- **Robust CLI**: Built with `typer` and `rich` for a clean, informative, and user-friendly interface.

## Installation

This project requires **Python 3.13** or higher and uses `uv` for dependency management.

```bash
# Clone the repository
git clone <repository-url>
cd neuro-manager

# Install dependencies
uv sync
```

## Setup & Authentication

Neuro Manager requires a Google Service Account to access the archive.

1.  [Obtain a Google Service Account JSON credentials file](service_account.md).
2.  Save the file as `creds.json` in the project root (default location) or specify the path using the `--service-account` option.

## Usage

### Global Options

These options can be applied to any command:

- `-l, --library-path`: Specify where the library should be saved (defaults to current directory).
- `-s, --service-account`: Path to your [Google Service Account JSON](service_account.md) (defauls to creds.json under current directory).
- `-t, --threads`: Number of concurrent downloads (default: 5).
- `-v, --verbose`: Enable detailed debug logging.

### Commands

#### Initial Setup

Create the library, this downloads all songs and sets up tracking for songs:

```bash
neuro-manager create
```

#### Synchronize Library

Checks if any songs has been updated and wether there are new songs, pulls only the updated / new songs:

```bash
neuro-manager update
```

#### Download Extras

Fully download all extra content (anything that is not songs):

```bash
neuro-manager download-extras
```

## File Tracking Info

The app utilizes a custom binary format to track files by their last modified timestamps as obtained from google drive

### File Structure (In Order)

- **Header:** 4 byte magic number `hshf` one int4 denoting the size of the offset table
- **Offset Table Size:** `int4`, size in bytes for the offset table
- **Offset Table:** because this file is made up of structs packed togther sorted ascending by primary key (disc number) and secondary key (track number), tracks belonging to the same disc is grouped togther, this table contains information for each volume. Schema:
  |dtype|description|
  |-----|-----------|
  |int4|disc number|
  |int4|start offset (unit: tracks)|
  |int4|end offset (unit: tracks)|
  |int4|track count (unit: track)|
- **Song Entry:** This is the entry for one song, duplicate for multiple songs, these must be sorted by disc number then track number, sorting by any order will cause the seeking and insertion to fail
  Schema:
  |dtype|description|
  |-----|-----------|
  |int4|Disc Number|
  |int4|Track Number|
  |int64|Unix Epoch Timestamp of last modification|

## License

[Specify License Type, e.g., MIT]
