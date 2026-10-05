/*
 * The graphics-card download, followed from outside the panel that started it.
 *
 * Held at module level for the same reason the model download is: over a gigabyte takes long enough
 * that somebody will close the settings sheet while it runs, and a watcher inside the component
 * would be thrown away with it, so coming back would show the button again and the only way to
 * find out whether it had finished would be to reload the page.
 */

import { ModelFetchWatch } from '$lib/jobs/model-fetch';

/** The job kind, as the queue knows it. Must match `ACCEL_INSTALL` in the performance slice. */
export const ACCEL_INSTALL = 'accel_install';

/* Finished is not "it works": the panel's own test answers that. */
export const accelWatch = new ModelFetchWatch(
	ACCEL_INSTALL,
	async (row) =>
		row?.state === 'done' ? 'The download finished. Test the GPU to see that it works.' : null,
	'GPU support'
);
