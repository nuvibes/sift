// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which order the Collections shelf is in, and where that choice is kept. */
import { COLLECTION_ORDERS } from '$lib/library/collections.svelte';
import { WallSort } from '$lib/grid/wall-sort.svelte';

const KEY = 'sift.collections.sort';

/** What the shelf is in until somebody says otherwise: the biggest first. */
const COLLECTIONS_DEFAULT_SORT = 'largest';

/** The one instance the screen reads. It outlives the screen, which is the point of it. */
export const collectionsSort = new WallSort(
	KEY,
	COLLECTIONS_DEFAULT_SORT,
	COLLECTION_ORDERS.map((one) => one.value)
);
