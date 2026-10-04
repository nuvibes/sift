// SPDX-License-Identifier: AGPL-3.0-or-later
/* An artist: who a song credits, renamed on every song at once.
 *
 * An artist has no page and no wall of its own: it is a field of a song, reached as a press under a
 * song's name and as the Artists column of the Music wall. What it has is one verb, Rename, which
 * reaches every song crediting it (`PUT /artists/{id}`): a name another artist already has folds
 * the two into one. Typing a different name into one song's form is a different act, and the right
 * one there: it credits that song with a new artist and leaves every other song alone.
 *
 * The verb is declared once, as data (`artistVerbs`), so the right-click on an artist's press, the
 * right-click on its chip on the bar and the menu door on the Music wall draw the same row. The
 * sheet is one per screen (`ArtistRenameDialog`) and reads the one holder below.
 */
import { api, ApiError, type ApiPath } from '$lib/api/client';
import type { Verb } from '$lib/components/common/verbs';
import { rememberFacetNames } from '$lib/components/shell/facet-labels';
import { libraryChanges } from '$lib/library/changes.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

/** The longest artist name the server takes (`MAX_SONG_NAME`, the bound on a song's own name). */
export const ARTIST_NAME_LIMIT = 200;

/** The query parameter and facet column an artist is filtered by. */
export const ARTIST_FIELD = 'artists';

interface ArtistRef {
	id: string;
	name: string;
}

/** What renaming an artist does, said under the sheet's title. */
export const RENAME_SAYS =
	'Every song crediting them says the new name. A name another artist already has joins the two into one artist.';

class ArtistRename {
	/** The artist the sheet is open on, and what is typed over their name. */
	artist = $state<ArtistRef | null>(null);
	open = $state(false);
	typed = $state('');

	ask(artist: ArtistRef): void {
		this.artist = artist;
		this.typed = artist.name;
		this.open = true;
	}

	/** Write the typed name. Quiet on a name that has not changed: that is a press, not an edit. */
	async save(): Promise<void> {
		const artist = this.artist;
		const wanted = this.typed.trim();
		if (!artist || !wanted || wanted === artist.name) return;
		try {
			await api.put(`/artists/${artist.id}` as ApiPath, { body: { name: wanted } });
		} catch (error) {
			toasts.show(
				error instanceof ApiError && error.status === 404
					? 'No song you can see credits that artist any more'
					: "That name couldn't be saved",
				{ tone: 'error' }
			);
			return;
		}
		// The chip on the bar names the artist by id; it says the new name at once.
		rememberFacetNames(ARTIST_FIELD, [{ value: artist.id, label: wanted, count: 0 }]);
		// Every wall of songs, every song's page and every card's line read the name again.
		libraryChanges.changed();
		const href = `/songs?${new URLSearchParams({ [ARTIST_FIELD]: artist.id })}`;
		const named = { text: wanted, kind: 'artist', id: artist.id, href };
		toasts.show(['Renamed to ', named, ' on every song'], { tone: 'success' });
	}
}

export const artistRename = new ArtistRename();

/**
 * Every verb an artist has, for the menus that draw it. Rename is an admin's, as the route is: a
 * name is shared vocabulary. A viewer who may rename nothing gets no verbs, and so no menu.
 */
export function artistVerbs(artist: ArtistRef, isAdmin: boolean): Verb[] {
	if (!isAdmin) return [];
	return [
		{
			id: 'rename',
			label: 'Rename',
			icon: 'edit_square',
			run: () => artistRename.ask(artist)
		}
	];
}
