/*
 * The graphics-card download, followed from outside the panel that started it.
 *
 * Held at module level for the same reason the model download is: over a gigabyte takes long enough
 * that somebody will close the settings sheet while it runs, and a watcher inside the component
 * would be thrown away with it, so coming back would show the button again and the only way to
 * find out whether it had finished would be to reload the page.
 */

import { DownloadWatch } from '$lib/jobs/watch-download.svelte';

/** The job kind, as the queue knows it. Must match `ACCEL_INSTALL` in the performance slice. */
export const ACCEL_INSTALL = 'accel_install';

export const accelWatch = new DownloadWatch(ACCEL_INSTALL, async () =>
	/* Deliberately not "it worked". Whether it worked is a separate question with a separate answer
	   (the card has to actually run a model), and the panel asks it with its own button. What is
	   known here is only that the download stopped, which is why the sentence covers both ways it
	   can stop and neither claims success. */
	Promise.resolve(
		'The download has stopped. If it finished, the card can be tested below; if it did not, ' +
			'what arrived is kept, so starting again costs only the rest.'
	)
);
