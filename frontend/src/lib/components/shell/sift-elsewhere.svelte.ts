// SPDX-License-Identifier: AGPL-3.0-or-later
/* Whether this window is the Sift app in client mode, so eco mode is another computer's. */

import { bridge } from '$lib/bridge';

class SiftElsewhere {
	#asked = false;
	#yes = $state(false);

	/** Asked of the shell on the first reading; false in a browser and on the device running Sift. */
	get yes(): boolean {
		if (!this.#asked) {
			this.#asked = true;
			bridge.localHardware().then(
				(machine) => (this.#yes = machine !== null),
				() => {}
			);
		}
		return this.#yes;
	}
}

export const siftElsewhere = new SiftElsewhere();
