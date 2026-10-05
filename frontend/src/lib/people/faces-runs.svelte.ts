/*
 * Recognition's model download, followed from outside any one screen.
 *
 * Held at module level for the reason the semantic one and the graphics-card one are: the download
 * is hundreds of megabytes over whatever connection the machine has, so it outlives the pane that
 * started it. Watched inside the component, closing the settings sheet would throw the watcher away
 * and coming back would show the button again, and the only way to find out whether it had landed
 * would be to reload the page.
 *
 * The first run is a second screen that starts this download. Two components each polling the same job is two answers to one question, and the one that is not
 * on screen is the one that goes wrong quietly.
 *
 * The machinery and the sentence for a download that left no models are `jobs/model-fetch`'s.
 * What is here is about faces: the sentence when they arrive, and whether recognition can run now.
 */

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
