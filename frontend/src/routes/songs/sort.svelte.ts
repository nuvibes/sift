// SPDX-License-Identifier: AGPL-3.0-or-later
import { COLLECTION_ORDERS } from '$lib/library/collections.svelte';
import { ARTIST_ORDER } from '$lib/grid/sort-state.svelte';
import { WallSort } from '$lib/grid/wall-sort.svelte';

/* Which order the Music wall is in, and where that choice is kept. */

const KEY = 'sift.songs.sort';

/** What the wall is in until somebody says otherwise: the songs on the most files first. */
export const SONGS_DEFAULT_SORT = 'largest';

/* The orders this wall offers: the list every entity wall shares, so `Newest first` is one string
   across the application, and the one order only a wall of songs has the data for: by artist. */
export const SONG_ORDERS: readonly { value: string; label: string }[] = [
	...COLLECTION_ORDERS,
	ARTIST_ORDER
];

const KNOWN = SONG_ORDERS.map((one) => one.value);

/* The one instance the screen reads. It outlives the screen, which is the point of it. */
export const songsSort = new WallSort(KEY, SONGS_DEFAULT_SORT, KNOWN);
