// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which order the Tags wall is in, and where that choice is kept.
 *
 * A MODULE rather than a `$state` inside the screen: opening a tag unmounts the wall, so an order
 * held in the component would go with it and the wall would come back in an order nobody had
 * chosen.
 *
 * ITS OWN KEY. Most used first is this wall's default, and a library with two hundred tags has
 * perhaps twelve anybody reaches for, which is a fact about tags and not about shoots, so the
 * choice is not shared with the other walls.
 *
 * The rule itself (read at import, refuse an order this wall does not offer, sort anyway where
 * storage is refused) lives in `lib/grid/wall-sort`.
 */
import { WallSort } from '$lib/grid/wall-sort.svelte';
import { TAG_ORDERS } from '$lib/entity/tags.svelte';

const KEY = 'sift.tags.sort';

/** What the wall is in until somebody says otherwise: the most used first. */
const TAGS_DEFAULT_SORT = 'largest';

/** The one instance the screen reads. It outlives the screen, which is the point of it. */
export const tagsSort = new WallSort(
	KEY,
	TAGS_DEFAULT_SORT,
	TAG_ORDERS.map((one) => one.value)
);
