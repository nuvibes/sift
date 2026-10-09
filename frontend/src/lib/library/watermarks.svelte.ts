/* Reading the site's own mark off the picture: what the install can do, and what somebody can
 * ask it to do. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The consent gate, named here for the reason the other two passes' are. */
export const WATERMARKS_ENABLED_KEY = 'watermarks.enabled';

/** Whether a file is read for a mark as it arrives. */
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
