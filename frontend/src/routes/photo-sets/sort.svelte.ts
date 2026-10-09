// SPDX-License-Identifier: AGPL-3.0-or-later
import { COLLECTION_ORDERS } from '$lib/library/collections.svelte';
import { WallSort } from '$lib/grid/wall-sort.svelte';

/* Which order the Photo Sets wall is in, and where that choice is kept. */

const KEY = 'sift.photo-sets.sort';

/** What the wall is in until somebody says otherwise: the biggest shoots first. */
export const PHOTO_SETS_DEFAULT_SORT = 'largest';

/* The orders this wall offers, taken from the list the entity walls share rather than written
   again here: the same rule the grid's own list follows, so `Newest first` is one string across
   the application instead of two that happen to agree today. */
const KNOWN = COLLECTION_ORDERS.map((one) => one.value);

/* The one instance the screen reads. It outlives the screen, which is the point of it. */
export const photoSetsSort = new WallSort(KEY, PHOTO_SETS_DEFAULT_SORT, KNOWN);
