/* Recognition's model download, followed from outside any one screen. */

import { ModelFetchWatch } from '$lib/jobs/model-fetch';
import { FETCHING_WEIGHTS, faceSettings, type FaceSettings } from '$lib/people/faces.svelte';

class ModelFetch extends ModelFetchWatch {
	/** What the server says about the feature after the download stopped, so a screen can draw the
	 *  new state without asking again. Null until a run has ended here. */
	settled = $state<FaceSettings | null>(null);

	constructor() {
		super(FETCHING_WEIGHTS, async () => {
			// Asked afresh: whether the models are here now is the server's answer, however it ended.
			const state = await faceSettings().catch(() => null);
			this.settled = state;
			return state?.ready ? 'The models are installed. Sift can recognize faces now.' : null;
		});
	}
}

export const modelFetch = new ModelFetch();
