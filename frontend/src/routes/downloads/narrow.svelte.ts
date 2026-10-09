// SPDX-License-Identifier: AGPL-3.0-or-later
/* The width below which the downloads screen changes shape, written once. */

/** The window the downloads screen is drawn narrow in. An absolute length; see above. */
export const NARROW_DOWNLOADS = '(max-width: 719px)';

/** The window a download row puts its three facts and its fix on a line under the name in. */
export const FACTS_UNDER_THE_NAME = '(max-width: 1439px)';

/** Whether the window is under that width, watched because a window is resized. */
export class Narrow {
	yes = $state(false);

	constructor(query: string = NARROW_DOWNLOADS) {
		if (typeof matchMedia !== 'function') return;
		const media = matchMedia(query);
		this.yes = media.matches;
		media.addEventListener('change', (event) => (this.yes = event.matches));
	}
}
