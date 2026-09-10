"""Folder traversal and the folder-membership guard.

Knows nothing about MCP: takes a Drive service and plain arguments, returns
plain dataclasses.
"""

from collections import deque
from dataclasses import dataclass, field

from ..errors import DriveAPIError, FileNotFound
from .client import call

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

_LIST_FIELDS = (
    "nextPageToken, files(id, name, mimeType, size, modifiedTime, parents, "
    "shortcutDetails(targetId, targetMimeType))"
)

_SHARED_DRIVE_ARGS = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}


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
    children: list[dict] = []
    page_token: str | None = None

    while True:
        try:
            response = await call(
                service.files().list(
                    q=f"'{parent_id}' in parents and trashed = false",
                    fields=_LIST_FIELDS,
                    pageToken=page_token,
                    **_SHARED_DRIVE_ARGS,
                )
            )
        except Exception as exc:
            raise DriveAPIError(
                f"Could not list children of {parent_id}: {exc}"
            ) from exc

        children.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return children


async def list_folder_tree(service, folder_id: str) -> list[DriveFile]:
    # Breadth-first traversal of a folder tree, returning all files and folders
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
