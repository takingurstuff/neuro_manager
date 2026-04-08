import struct
import random
import hashlib
from pathlib import Path
from typing import Iterable
from dataclasses import dataclass
from functools import total_ordering

HASH_ENTRY_STRUCT = (
    "<iiq"  # Volume, Track, Last Modified Timestamp (google drive, UTC, Unix Epoch)
)
HASH_ENTRY_SIZE = 16
HASH_MN = b"hshf"  # MAGIC NUMBER, DO NOT TOUCH
OFFSET_TABLE_ENTRY_STRUCT = (
    "<iiii"  # Unit: Entries (not raw offset), Items: Volume, Start, End, Items
)
OFFSET_TABLE_ENTRY_SIZE = 16


@dataclass
@total_ordering
class SongEntry:
    volume: int
    track: int
    timestamp: int

    @property
    def raw(self):
        return struct.pack(HASH_ENTRY_STRUCT, self.volume, self.track, self.timestamp)

    def __lt__(self, other):
        if not isinstance(other, SongEntry):
            return NotImplemented
        return (self.volume, self.track) < (other.volume, other.track)

    def __eq__(self, other):
        if not isinstance(other, SongEntry):
            return NotImplemented
        return (self.volume, self.track) == (other.volume, other.track)


@dataclass
class Seeker:
    volume: int | None
    track: int | None


@dataclass(frozen=True)
class Volume:
    volume: int
    start: int
    end: int
    size: int


class VolumeMap:
    __tkn = random.randbytes(64)

    def __init__(self, pointers: Iterable[tuple[int, int, int, int]], token: bytes):
        """
        DO NOT CALL DIRECTLY
        """
        if token != self.__tkn:
            raise RuntimeError(
                "Please Do not instantiate this class directly, use either the from_objects or from_bytes constructor"
            )
        self.pointers = sorted(list(pointers), key=lambda x: x[0])
        self.last_vol = max(map(lambda x: x[0], pointers))

    @property
    def raw(self):
        return b"".join(
            struct.pack(OFFSET_TABLE_ENTRY_STRUCT, *ptr) for ptr in self.pointers
        )

    def __repr__(self):
        return "\n".join(
            f"Volume {vol}:\n start {start}\nend {end}\nsize {size} tracks\n"
            for vol, start, end, size in self.pointers
        )

    @classmethod
    def from_objects(cls, volumes: list[Volume]):
        return cls(
            map(lambda x: (x.volume, x.start, x.end, x.size), volumes), cls.__tkn
        )

    @classmethod
    def from_bytes(cls, raw_table: bytes):
        return cls(
            struct.iter_unpack(OFFSET_TABLE_ENTRY_STRUCT, raw_table),
            cls.__tkn,
        )

    @classmethod
    def from_entries(cls, md5: list[SongEntry]):
        # 1. Initialize our "state" containers
        counts = {}
        max_vol = 0

        # 2. Single pass to find max and count frequencies (O(N))
        for entry in md5:
            v = entry.volume
            counts[v] = counts.get(v, 0) + 1
            if v > max_vol:
                max_vol = v

        # 3. Build the volumes list using a running "offset" (The State!)
        volumes = []
        current_offset = 0

        for vol_id in range(1, max_vol + 1):
            size = counts.get(vol_id, 0)

            # We represent the state directly as a variable that we mutate
            # instead of recalculating sum() every iteration.
            volumes.append((vol_id, current_offset, current_offset + size, size))

            # Update the state for the next iteration
            current_offset += size

        return cls(volumes, cls.__tkn)

    def __iter__(self):
        """Yields Volume objects for every entry in the map."""
        for p in self.pointers:
            yield Volume(*p)

    def __getitem__(self, key):
        """Standard indexing: vmap[1] or vmap[1:3]."""
        if isinstance(key, slice):
            start = (key.start - 1) if key.start is not None else None
            stop = (key.stop - 1) if key.stop is not None else None
            return VolumeMap(self.pointers[start : stop : key.step], self.__tkn)

        if isinstance(key, int):
            if key < 1:
                raise IndexError("Volume numbers are 1-indexed.")
            return Volume(*self.pointers[key - 1])

        raise TypeError(f"Invalid index type: {type(key).__name__}")

    def __len__(self):
        return len(self.pointers)

    def __getattr__(self, name: str):
        """
        Allows access via vmap.vol_1, vmap.vol_2, etc.
        """
        if name.startswith("vol_"):
            try:
                vol_num = int(name.split("_")[1])
                return self[vol_num]  # Reuses __getitem__ logic
            except (ValueError, IndexError):
                pass

        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )

    def add_record(self, volume_id: int):
        """Adjusts offsets to accommodate a new entry in a specific volume."""
        found = False
        for i, (vol, start, end, size) in enumerate(self.pointers):
            if vol == volume_id:
                # Increment end and size for the target volume
                self.pointers[i] = (vol, start, end + 1, size + 1)
                found = True
            elif vol > volume_id:
                # Shift start and end for all subsequent volumes
                self.pointers[i] = (vol, start + 1, end + 1, size)

        if not found:
            # If it's a brand new volume at the end
            start = self.pointers[-1][2] if self.pointers else 0
            self.pointers.append((volume_id, start, start + 1, 1))
            self.last_vol = volume_id


