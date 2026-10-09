/* The model download, followed from outside any one screen. */

import { ModelFetchWatch } from '$lib/jobs/model-fetch';
import { FETCHING_MODELS, watermarkStatus } from '$lib/library/watermarks.svelte';

class ModelFetch extends ModelFetchWatch {
	constructor() {
		super(FETCHING_MODELS, async () => {
			const state = await watermarkStatus().catch(() => null);
			return state?.ready ? 'The models are on this machine. Sift can read watermarks now.' : null;
		});
	}
}

export const modelFetch = new ModelFetch();
