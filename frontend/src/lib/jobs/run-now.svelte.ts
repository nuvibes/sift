/**
 * "RUN TASK" ON A FILE OR A SELECTION: the Importing pane's own presses, filtered to what was picked.
 *
 * Settings has a "Scan now", a "Generate now" and an "Identify now", each going over the whole
 * library for whatever its switches say. A file's menu, its three-dot menu and a selection's bar
 * offer the same three, each opening onto its every-pass press ("Identify all") and then the passes
 * under it that can run for one file, in the words the Importing pane uses for them, because the
 * list is the server's and so are the words. "Identify all" is ONE request naming the stage, which
 * the server expands: it is the one place that knows which passes a stage has.
 * See `GET /importing/run-now` and `POST /assets/run`.
 *
 * ## Why the list is held rather than asked per menu
 *
 * The same reason `enrichBoxes` gives: a menu opens on every right-click, and what can run for a
 * file changes only when a feature is added to the application, never while it runs. So it is
 * loaded by the first menu that wants it and kept. Whether a pass can run on this machine RIGHT
 * NOW (a graphics card, a model download) is not in it on purpose: that answer moves, and the press
 * asks the server, which refuses with the sentence that says why.
 */
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

/**
 * Run one pass now (or a stage's every pass) for these files, and say what happened.
 *
 * The server's sentence is shown as written, whether it started work or refused: every refusal it
 * can give (a switch that is off, the file already waiting, a model not fetched yet) is written
 * for the person pressing, and "Sift could not" would leave them with nothing to act on.
 */
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
