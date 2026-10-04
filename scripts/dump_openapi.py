#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write the API schema to a file, for the client's types to be generated from.

The client's request and response types are not written by hand. Hand-written ones drift: the server
gains a field, nobody tells the client, and the mismatch shows up as a bug in a screen rather than as
an error at the point it was introduced. So the schema the server itself produces is the source, and
a check in CI regenerates from it and fails if what is committed no longer matches.

    python scripts/dump_openapi.py frontend/openapi.json
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <output.json>")

    # The app reads its directories at import time and insists they are writable. Nothing here needs
    # them (the schema is built from the route table, not from anything on disk), so it is given
    # somewhere harmless to look rather than being pointed at a real install.
    #
    # Taken away again afterwards: this runs on every build and in CI, and left behind it would
    # leave an empty directory under the temp folder per run.
    scratch = tempfile.mkdtemp(prefix="sift-schema-")
    try:
        os.environ.setdefault("SIFT_DATA_DIR", str(Path(scratch) / "data"))
        os.environ.setdefault("SIFT_CACHE_DIR", str(Path(scratch) / "cache"))

        from sift.main import create_app

        schema = create_app().openapi()
        # `newline` is not decoration. Text mode on Windows turns every \n into \r\n, so this
        # file (which is committed, and which a check regenerates and diffs) would come out
        # with a different line ending from the one the repository keeps: git normalises on the
        # way in, so the diff is empty while the working tree reads as modified for ever.
        Path(sys.argv[1]).write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    finally:
        # The directory this run made for itself, and only that: under the system temp directory,
        # holding nothing but the two empty folders above. Never a library, and never a file
        # anybody else put there. Suppressed on the line rather than for the file, so the rule
        # keeps watching everything else here, and on the OPENING line, because the formatter
        # wraps this call and a comment left on the closing bracket is attached to nothing.
        shutil.rmtree(  # nosemgrep: sift-no-file-removal-outside-delete-trash
            scratch, ignore_errors=True
        )


if __name__ == "__main__":
    main()
