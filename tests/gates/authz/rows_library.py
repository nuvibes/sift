# SPDX-License-Identifier: AGPL-3.0-or-later
"""Roots, folders, importing, organizing, performance and compressing."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    A_FINISHED_RUN,
    A_FOLDER,
    A_MOVE,
    A_QUARANTINED,
    A_ROOT,
    A_SAMPLE_JOB,
    AN_ASSET,
    DELETABLE_ROOT,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
    # Everything about a ROOT, the folder picker and the grants is admin-only: a root is a directory
    # on the server's disk, and the picker enumerates what is mounted.
    ("GET", "/api/library/grants"): Case(Policy.ADMIN),
    ("POST", "/api/library/grants"): Case(Policy.ADMIN, body={"path": "/nowhere/that/exists"}),
    # How a file is handled on arrival changes what the machine does for everybody.
    ("GET", "/api/importing/folders"): Case(Policy.ADMIN),
    ("PUT", "/api/importing/folders/{root_id}"): Case(
        Policy.ADMIN, params={"root_id": A_ROOT}, body={"answers": {}}
    ),
    ("GET", "/api/importing/build"): Case(Policy.ADMIN),
    ("POST", "/api/importing/build"): Case(Policy.ADMIN, body={"products": ["pictures"]}),
    ("POST", "/api/importing/build/retry"): Case(Policy.ADMIN, body={"products": ["pictures"]}),
    # Real work on the machine; a 409 tells an admin the file is already waiting.
    ("GET", "/api/importing/run-now"): Case(Policy.ADMIN),
    ("POST", "/api/assets/run"): Case(
        Policy.ADMIN, body={"run": "details", "asset_ids": [AN_ASSET]}, answers_anyway=409
    ),
    ("GET", "/api/library/browse"): Case(Policy.ADMIN),
    ("GET", "/api/library/roots"): Case(Policy.ADMIN),
    ("POST", "/api/library/roots"): Case(Policy.ADMIN),
    ("PATCH", "/api/library/roots/{root_id}"): Case(Policy.ADMIN, params={"root_id": A_ROOT}),
    ("DELETE", "/api/library/roots/{root_id}"): Case(
        Policy.ADMIN, params={"root_id": DELETABLE_ROOT}
    ),
    ("POST", "/api/library/roots/{root_id}/rescan"): Case(Policy.ADMIN, params={"root_id": A_ROOT}),
    # Carries a path on the machine in its body, as adding a root does.
    ("POST", "/api/library/roots/{root_id}/moved"): Case(
        Policy.ADMIN, params={"root_id": A_ROOT}, body={"abs_path": "/nowhere/at/all"}
    ),
    # A refusal names a file by its path inside somebody's library.
    ("POST", "/api/library/roots/{root_id}/rejections/allow"): Case(
        Policy.ADMIN, params={"root_id": A_ROOT}, body={"rel_path": "nothing/was/refused.bin"}
    ),
    ("GET", "/api/library/quarantine"): Case(Policy.ADMIN),
    # The one route whose job is deleting the file it is handed.
    ("DELETE", "/api/library/quarantine/{name}"): Case(
        Policy.ADMIN, params={"name": A_QUARANTINED}, destructive=True
    ),
    # FOLDER reads are for anyone signed in and scoped in the handler: a hidden folder is absent.
    ("GET", "/api/library/folders"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/library/folders/{folder_id}"): Case(
        Policy.AUTHENTICATED, params={"folder_id": A_FOLDER}
    ),
    # These count what a folder physically holds, not what the caller may open.
    ("GET", "/api/library/folders/facts"): Case(Policy.ADMIN),
    # Disk facts and physical counts, the line `FolderDetail`'s file count draws.
    ("GET", "/api/library/folders/{folder_id}/properties"): Case(
        Policy.ADMIN, params={"folder_id": A_FOLDER}
    ),
    # Writes into somebody's library; a folder not handed over read-write is refused in the handler.
    ("POST", "/api/library/folders"): Case(
        Policy.ADMIN, body={"parent_id": A_FOLDER, "name": "nothing"}
    ),
    ("POST", "/api/library/folders/placed"): Case(
        Policy.ADMIN, body={"parent_id": A_FOLDER, "name": "nothing"}
    ),
    ("PATCH", "/api/library/folders/{folder_id}"): Case(
        Policy.ADMIN, params={"folder_id": A_FOLDER}, body={"name": "nothing"}
    ),
    # Organizing changes a file somebody else put there. Reading whether a file *can* be organized
    # must first settle whether the caller can see it, so it is authenticated.
    ("GET", "/api/assets/{asset_id}/organize"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("POST", "/api/assets/{asset_id}/rename"): Case(Policy.ADMIN, params={"asset_id": AN_ASSET}),
    ("POST", "/api/assets/move"): Case(
        Policy.ADMIN, body={"asset_ids": [AN_ASSET], "folder_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/moves/{move_id}/undo"): Case(Policy.ADMIN, params={"move_id": A_MOVE}),
    # Admin at the door: per-file refusals are answered in the rows, so a guest would get a 200.
    ("POST", "/api/organize/rename/preview"): Case(
        Policy.ADMIN, body={"asset_ids": [AN_ASSET], "template": "{name}"}
    ),
    ("POST", "/api/organize/rename"): Case(
        Policy.ADMIN, body={"asset_ids": [AN_ASSET], "template": "{name}"}
    ),
    # Works the machine hard and recommends instance-wide settings; starting one starts one.
    ("POST", "/api/performance/self-test"): Case(Policy.ADMIN, destructive=True),
    ("GET", "/api/performance/self-test"): Case(Policy.ADMIN),
    ("GET", "/api/performance/benchmark"): Case(Policy.ADMIN),
    # A log line names files, users and paths, and what OTHER people did.
    ("GET", "/api/logs"): Case(Policy.ADMIN),
    ("GET", "/api/logs/archive"): Case(Policy.ADMIN),
    ("GET", "/api/performance/hardware"): Case(Policy.ADMIN),
    # A run says how big the library is and names the machine.
    ("GET", "/api/performance/runs/{run_id}/report"): Case(
        Policy.ADMIN, params={"run_id": A_FINISHED_RUN}
    ),
    # The graphics-card runtime: reading names the card; the others spend bandwidth, disk and time.
    ("GET", "/api/performance/accelerator"): Case(Policy.ADMIN),
    ("POST", "/api/performance/accelerator"): Case(Policy.ADMIN),
    ("POST", "/api/performance/accelerator/test"): Case(Policy.ADMIN),
    ("DELETE", "/api/performance/accelerator"): Case(Policy.ADMIN),
    # Takes the library off the air for everybody. Not `destructive`: nothing supervises a test
    # process, so it answers 409 and stops nothing.
    ("POST", "/api/performance/restart"): Case(Policy.ADMIN),
    # Compressing reads or writes somebody's files. A route naming an asset settles visibility
    # first, so a guest is told there is no such file rather than that one exists.
    ("POST", "/api/compress/preflight"): Case(Policy.ADMIN),
    ("POST", "/api/compress"): Case(Policy.ADMIN),
    ("POST", "/api/assets/{asset_id}/compress/sample"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET}
    ),
    # Editing produces a new file, so it is an admin's; the door is in the service, which settles
    # visibility first. The frame size is asked only in order to edit.
    ("GET", "/api/assets/{asset_id}/edit/frame"): Case(Policy.ADMIN, params={"asset_id": AN_ASSET}),
    ("POST", "/api/assets/{asset_id}/edit/preflight"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET}
    ),
    ("POST", "/api/assets/{asset_id}/edit"): Case(Policy.ADMIN, params={"asset_id": AN_ASSET}),
    # A fact about a file the user can already see; an original they may not see is not named.
    ("GET", "/api/assets/{asset_id}/produced"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/made-from"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # A few seconds of somebody's file.
    ("GET", "/api/compress/samples/{job_id}"): Case(Policy.ADMIN, params={"job_id": A_SAMPLE_JOB}),
}
