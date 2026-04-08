from pathlib import Path
from dataclasses import dataclass


# 1. Setup the State and Global App
@dataclass
class State:
    verbose: bool
    concurrent_downloads: int
    retries: int
    library_path: Path
    credentials_path: Path