class SongDB:
    def __init__(self, song_db_path: Path):
        self.path = song_db_path
        with open(song_db_path, "rb") as f:
            mn = f.read(4)
            if mn != HASH_MN:
                raise RuntimeError("Corrupt Hash Database file, OR wrong file")
            _offset_table_size = struct.unpack("<i", f.read(4))[0]
            self.volumes = VolumeMap.from_bytes(f.read(_offset_table_size))
            self._entries = bytearray(f.read())

    def __len__(self):
        return len(self._entries) // HASH_ENTRY_SIZE

    def _get_entry(self, index):
        """Helper to unpack a single entry by index."""
        offset = index * HASH_ENTRY_SIZE
        data = self._entries[offset : offset + HASH_ENTRY_SIZE]
        return SongEntry(*struct.unpack(HASH_ENTRY_STRUCT, data))

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]

    def iter_bytes(self):
        for i in range(0, len(self._entries), HASH_ENTRY_SIZE):
            yield self._entries[i : i + HASH_ENTRY_SIZE]

    def _trksearch(self, volume: int, track: int):
        # 1. Use the VolumeMap to find the specific range for this volume
        try:
            vol_info = self.volumes[volume]
            low = vol_info.start
            high = vol_info.end - 1  # end is exclusive in VolumeMap
        except (IndexError, KeyError):
            raise IndexError(f"Volume {volume} not found in index.")

        target_track = track

        # 2. Binary search only within the slice of the volume
        while low <= high:
            mid = (low + high) // 2
            tind = mid * HASH_ENTRY_SIZE

            # Since we are already inside the correct volume range,
            # we technically only need to compare the track number.
            # However, reading both keeps the logic robust.
            # Assuming track is at offset +4 as per your original code
            current_trk = int.from_bytes(self._entries[tind + 4 : tind + 8], "little")

            if current_trk == target_track:
                return self._get_entry(mid)
            elif current_trk < target_track:
                low = mid + 1
            else:
                high = mid - 1

        raise IndexError(f"Track {track} not found in Volume {volume}.")

    def _volsearch(self, volume: int):
        offsets = self.volumes[volume]
        return self[offsets.start : offsets.end]

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            return [self._get_entry(i) for i in range(start, stop, step)]

        elif isinstance(key, int):
            if key < 0:
                key += len(self)
            if key < 0 or key >= len(self):
                raise IndexError("Database index out of range")
            return self._get_entry(key)

        elif isinstance(key, Seeker):
            if key.volume and key.track:
                return self._trksearch(key.volume, key.track)
            if key.volume:
                start, end = self._volsearch(key.volume)
                return self[start:end]

        else:
            raise TypeError(f"Invalid argument type: {type(key).__name__}")

    def __contains__(self, key: Seeker | SongEntry | tuple[int, int]):
        if isinstance(key, tuple):
            return Seeker(*key) in self
        if not isinstance(key, (Seeker, SongEntry)):
            raise TypeError(
                "Can only check using the Seeker object, the MD5 object, OR a tuple of (volume, track)"
            )
        try:
            self._trksearch(key.volume, key.track)
            return True
        except IndexError:
            return False

    def add_song(self, entry: SongEntry):
        """Adds a single song entry and maintains sort order."""
        try:
            vol_info = self.volumes[entry.volume]
            # Find insertion point within the volume via track number
            low = vol_info.start
            high = vol_info.end - 1
            insert_idx = vol_info.end  # Default to end of volume

            while low <= high:
                mid = (low + high) // 2
                t_off = mid * HASH_ENTRY_SIZE
                current_trk = struct.unpack("<i", self._entries[t_off + 4 : t_off + 8])[
                    0
                ]

                if current_trk == entry.track:
                    raise ValueError(
                        f"Track {entry.track} already exists in Volume {entry.volume}"
                    )
                if current_trk < entry.track:
                    low = mid + 1
                else:
                    insert_idx = mid
                    high = mid - 1
        except (IndexError, KeyError):
            # Volume doesn't exist, append to end
            insert_idx = len(self._entries) // HASH_ENTRY_SIZE

        # Splice the new entry into the bytearray
        offset = insert_idx * HASH_ENTRY_SIZE
        self._entries[offset:offset] = entry.raw

        # Update the volume map offsets
        self.volumes.add_record(entry.volume)

    def add_volume(self, entries: list[SongEntry]):
        """Appends a contiguous new volume."""
        if not entries:
            return

        new_vol_id = entries[0].volume
        if new_vol_id != self.volumes.last_vol + 1:
            raise ValueError(f"New volume must be {self.volumes.last_vol + 1}")

        if not all(e.volume == new_vol_id for e in entries):
            raise ValueError("All entries in add_volume must share the same volume ID")

        # Sort the incoming volume tracks
        entries.sort()

        start_idx = len(self._entries) // HASH_ENTRY_SIZE
        new_data = b"".join(e.raw for e in entries)
        self._entries.extend(new_data)

        # Update map
        size = len(entries)
        self.volumes.pointers.append((new_vol_id, start_idx, start_idx + size, size))
        self.volumes.last_vol = new_vol_id

    def update(self, entries: list[SongEntry]):
        """
        Updates the DB with a list of entries.
        1. Validates internal contiguity.
        2. Validates contiguity with existing data.
        3. Overwrites overlaps and appends new data.
        """
        if not entries:
            return

        # 1. Sort and Validate Internal Contiguity
        entries.sort()
        for i in range(len(entries) - 1):
            curr, nxt = entries[i], entries[i + 1]
            # Check if volume/track sequence is broken
            if nxt.volume == curr.volume:
                if nxt.track != curr.track + 1:
                    raise ValueError(
                        f"Tracks not contiguous: Vol {curr.volume} Trk {curr.track} -> {nxt.track}"
                    )
            elif nxt.volume == curr.volume + 1:
                if nxt.track != 1:
                    raise ValueError(f"New volume {nxt.volume} must start at track 1")
            else:
                raise ValueError(
                    f"Volumes not contiguous: {curr.volume} -> {nxt.volume}"
                )

        # 2. Validate Contiguity with Database
        db_len = len(self)
        if db_len > 0:
            last_entry = self._get_entry(db_len - 1)
            first_in = entries[0]

            # Ensure input isn't skipping a gap in the sequence
            if first_in.volume > last_entry.volume + 1:
                raise ValueError("Input volume creates a gap in the database.")
            if (
                first_in.volume == last_entry.volume
                and first_in.track > last_entry.track + 1
            ):
                raise ValueError("Input tracks create a gap in the database.")
            if first_in < last_entry:
                # If the first item of input is significantly behind the last item of DB
                # beyond just the last volume's overlap, we reject for safety.
                if first_in.volume < last_entry.volume:
                    raise ValueError("Input starts before the current last volume.")

        # 3. Find the overlap point
        # We look for the index in _entries where the first_in (volume, track) exists
        overlap_idx = None
        try:
            # Reusing _trksearch logic via Seeker or direct comparison
            target = entries[0]
            vol_info = self.volumes[target.volume]
            # Binary search to find if the starting track of the input exists in the DB
            low, high = vol_info.start, vol_info.end - 1
            while low <= high:
                mid = (low + high) // 2
                t_off = mid * HASH_ENTRY_SIZE
                c_vol, c_trk = struct.unpack("<ii", self._entries[t_off : t_off + 8])
                if c_vol == target.volume and c_trk == target.track:
                    overlap_idx = mid
                    break
                elif (c_vol, c_trk) < (target.volume, target.track):
                    low = mid + 1
                else:
                    high = mid - 1
        except (IndexError, KeyError):
            # No overlap, starting fresh at the end
            overlap_idx = db_len

        if overlap_idx is None:
            raise ValueError("Cannot Insert in the middle of a volume")

        # 4. Perform the Update (Bulk Write)
        insertion_offset = overlap_idx * HASH_ENTRY_SIZE
        new_payload = b"".join(e.raw for e in entries)

        # This replaces everything from the overlap point forward with the new list
        self._entries[insertion_offset:] = new_payload

        # 5. Rebuild VolumeMap (Most efficient for bulk updates)
        # Instead of incremental shifts, we re-index from the bytearray
        self.volumes = VolumeMap.from_entries(
            [
                SongEntry(
                    *struct.unpack(
                        HASH_ENTRY_STRUCT, self._entries[i : i + HASH_ENTRY_SIZE]
                    )
                )
                for i in range(0, len(self._entries), HASH_ENTRY_SIZE)
            ]
        )

    def update_entry(
        self,
        key: Seeker | SongEntry | tuple[int, int] | int,
        timestamp: int | None = None,
    ):
        """
        Updates the timestamp of a single entry.
        'key' can be any index/lookup format supported by __getitem__.
        If 'key' is a SongEntry, its internal timestamp is used.
        Otherwise, the 'timestamp' argument must be provided.
        """
        # 1. Identify the target entry to get its index
        # We need the index specifically to calculate the byte offset
        if isinstance(key, int):
            idx = key if key >= 0 else len(self) + key
        elif isinstance(key, (Seeker, SongEntry, tuple)):
            # Reuse _trksearch logic to find the specific index
            if isinstance(key, tuple):
                key = Seeker(*key)

            # Binary search to find the index (O(log N))
            vol_info = self.volumes[key.volume]
            low, high = vol_info.start, vol_info.end - 1
            idx = None

            while low <= high:
                mid = (low + high) // 2
                t_off = mid * HASH_ENTRY_SIZE
                # Unpack vol and trk to verify
                v, t = struct.unpack("<ii", self._entries[t_off : t_off + 8])
                if (v, t) == (key.volume, key.track):
                    idx = mid
                    break
                elif (v, t) < (key.volume, key.track):
                    low = mid + 1
                else:
                    high = mid - 1

            if idx is None:
                raise IndexError(f"Entry {key} not found.")
        else:
            raise TypeError(f"Unsupported key type: {type(key).__name__}")

        # 2. Determine the new timestamp
        if isinstance(key, SongEntry):
            new_ts = key.timestamp
        else:
            if timestamp is None:
                raise ValueError("Timestamp is required for non-SongEntry lookups.")
            new_ts = timestamp

        # 3. Update bytes in-place
        # HASH_ENTRY_STRUCT is "<iiq", timestamp is the 'q' (8 bytes) at offset 8
        struct.pack_into("<q", self._entries, (idx * HASH_ENTRY_SIZE) + 8, new_ts)

    def save(self):
        """Overwrites the original file with current state."""
        raw_map = self.volumes.raw
        header = HASH_MN + struct.pack("<i", len(raw_map))
        self.path.write_bytes(header + raw_map + self._entries)


def save_entries(song_db_path: Path, entries: list[SongEntry]):
    entries.sort()
    hash_entries = b"".join(entry.raw for entry in entries)
    offset_table = VolumeMap.from_entries(entries).raw
    data = HASH_MN + struct.pack("<i", len(offset_table)) + offset_table + hash_entries
    song_db_path.write_bytes(data)
