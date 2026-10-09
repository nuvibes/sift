/* The graphics-card download, followed from outside the panel that started it. */

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
