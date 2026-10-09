# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release's own files: which names are its artefacts, and the copy left on the desktop."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

#: What this script may replace on a desktop: anchored, and narrower than `ARTEFACT` (no blockmap).
OURS = re.compile(
    r"^Sift-\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?-x64-setup\.exe"
    r"(?:\.sha256(?:\.minisig)?|\.manifest\.json(?:\.minisig)?)?$"
)


#: One release's files in the artefacts folder, its version captured for the prune to order on.
ARTEFACT = re.compile(
    r"^Sift-(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)-x64-setup\.exe"
    r"(?:\.blockmap|(?:\.sha256|\.manifest\.json)(?:\.minisig)?)?$"
)


#: The packer's payload a failed pack leaves behind; nobody's download, so the prune removes it.
INTERMEDIATE = re.compile(
    r"^sift-desktop-(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)-x64\.nsis\.7z$"
)


#: The release before last stays to roll back to, with one to spare.
KEEP_RELEASES = 3


def _desktop() -> Path | None:
    """This account's desktop as Explorer reads it (it may be redirected), or None."""
    import winreg

    shell_folders = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_folders) as key:
            recorded, _ = winreg.QueryValueEx(key, "Desktop")
    except OSError:
        return None
    place = Path(os.path.expandvars(str(recorded)))
    return place if place.is_dir() else None


def put_on_the_desktop(artefacts: list[Path]) -> None:
    """Copy the release to the desktop over this script's older copies, which could look current."""
    where = _desktop()
    if where is None:
        print("\n  No desktop to copy to; the artefacts are in the release folder only.")
        return

    for old in sorted(where.glob("Sift-*-x64-setup.exe*")):
        if OURS.match(old.name) and old.name not in {one.name for one in artefacts}:
            old.unlink()
            print(f"  removed  {old.name}")
    for one in artefacts:
        shutil.copy2(one, where / one.name)
        print(f"  copied   {one.name}  ->  {where}")
