# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a refusal reaches the caller, and the cases the ordinary paths cannot produce.

Three of these are the translation from the service's own exceptions to a status code. They are
tested directly rather than through a request because the routes that raise them answer the
permission question at the door, so the branch is unreachable from outside, and it is kept anyway:
it is the layer that stops a service refusal ever leaving as a 500, and a service is free to grow a
new refusal tomorrow.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import HTTPException, status

from sift.kernel.access import Viewer
from sift.kernel.db import Database
from sift.kernel.library_write import LibraryWriteRefused
from sift.slices.media_edit.models import CompressRequest
from sift.slices.media_edit.refusals import NotAllowed, NotFound, Refused
from sift.slices.media_edit.router import _refusal
from sift.slices.media_edit.service import CompressService
from sift.slices.media_edit.settings import Preset
from sift.slices.media_edit.tests.conftest import Library

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stub_handlers")]


def test_nothing_there_for_you_is_a_404() -> None:
    assert (
        _refusal(NotFound("Sift could not find the file.")).status_code == status.HTTP_404_NOT_FOUND
    )


def test_you_may_not_is_a_403() -> None:
    assert _refusal(NotAllowed("Only an admin.")).status_code == status.HTTP_403_FORBIDDEN


def test_the_state_of_the_disk_says_no_is_a_409() -> None:
    """A reasonable request that cannot be carried out. Not the caller's fault, not a 500."""
    assert _refusal(Refused("No.")).status_code == status.HTTP_409_CONFLICT
    assert (
        _refusal(LibraryWriteRefused("The folder is read-only.")).status_code
        == status.HTTP_409_CONFLICT
    )


def test_a_refusal_keeps_its_own_sentence() -> None:
    """Written for the person who pressed the button, and passed through unchanged."""
    error: HTTPException = _refusal(NotFound("Sift could not find the file."))
    assert error.detail == "Sift could not find the file."


async def test_a_file_with_nowhere_to_be_is_reported_rather_than_crashed(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """An asset whose last location has gone: nothing to compress, and nothing to write beside."""
    ingested = await add_file(managed, "clip.mp4")
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (ingested.asset.id,))

    answer = await compressor.preflight(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL), viewer=admin
    )

    assert answer.files[0].skip_reason is not None
    assert answer.eligible_count == 0


class RefusingService:
    """A service that refuses everything, for the branch a request cannot reach.

    Both routes answer the permission question at the door, so the only caller that could make
    the service refuse is one that has already been let through, which leaves the translation
    below unreachable from outside and worth keeping anyway. Driven directly rather than through a
    request, because there is no request that produces it.
    """

    async def preflight(self, *_args: Any, **_kwargs: Any) -> Any:
        raise Refused("Sift will not do that.")

    async def start(self, *_args: Any, **_kwargs: Any) -> Any:
        raise LibraryWriteRefused("That folder is read-only.")


def _anyone() -> Viewer:
    """A viewer for a path that never looks at one. Tests may build one; nothing else may."""
    from sift.kernel.access import Role

    return Viewer(id="01HX0000000000000000000301", role=Role.ADMIN)


async def test_a_service_refusal_from_preflight_arrives_as_a_status() -> None:
    from sift.slices.media_edit.router import preflight

    with pytest.raises(HTTPException) as raised:
        await preflight(
            service=RefusingService(),  # type: ignore[arg-type]
            viewer=_anyone(),
            body=CompressRequest(asset_ids=["a"], preset=Preset.SMALL),
        )
    assert raised.value.status_code == status.HTTP_409_CONFLICT
    assert raised.value.detail == "Sift will not do that."


async def test_a_write_refusal_from_starting_arrives_as_a_status() -> None:
    from sift.slices.media_edit.router import start

    with pytest.raises(HTTPException) as raised:
        await start(
            service=RefusingService(),  # type: ignore[arg-type]
            viewer=_anyone(),
            body=CompressRequest(asset_ids=["a"], preset=Preset.SMALL),
        )
    assert raised.value.status_code == status.HTTP_409_CONFLICT
    assert raised.value.detail == "That folder is read-only."


async def test_the_table_is_not_rebuilt_on_a_database_that_already_has_it(
    temp_db: Database,
) -> None:
    """The upgrade path: a database already carrying this component is left alone.

    Worth a test rather than an assumption. A step that ran again on an existing database is how a
    migration destroys something, and the shape here (one `if` on the version) is the shape that
    is easy to get wrong when a second version is added later.
    """
    from sift.slices.media_edit import schema

    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await schema.initialize(connection, schema.VERSION)

    rows = await temp_db.fetch_all("SELECT id FROM produced_files", ())
    assert rows == []


async def test_a_copy_whose_source_was_never_recorded_still_says_it_is_a_copy(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The original has gone entirely, taking the link with it. The record survives it."""
    copy = await add_file(managed, "clip-10MB.mp4")
    await compressor.record(
        asset_id=copy.asset.id,
        source_asset_id=copy.asset.id,
        preset="small",
        target_bytes=1,
        actor_id=admin.id,
    )
    await temp_db.execute(
        "UPDATE produced_files SET source_asset_id = NULL WHERE asset_id = ?", (copy.asset.id,)
    )

    produced = await compressor.produced_for(copy.asset.id, viewer=admin)
    assert produced is not None
    assert produced.source_asset_id is None
    assert produced.operation == "compress"


def test_a_file_concealed_by_the_vault_answers_locked_rather_than_gone() -> None:
    """The same relaxation the organize router makes, for the same reason and reached by nothing.

    404 tells the person who locked their own vault that their file has been deleted. The three
    assertions under it are what stop this being a mapper that answers 423 to everything.
    """
    from fastapi import status

    from sift.kernel.reach import ConcealedByVault
    from sift.slices.media_edit.refusals import NotAllowed, NotFound
    from sift.slices.media_edit.router import _refusal

    assert _refusal(ConcealedByVault("shut")).status_code == status.HTTP_423_LOCKED
    assert _refusal(NotFound("no such file")).status_code == status.HTTP_404_NOT_FOUND
    assert _refusal(NotAllowed("not yours")).status_code == status.HTTP_403_FORBIDDEN
