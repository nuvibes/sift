// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Whether a finger is the main pointer, watched rather than read once.
 *
 * The viewer's sideways stroke is a finger's, so on a phone held in the hand the bar's Previous and
 * Next give way to it; a narrow desktop window with a mouse keeps them. The width is the shell's
 * own reading (`phoneWidth`); this is only the other half of the question.
 *
 * Absent `matchMedia` (the unit environment) the answer is false. A test that wants a finger sets
 * `finger.yes` itself.
 */

/** A finger as the main pointer. */
const A_FINGER = '(pointer: coarse)';

class Finger {
	yes = $state(false);

	constructor() {
		if (typeof matchMedia !== 'function') return;
		const media = matchMedia(A_FINGER);
		this.yes = media.matches;
		media.addEventListener('change', (event) => (this.yes = event.matches));
	}
}

/** The one reading of whether a finger is the pointer. */
export const finger = new Finger();
