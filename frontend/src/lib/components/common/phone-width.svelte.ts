/*
 * The phone's width, as something script can read, watched rather than read once.
 *
 * The shell draws a phone below this width: the rail goes, the tabs come, and a handful of
 * primitives change WHAT they draw rather than how wide it is. A menu becomes a sheet from the
 * foot of the screen, the selection bar names four verbs and folds the rest behind More, and the
 * corner player docks as a strip above the tabs. Each of those is a decision about markup, and a
 * media query cannot reach markup, so the width is read
 * here once and every primitive that reshapes asks this.
 *
 * The number is the shell's own phone rule, the same query the layout's stylesheet and the top bar
 * are written against, so the markup and the layout change at the same pixel.
 *
 * Absent `matchMedia` (the unit environment) the answer is false: the wide layout is the one every
 * screen has been looked at in, so it is the safe way to be wrong. A test that wants the phone sets
 * `phoneWidth.yes` itself.
 */

/** The window a phone is drawn in. An absolute length, as every floor is. */
export const PHONE_WIDTH = '(max-width: 767px)';

class PhoneWidth {
	/** Whether the window is under the phone's width now. */
	yes = $state(false);

	constructor() {
		if (typeof matchMedia !== 'function') return;
		const media = matchMedia(PHONE_WIDTH);
		this.yes = media.matches;
		media.addEventListener('change', (event) => (this.yes = event.matches));
	}
}

/** The one reading of the phone's width, shared by every primitive that reshapes on a phone. */
export const phoneWidth = new PhoneWidth();
