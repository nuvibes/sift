# SPDX-License-Identifier: AGPL-3.0-or-later
"""Songs: one piece of music, and every file that carries it.

A song is named for a file by AcoustID (from the file's music fingerprint), by a Site's page a
download read, by another file with the same music, or by a person; every file carrying the same
song is on the same row, and the Music page lists them as the Photo Sets page lists sets: with a
cover (the music glyph where nobody chose one), a page with its files, the people, tags and Sites
those files reach, and its History.

## What this slice owns and does not

It owns the endpoints and the writes to a song's own row: a rename, a note, a cover, a merge, a
delete, the files somebody puts on it by hand. It owns no table. `songs`, `song_files` and
`song_user_state` live in the kernel (`kernel/access/schema.py`), because the walls, the stored
counts and the kernel's own writers read them, and which song a file carries is written only
through the kernel's song door (`kernel/content/songs.py`), which this slice calls like every other
writer does.

Asking AcoustID is the music slice's (`slices/music/lookup.py`): this slice never sends anything
anywhere.
"""

from __future__ import annotations

from sift.slices.songs.models import MAX_SONG_NAME
from sift.slices.songs.router import router
from sift.slices.songs.service import SERVICE, SongService

__all__ = ["MAX_SONG_NAME", "SERVICE", "SongService", "router"]
