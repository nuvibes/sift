# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nothing Sift writes or sends carries where a file was made.

A location is never kept in anything Sift stores, and every copy Sift makes or sends
carries none; the originals in somebody's own folders are theirs and are never
rewritten. `sift.kernel.places` is the one door that takes a place out of a file. This gate holds
every way a file leaves or lands to it.

## What is checked

1. **Every route that hands a file out** (a `FileResponse`, a `StreamingResponse`, `serve_file`)
   is in `HANDED_OUT`, and says either `DOOR` (and then its function really calls the door) or why
   what it serves cannot carry a place. A new one fails here until somebody has said which.
2. **Every copy into a place a file lands** (`shutil.copyfile`, `copy2`, `copyfileobj`, `move`) is
   in `COPIED`, the same way.
3. **The writers that land a file go through the door:** `import_file` (every download, upload,
   paste, screenshot and swap), the write seam's `keep` (every file an edit produces) and the
   swap's `strip` (every file sent).
4. **The one function that rewrites a file where it lies** (`remove_places_from_own`) is called
   from `keep` alone, which only ever holds the scratch file an operation built. No original in
   anybody's library is ever handed to it.

Tests are exempt: a test serving a file is describing a route, not handing anything out.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: A route that goes through the door.
DOOR = "door"

_ENCODED_STILL = (
    "a picture ffmpeg encodes from decoded pixels; its JPEG, PNG and WebP writers put no EXIF in"
)
_DERIVATIVE = (
    "thumbnails, previews and sprites: pictures ffmpeg encodes, and MP4s from its MP4 muxer, "
    "which writes no place tag (a MOV holding a location atom and an ISO 6709 key, remuxed "
    "to MP4, keeps neither)"
)

_PILLOW_JPEG = (
    "a HEIC or AVIF decoded and written again as a JPEG by Pillow, whose JPEG writer puts EXIF in "
    "only when it is handed some, and none is (a picture with a GPS directory comes out as a "
    "JPEG with no EXIF block)"
)

#: Every function that hands a file out, and why it is safe.
HANDED_OUT: dict[str, str] = {
    "slices/browse/router.py::save_to_device": DOOR,
    "slices/backup/router.py::copy_of": (
        "Sift's own database, which keeps no place: a probe's answer is stored through "
        "strip_places and no column holds a location"
    ),
    # The drag out of the window (the desktop shell fetches it) and Copy image (the clipboard).
    "slices/player/router.py::outgoing": DOOR,
    # THE ONE ORIGINAL SERVED AS IT IS: the file, to Sift's own player on the page that asked. It
    # plays in the app's session and is never saved by it; saving is `save_to_device`, the door.
    "slices/player/router.py::stream": (
        "the original, to the app's own player in this session; saving, a drag out of the window"
        " and Copy image go through the door"
    ),
    "slices/player/router.py::rendition": _PILLOW_JPEG,
    "slices/player/router.py::hls_segment": (
        "segments of a transcode Sift made to play a file, written by ffmpeg with no place tag"
    ),
    "kernel/serving.py::serve_file": "the shared helper; each caller is listed here on its own",
    "kernel/covers.py::serve_cover": (
        "Sift's own JPEG, encoded by ffmpeg with -map_metadata -1 (kernel/media.py)"
    ),
    "slices/browse/router.py::_serve_derivative": _DERIVATIVE,
    "slices/browse/router.py::get_sprite": _DERIVATIVE,
    "slices/loops/router.py::loop_thumb": _DERIVATIVE,
    "slices/faces/router_faces.py::face_crop": _ENCODED_STILL,
    "slices/faces/router_faces.py::face_cover": _ENCODED_STILL,
    "slices/download/router.py::creator_art": (
        "a Site's picture of a creator, re-encoded into Sift's own JPEG before it is kept"
    ),
    "slices/people/router_icons.py::site_icon": "a Site's icon from the installed pack of Site icons",
    "slices/people/router_icons.py::site_icon_for_link": "a Site's icon from the installed pack",
    "slices/media_edit/router.py::read_sample": (
        "a few seconds of a compression, written by ffmpeg's MP4 muxer, which writes no place tag"
    ),
    "wiring/routes.py::build_routes.browser_client": "the app's own client files",
}

#: Every copy of a file's bytes into somewhere, and why it lands no place.
COPIED: dict[str, str] = {
    "kernel/jpeg_turn.py::_place": "a copy keeping the first orientation record, from Sift's scratch to Sift's cache",
    "slices/capture/pipeline.py::_copy_into_folder": (
        "called by import_file alone, after the door has made the copy it lands"
    ),
    "kernel/places.py::_write": "the door itself, writing the copy without the place",
    "kernel/ingress.py::_quarantine": "a refused file set aside in the quarantine, never served",
    "kernel/media.py::_moved": "frames a decode wrote, moved between Sift's own scratch folders",
    "kernel/webp.py::_place": _DERIVATIVE,
    "kernel/heif.py::_place": _PILLOW_JPEG,
    "kernel/ml/weights.py::WeightStore._install_local": "a model's weights, not media",
    "kernel/ml/weights.py::_extract": "a model's weights, not media",
    "slices/backup/libraries.py::_copy_files": "Sift's own data folders, not media",
    "slices/backup/service.py::_extract_database": "Sift's own database, restored",
    "slices/backup/service.py::_extract_folder": "Sift's own data folder, restored",
    "slices/media_jobs/sprites.py::_fill_gaps": "frames a decode wrote, in Sift's own scratch",
}

