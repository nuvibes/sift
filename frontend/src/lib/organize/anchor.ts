/* Arriving at one row of a queue, named by a fragment in the address. */

/** Put the row a fragment names on screen. True when there was one, so the caller can stop
 * asking. */
export function revealAnchored(id: string): boolean {
	if (!id) return false;
	const found = document.getElementById(id);
	if (!found) return false;
	if (typeof found.scrollIntoView === 'function') {
		found.scrollIntoView({ block: 'center' });
	}
	return true;
}
