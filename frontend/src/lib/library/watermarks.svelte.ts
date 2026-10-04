/* Reading the site's own mark off the picture: what the install can do, and what somebody can ask
 * it to do.
 *
 * One place rather than in the pane, because two screens read it: the settings pane, and the
 * Importing screen's row for reading as a file arrives. What was read off ONE file is not asked
 * for here: it is a line in that file's History, which the server writes.
 *
 * **Nothing here decides anything.** Every field arriving from the server is the server's answer;
 * the browser draws it.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The consent gate, named here for the reason the other two passes' are. */
export const WATERMARKS_ENABLED_KEY = 'watermarks.enabled';

/** Whether a file is read for a mark as it arrives. Named here rather than in the Importing
 *  screen's own list so the key is spelled once: that screen draws the row and this file is where
 *  everything about this pass is named. */
export const WATERMARKS_READ_ON_IMPORT_KEY = 'watermarks.read_on_import';

/** What reading runs on. Named here for the reason the other two passes' are. */
export const WATERMARKS_DEVICE_KEY = 'watermarks.device';

/* The job types, as constants rather than typed strings at the call sites: a reader filtered by a
 * job type that does not exist returns nothing at all, which reads exactly like a job that has
 * already finished. */
export const FETCHING_MODELS = 'watermark_fetch_models';

export type WatermarkStatus = components['schemas']['WatermarkStatus'];
export type ReadingsRemoved = components['schemas']['ReadingsRemoved'];

interface JobStarted {
	job_id: string;
}

export function watermarkStatus(): Promise<WatermarkStatus> {
	return api.get<WatermarkStatus>('/watermarks/status');
}

/** Fetch the models. `again` re-downloads files that are already on disk, which is the repair path
 *  for a model that is present but is not the one Sift expects. */
export function fetchWatermarkModels(again = false): Promise<JobStarted> {
	return api.post<JobStarted>('/watermarks/models/fetch', again ? { query: { again: true } } : {});
}

/** Throw away what was read, so the library is read again. The filings stay where they are. */
export function forgetWatermarkReads(): Promise<ReadingsRemoved> {
	return api.del<ReadingsRemoved>('/watermarks/reads');
}
