"""Folder traversal and the folder-membership guard.

Knows nothing about MCP: takes a Drive service and plain arguments, returns
plain dataclasses.
"""

from collections import deque
from dataclasses import dataclass, field

from ..errors import DriveAPIError, FileNotFound, FileOutsideAllowedFolder
from .client import call

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Fields must be requested explicitly. The default response carries only id,
# name and mimeType — size and parents come back missing rather than erroring,
# which is a quiet way to break the caller.
_LIST_FIELDS = (
    "nextPageToken, files(id, name, mimeType, size, modifiedTime, parents, "
    "shortcutDetails(targetId, targetMimeType))"
)
_GET_FIELDS = "id, name, mimeType, parents"

# Works for both My Drive and Shared Drives. Harmless for the former, and
# without them a folder on a Shared Drive silently lists as empty.
_SHARED_DRIVE_ARGS = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}

# Guards against a malformed or hostile parent chain. Drive allows a file to
# have several parents, so "walk upwards" is a graph traversal, not a line.
MAX_PARENT_DEPTH = 25


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    size: int | None
    modified_time: str | None
    parents: list[str] = field(default_factory=list)
    path: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_shortcut(self) -> bool:
        return self.mime_type == SHORTCUT_MIME


def _to_drive_file(raw: dict, path: str) -> DriveFile:
    size = raw.get("size")
    return DriveFile(
        id=raw["id"],
        name=raw.get("name", "(untitled)"),
        mime_type=raw.get("mimeType", "application/octet-stream"),
        # Native Google formats report no size at all.
        size=int(size) if size is not None else None,
        modified_time=raw.get("modifiedTime"),
        parents=raw.get("parents", []) or [],
        path=path,
    )


async def _list_children(service, parent_id: str) -> list[dict]:
    """Every child of one folder, following pagination to the end.

    Drive caps a response well below what a folder may hold, so the loop is
    not optional even when the folder in front of you is small.
    """
    children: list[dict] = []
    page_token = None

    while True:
        try:
            response = await call(
                service.files().list(
                    q=f"'{parent_id}' in parents and trashed = false",
                    fields=_LIST_FIELDS,
                    pageSize=1000,
                    pageToken=page_token,
                    **_SHARED_DRIVE_ARGS,
                )
            )
        except Exception as exc:
            raise DriveAPIError(f"Could not list folder {parent_id}: {exc}") from exc

        children.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return children


async def list_folder_tree(service, folder_id: str) -> list[DriveFile]:
    """Every file under `folder_id`, including files in nested subfolders.

    Breadth-first, carrying each folder's path down to its children so paths
    are built during the walk rather than reconstructed from the parent graph
    afterwards. Folders themselves are not returned — only the files in them.
    """
    files: list[DriveFile] = []
    visited: set[str] = {folder_id}
    queue: deque[tuple[str, str]] = deque([(folder_id, "")])

    while queue:
        current_id, prefix = queue.popleft()

        for raw in await _list_children(service, current_id):
            name = raw.get("name", "(untitled)")
            path = f"{prefix}/{name}" if prefix else name

            if raw.get("mimeType") == FOLDER_MIME:
                # A file may have several parents, so the same folder can be
                # reached twice. Without this the walk can loop forever.
                if raw["id"] not in visited:
                    visited.add(raw["id"])
                    queue.append((raw["id"], path))
                continue

            files.append(_to_drive_file(raw, path))

    return files


async def get_file_metadata(service, file_id: str) -> DriveFile:
    """Fetch one file's metadata, with a readable error when it is missing."""
    try:
        raw = await call(
            service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, modifiedTime, parents",
                supportsAllDrives=True,
            )
        )
    except Exception as exc:
        if "404" in str(exc) or "notFound" in str(exc):
            raise FileNotFound(f"No file with id {file_id}.") from exc
        raise DriveAPIError(f"Could not read metadata for {file_id}: {exc}") from exc

    return _to_drive_file(raw, path=raw.get("name", ""))


async def assert_within_folder(service, file_id: str, root_folder_id: str) -> None:
    """Raise unless `file_id` is a descendant of `root_folder_id`.

    THIS IS THE SECURITY BOUNDARY OF THE SERVICE.

    The drive.readonly scope grants read access to the user's entire Drive —
    Google offers no per-folder scope — so "only the shared folder" is a
    property this function enforces and nothing else does. Without it,
    read_file would happily fetch any file the user can see.

    Walks upward through `parents`. That is a graph rather than a chain,
    because Drive lets a file sit in several folders at once.
    """
    if file_id == root_folder_id:
        return

    seen: set[str] = set()
    frontier = deque([(file_id, 0)])

    while frontier:
        current_id, depth = frontier.popleft()
        if current_id in seen or depth > MAX_PARENT_DEPTH:
            continue
        seen.add(current_id)

        try:
            raw = await call(
                service.files().get(
                    fileId=current_id,
                    fields=_GET_FIELDS,
                    supportsAllDrives=True,
                )
            )
        except Exception as exc:
            if "404" in str(exc) or "notFound" in str(exc):
                raise FileNotFound(f"No file with id {file_id}.") from exc
            raise DriveAPIError(f"Could not verify {file_id}: {exc}") from exc

        for parent_id in raw.get("parents", []) or []:
            if parent_id == root_folder_id:
                return
            frontier.append((parent_id, depth + 1))

    raise FileOutsideAllowedFolder(
        f"File {file_id} is not inside the shared folder this server is allowed to read."
    )
