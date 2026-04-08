from datetime import datetime


class DriveNode:
    """Base for anything in Google Drive."""

    def __init__(self, name: str, drive_id: str, modified_timestamps: datetime):
        self.name = name
        self.drive_id = drive_id
        self.modified_timestamps = modified_timestamps


class DriveFile(DriveNode):
    """Represents a file to be downloaded."""

    pass


class DriveFolder(DriveNode):
    """Represents a directory that can contain more items."""

    def __init__(
        self,
        name: str,
        drive_id: str,
        modified_timestamps: datetime,
        children: list[DriveNode] | None = None,
    ):
        super().__init__(name, drive_id, modified_timestamps)
        self.children = children

    def add(self, node):
        self.children.append(node)
