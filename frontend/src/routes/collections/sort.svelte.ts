// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which order the Collections shelf is in, and where that choice is kept.
 *
 * A MODULE rather than a `$state` inside the screen: opening a collection unmounts the shelf, so
 * an order held in the component would go with it and the shelf would come back in an order
 * nobody had chosen.
 *
 * It orders the SHELF. The sequence inside a collection is arranged by hand and is the collection
 * itself; nothing here touches it.
 *
 * The rule itself (read at import, refuse an order this shelf does not offer, sort anyway where
 * storage is refused) lives in `lib/grid/wall-sort`.
 */
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
