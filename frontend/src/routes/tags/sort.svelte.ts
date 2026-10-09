// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which order the Tags wall is in, and where that choice is kept. */
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
