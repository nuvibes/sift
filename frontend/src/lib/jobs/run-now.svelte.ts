/** "RUN TASK" ON A FILE OR A SELECTION: the Importing pane's own presses, filtered to what was
 * picked. */
import { ApiError, api } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';
import type { components } from '$lib/api/schema';

/* LIVE: nothing moves it (the passes a file can be run through are the server's code, and a new version reloads the window) */

export type RunNowGroup = components['schemas']['RunNowGroup'];
type RunNowPasses = components['schemas']['RunNowPasses'];
type RunNowStarted = components['schemas']['RunNowStarted'];

let known = $state<RunNowGroup[] | null>(null);
let asking: Promise<void> | null = null;

/** The stages and their per-file passes, as the server declared them. Empty until loaded. */
export function runNowGroups(): readonly RunNowGroup[] {
	return known ?? [];
}

/** Load it once. Safe to call from every menu that opens: the second call joins the first. */
export function loadRunNowGroups(): void {
	if (known !== null || asking !== null) return;
	asking = (async () => {
		try {
			known = (await api.get<RunNowPasses>('/importing/run-now')).groups;
		} catch {
			// A guest, or a server that cannot be reached: no Run task row, which is the right failure,
			// since the passes still run for every file that arrives, and Importing still has its own.
			known = [];
		} finally {
			asking = null;
		}
	})();
}

/** Run one pass now (or a stage's every pass) for these files, and say what happened. */
export async function runNow(ids: string[], run: string): Promise<void> {
	try {
		const started = await api.post<RunNowStarted>('/assets/run', {
			body: { run, asset_ids: ids }
		});
		toasts.show(started.said);
	} catch (error) {
		const said = error instanceof ApiError && error.status === 409 ? error.detail : undefined;
		toasts.show(said ?? "Sift couldn't start that for these files", { tone: 'error' });
	}
}
