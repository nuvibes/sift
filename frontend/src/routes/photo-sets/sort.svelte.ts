// SPDX-License-Identifier: AGPL-3.0-or-later
import { COLLECTION_ORDERS } from '$lib/library/collections.svelte';
import { WallSort } from '$lib/grid/wall-sort.svelte';

/* Which order the Photo Sets wall is in, and where that choice is kept.
 *
 * A MODULE rather than a `$state` inside the screen: opening a set unmounts the wall, so an order
 * held in the component would go with it and the wall would come back in an order nobody had
 * chosen.
 *
 * Remembered in the browser rather than on the account, for the reason the grid's order is
 * (`lib/grid/sort-state`): it is a display preference the browser can answer before the first
 * paint, where a value arriving from the server a moment after the screen does would draw the
 * wall once and redraw it a beat later.
 *
 * THIS WALL'S OWN KEY, not the grid's. The grid's order is one choice across every screen of
 * FILES, and these are not files: two of the orders here count pictures in a set rather than
 * bytes, and the grid's duration and relevance orders mean nothing at all on a wall of shoots.
 * So sharing the key would let a choice made on Browse arrive here as an order this wall does not
 * offer.
 *
 * The keys are the server's own (`largest`, `name_az`, ...). An unknown value read back (an
 * older build's key, a hand-edited store) reads as the default rather than as an error, and the
 * server refuses anything it does not know regardless, so a stale key cannot mis-sort a page.
 */

const KEY = 'sift.photo-sets.sort';

/** What the wall is in until somebody says otherwise: the biggest shoots first. */
export const PHOTO_SETS_DEFAULT_SORT = 'largest';

/* The orders this wall offers, taken from the list the entity walls share rather than written
   again here: the same rule the grid's own list follows, so `Newest first` is one string across
   the application instead of two that happen to agree today. */
const KNOWN = COLLECTION_ORDERS.map((one) => one.value);

/* The one instance the screen reads. It outlives the screen, which is the point of it.

   The rule itself lives in `lib/grid/wall-sort`, shared with the Tags and Collections walls:
   three copies of "read it at import, refuse an order this wall does not offer, sort anyway when
   storage refuses" would be three chances for one of them to drift. */
export const photoSetsSort = new WallSort(KEY, PHOTO_SETS_DEFAULT_SORT, KNOWN);
