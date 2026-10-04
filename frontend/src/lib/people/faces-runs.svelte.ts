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
 * The machinery (the job id, the fraction, joining a run already going, keeping the outcome after
 * the bar goes) is `jobs/watch-download`'s. What is here is the only part that is about faces:
 * the sentence at the end, and re-reading whether the feature can actually run now.
 */

import { DownloadWatch } from '$lib/jobs/watch-download.svelte';
import { FETCHING_WEIGHTS, faceSettings, type FaceSettings } from '$lib/people/faces.svelte';

class ModelFetch extends DownloadWatch {
	/** What the server says about the feature after the download stopped, so a screen can draw the
	 *  new state without asking again. Null until a run has ended here. */
	settled = $state<FaceSettings | null>(null);

	constructor() {
		super(FETCHING_WEIGHTS, async () => {
			/* Asked afresh rather than read off the job's last state. Finishing, failing and being
			   cancelled all leave the same question (are the models on this machine now?), and the
			   server is where that is answered. */
			const state = await faceSettings().catch(() => null);
			this.settled = state;
			return state?.ready
				? 'The models are installed. Sift can recognize faces now.'
				: 'The download stopped before it finished. What arrived is kept, so starting again costs only the rest.';
		});
	}
}

export const modelFetch = new ModelFetch();
