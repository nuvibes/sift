/* Arriving at one row of a queue, named by a fragment in the address.
 *
 * A still on the Organize board leads to its group, and neither duplicates queue has an address per
 * group (a near-duplicate group exists only at the dials in force), so the address is the page and
 * a fragment naming the row, written by `slices/dedup/queue`. The browser scrolls to a fragment
 * before these lists have answered, so the reveal runs after the rows are drawn. It does not page:
 * a card's stills come from the front of its queue, and a row not on this page leaves it at the top.
 */

/**
 * Put the row a fragment names on screen. True when there was one, so the caller can stop asking.
 *
 * `getElementById`, not a selector: a group's name holds a colon, which a selector reads as a
 * pseudo-class. `scrollIntoView` is checked because jsdom leaves it undefined.
 */
export function revealAnchored(id: string): boolean {
	if (!id) return false;
	const found = document.getElementById(id);
	if (!found) return false;
	if (typeof found.scrollIntoView === 'function') {
		found.scrollIntoView({ block: 'center' });
	}
	return true;
}
