/* The model download, followed from outside any one screen.
 *
 * Held at module level for the reason the other two passes' watchers are: a run outlives the pane
 * that started it. Kept inside the component, closing the settings sheet would throw the watcher
 * away, and coming back would show a button, so the only way to find out whether the download had
 * arrived would be to reload the page.
 *
 * **The pass itself is not followed here, and that is deliberate rather than missing.** Reading the
 * library is a crowd of per-file jobs in the Identify family, so following one of them would say
 * nothing about the rest. The pane's progress is the thing itself (how many files have been read
 * against how many are still waiting), read from the status the server already computes, which is
 * also what makes it resume for free.
 */

import { DownloadWatch } from '$lib/jobs/watch-download.svelte';
import { FETCHING_MODELS, watermarkStatus } from '$lib/library/watermarks.svelte';

class ModelFetch extends DownloadWatch {
	constructor() {
		super(FETCHING_MODELS, async () => {
			const state = await watermarkStatus().catch(() => null);
			return state?.ready
				? 'The models are on this machine. Sift can read watermarks now.'
				: 'The download stopped before it finished. What arrived is kept, so starting again costs only the rest.';
		});
	}
}

export const modelFetch = new ModelFetch();
