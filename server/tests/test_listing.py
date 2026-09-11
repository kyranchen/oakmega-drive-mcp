"""Folder traversal and the folder-membership guard.

The traversal tests use a fake Drive service so pagination and cycle handling
can be exercised without a folder that is large enough to paginate.
"""

import pytest

from app.drive.listing import FOLDER_MIME, assert_within_folder, list_folder_tree
from app.errors import FileOutsideAllowedFolder


class FakeDrive:
    """Minimal stand-in for a Drive service.

    `children` maps a folder id to the raw dicts the API would return; pages
    splits a folder's children across responses so the pagination loop is
    actually driven rather than assumed.
    """

    def __init__(
        self, children: dict, parents: dict | None = None, page_size: int = 100
    ):
        self._children = children
        self._parents = parents or {}
        self._page_size = page_size
        self.list_calls = 0

    def files(self):
        return self

    def list(self, *, q, pageToken=None, **kw):
        self.list_calls += 1
        parent = q.split("'")[1]
        items = self._children.get(parent, [])
        start = int(pageToken or 0)
        page = items[start : start + self._page_size]
        next_start = start + self._page_size
        return _Request(
            {
                "files": page,
                **(
                    {"nextPageToken": str(next_start)}
                    if next_start < len(items)
                    else {}
                ),
            }
        )

    def get(self, *, fileId, **kw):
        if fileId not in self._parents:
            raise RuntimeError('{"error": {"code": 404, "message": "notFound"}}')
        return _Request(
            {"id": fileId, "name": fileId, "parents": self._parents[fileId]}
        )


class _Request:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


def _file(fid, name, mime="application/pdf"):
    return {"id": fid, "name": name, "mimeType": mime, "size": "10"}


def _folder(fid, name):
    return {"id": fid, "name": name, "mimeType": FOLDER_MIME}


class TestTraversal:
    async def test_pagination_is_followed(self):
        """The shared folder holds two files, so nothing here would page. The
        loop still has to exist, and this is what proves it does."""
        service = FakeDrive(
            {"root": [_file(f"f{i}", f"file{i}.pdf") for i in range(25)]}, page_size=10
        )

        files = await list_folder_tree(service, "root")

        assert len(files) == 25
        assert service.list_calls == 3, "should have followed nextPageToken twice"

    async def test_subfolders_are_walked_and_paths_carried_down(self):
        service = FakeDrive(
            {
                "root": [_folder("sub", "Invoices"), _file("a", "top.pdf")],
                "sub": [_file("b", "nested.pdf")],
            }
        )

        by_name = {f.name: f for f in await list_folder_tree(service, "root")}

        assert by_name["top.pdf"].path == "top.pdf"
        assert by_name["nested.pdf"].path == "Invoices/nested.pdf"

    async def test_folders_are_not_returned_as_readable_files(self):
        """A folder listed as a file would be offered for reading, and
        get_media on it fails with nothing explaining why."""
        service = FakeDrive({"root": [_folder("sub", "Invoices")], "sub": []})

        assert await list_folder_tree(service, "root") == []

    async def test_a_parent_cycle_terminates(self):
        """Drive allows several parents, so a walk can arrive back where it
        started. Without the visited set this would not return."""
        service = FakeDrive(
            {
                "root": [_folder("a", "A")],
                "a": [_folder("root", "Root again"), _file("x", "file.pdf")],
            }
        )

        files = await list_folder_tree(service, "root")

        assert [f.name for f in files] == ["file.pdf"]


class TestFolderBoundary:
    """The drive.readonly scope reaches the user's entire Drive — Google has no
    per-folder scope — so this function is what limits reads to the shared
    folder, and nothing else does.

    It matters because read_file's argument is filled in by the model, and the
    model's input includes text it has just read out of a file.
    """

    async def test_a_direct_child_is_allowed(self):
        service = FakeDrive({}, parents={"file": ["root"]})
        await assert_within_folder(service, "file", "root")

    async def test_a_deeply_nested_descendant_is_allowed(self):
        service = FakeDrive(
            {}, parents={"file": ["sub2"], "sub2": ["sub1"], "sub1": ["root"]}
        )
        await assert_within_folder(service, "file", "root")

    async def test_a_file_elsewhere_in_the_drive_is_refused(self):
        """Walks up to the account's My Drive, which has no parents, without
        ever meeting the shared folder."""
        service = FakeDrive(
            {},
            parents={
                "other": ["someone-elses-folder"],
                "someone-elses-folder": ["my-drive"],
                "my-drive": [],
            },
        )

        with pytest.raises(FileOutsideAllowedFolder):
            await assert_within_folder(service, "other", "root")

    async def test_a_file_with_several_parents_passes_via_any_of_them(self):
        """Walking only parents[0] would refuse a file that is legitimately in
        the shared folder as well as somewhere else."""
        service = FakeDrive(
            {}, parents={"file": ["unrelated", "root"], "unrelated": []}
        )
        await assert_within_folder(service, "file", "root")

    async def test_a_parent_cycle_does_not_hang(self):
        """Drive's parent graph can loop; the seen set is what bounds the walk."""
        service = FakeDrive({}, parents={"a": ["b"], "b": ["a"]})

        with pytest.raises(FileOutsideAllowedFolder):
            await assert_within_folder(service, "a", "root")

    async def test_the_folder_itself_is_allowed(self):
        await assert_within_folder(FakeDrive({}), "root", "root")


class TestFolderAccess:
    """Authorising with the wrong Google account is easy and its Drive-level
    symptom is a 404 naming an opaque id. The message has to name the cause."""

    async def test_an_inaccessible_root_names_the_account(self):
        from app.errors import FolderNotAccessible

        class NoAccess(FakeDrive):
            def list(self, **kw):
                raise RuntimeError('<HttpError 404 ... "notFound">')

        with pytest.raises(FolderNotAccessible) as exc:
            await list_folder_tree(NoAccess({}), "root")

        message = str(exc.value).lower()
        assert "account" in message
        assert "authorised" in message or "authorized" in message

    async def test_a_missing_subfolder_does_not_claim_an_account_problem(self):
        """Only the root gets the account-specific message. A subfolder that
        vanishes mid-walk is a different failure."""
        from app.errors import DriveAPIError

        class FailsOnSubfolder(FakeDrive):
            def list(self, *, q, **kw):
                if "'sub'" in q:
                    raise RuntimeError('<HttpError 404 ... "notFound">')
                return super().list(q=q, **kw)

        service = FailsOnSubfolder({"root": [_folder("sub", "Gone")]})

        with pytest.raises(DriveAPIError):
            await list_folder_tree(service, "root")
