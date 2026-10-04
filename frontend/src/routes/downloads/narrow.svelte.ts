// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The width below which the downloads screen changes shape, written once.
 *
 * Read in script, as the rail's narrow band is, because below it the markup changes (the chips
 * become one scrolling strip, the paste box puts its button under it), which a media query in a
 * stylesheet cannot do. 720 is where the paste box and its Download button stop fitting on one
 * line, inside the band where the rail has already gone to icons.
 * `dimensions.test.ts` holds it to an absolute length declared here.
 */

/** The window the downloads screen is drawn narrow in. An absolute length; see above. */
export const NARROW_DOWNLOADS = '(max-width: 719px)';

/**
 * The window a download row puts its three facts and its fix on a line under the name in.
 *
 * The row's fixed columns take about 870px and a name needs about 300px to be read, which with the
 * rail and the page's padding is a 1440 window; below it two lines read better than a cut name.
 */
export const FACTS_UNDER_THE_NAME = '(max-width: 1439px)';

/**
 * Whether the window is under that width, watched because a window is resized.
 *
 * Without `matchMedia` (the unit environment) the answer is false: the wide layout is the safe way
 * to be wrong.
 */
export class Narrow {
	yes = $state(false);

	constructor(query: string = NARROW_DOWNLOADS) {
		if (typeof matchMedia !== 'function') return;
		const media = matchMedia(query);
		this.yes = media.matches;
		media.addEventListener('change', (event) => (this.yes = event.matches));
	}
}