#: The writers that land or send a file, and the door each must call.
WRITERS: dict[str, str] = {
    "slices/capture/pipeline.py::import_file": "remove_places",
    "slices/organize/service.py::Organizer.keep": "remove_places_from_own",
    "slices/swap/transfer.py::_holds_a_place": "places_in",
    # The swap's look: a file sent as it is, uncopied, only when the door finds no place in it.
    "slices/swap/transfer.py::_look": "places_in",
    # Where the shell's drag finds a file: never the original's path when it holds a place.
    "slices/player/router.py::local_file": "size_without_places",
}

#: The only callers of the function that rewrites a file where it lies.
REWRITES_IN_PLACE = {"slices/organize/service.py::Organizer.keep"}

_SERVES = frozenset({"FileResponse", "StreamingResponse", "serve_file"})
_COPIES = frozenset({"copyfile", "copy2", "copyfileobj"})


def _name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_shutil_move(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "move"
        and isinstance(func.value, ast.Name)
        and func.value.id == "shutil"
    )


def _functions() -> dict[str, ast.AST]:
    """Every function in the source (not the tests), by `path::Qualified.name`."""
    found: dict[str, ast.AST] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        if "tests" in path.parts or "testing" in path.parts:
            continue
        rel = path.relative_to(SOURCE).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node: ast.AST, stack: tuple[str, ...], rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    inner = (*stack, child.name)
                    if not isinstance(child, ast.ClassDef):
                        found[f"{rel}::{'.'.join(inner)}"] = child
                    visit(child, inner)
                else:
                    visit(child, stack)

        visit(tree, ())
    return found


def _own_calls(function: ast.AST) -> list[ast.Call]:
    """The calls made in a function's own body, not in a function defined inside it."""
    calls: list[ast.Call] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if isinstance(child, ast.Call):
                calls.append(child)
            visit(child)

    visit(function)
    return calls


def _mentions(function: ast.AST, attribute: str) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr == attribute for node in ast.walk(function)
    )


def test_every_route_that_hands_a_file_out_goes_through_the_door_or_says_why_not() -> None:
    functions = _functions()
    serving = {
        key
        for key, function in functions.items()
        if any(_name(call) in _SERVES for call in _own_calls(function))
    }

    unlisted = sorted(serving - set(HANDED_OUT))
    assert not unlisted, (
        "These hand a file out and are not in HANDED_OUT. Send the copy through "
        "`sift.kernel.places.remove_places`, or say why what they serve cannot carry a place: "
        f"{unlisted}"
    )
    stale = sorted(set(HANDED_OUT) - serving)
    assert not stale, f"These are in HANDED_OUT and no longer hand a file out: {stale}"
    for key, why in HANDED_OUT.items():
        if why == DOOR:
            assert _mentions(functions[key], "remove_places"), f"{key} no longer calls the door"


def test_every_copy_of_a_file_says_why_it_lands_no_place() -> None:
    functions = _functions()
    copying = {
        key
        for key, function in functions.items()
        if any(_name(call) in _COPIES or _is_shutil_move(call) for call in _own_calls(function))
    }

    unlisted = sorted(copying - set(COPIED))
    assert not unlisted, (
        f"These copy a file and are not in COPIED; say why the copy lands no place: {unlisted}"
    )
    stale = sorted(set(COPIED) - copying)
    assert not stale, f"These are in COPIED and no longer copy a file: {stale}"


def test_every_writer_that_lands_or_sends_a_file_calls_the_door() -> None:
    functions = _functions()
    for key, door in WRITERS.items():
        assert key in functions, f"{key} is gone: find what lands or sends files now"
        assert _mentions(functions[key], door), f"{key} no longer calls places.{door}"
    # The swap's strip reaches the door through its helper, and the only copy into a library
    # folder is made by `import_file`, after the door.
    assert any(
        isinstance(node, ast.Name) and node.id == "_holds_a_place"
        for node in ast.walk(functions["slices/swap/transfer.py::strip"])
    )
    callers = {
        key
        for key, function in functions.items()
        if key != "slices/capture/pipeline.py::_copy_into_folder"
        and any(
            isinstance(node, ast.Name) and node.id == "_copy_into_folder"
            for node in ast.walk(function)
        )
    }
    assert callers == {"slices/capture/pipeline.py::import_file"}


def test_only_keep_rewrites_a_file_where_it_lies() -> None:
    """The door's one in-place rewrite is for the scratch file an edit built. An original in
    somebody's library is never handed to it."""
    functions = _functions()
    callers = {
        key
        for key, function in functions.items()
        if key != "kernel/places.py::remove_places_from_own"
        and _mentions(function, "remove_places_from_own")
    }
    assert callers == REWRITES_IN_PLACE


def test_the_door_never_opens_what_it_reads_for_writing() -> None:
    """Every `open` in the door that is not the copy's own exclusive create reads."""
    tree = ast.parse((SOURCE / "kernel" / "places.py").read_text(encoding="utf-8"))
    modes = [
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and _name(call) == "open"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    ]
    assert modes and set(modes) <= {"rb", "xb"}, modes
