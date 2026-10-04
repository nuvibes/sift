/* The folders Sift has been given, and the two ways that list changes.
 *
 * Adding one is the only thing in Sift that cannot be done from a page.
 *
 * A folder is granted by choosing it in the operating system's own dialog. That dialog
 * belongs to the machine, not to this document: nothing here can open it, drive it, read it or
 * pre-fill it. Which is exactly why the server is then willing to list everything inside whatever
 * comes back: nothing else confines what the backend can reach.
 *
 * So in a plain browser `canAdd` is false and this screen says so plainly rather than offering a
 * button that cannot work. That is not a missing feature: granting a folder is precisely the
 * decision that should require sitting at the machine.
 *
 * Named `-state` like every other store here, and not `grants.svelte.ts`: a module whose name ends
 * `.svelte.ts` beside a component called `Grants.svelte` is captured by that component's name on a
 * case-insensitive disk, which builds on Linux and fails on Windows and macOS.
 */

import { api, ApiError } from '$lib/api/client';
import { bridge } from '$lib/bridge';
import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';

export type Granted = components['schemas']['GrantView'];

function refusalFrom(error: unknown, fallback: string): string {
	return error instanceof ApiError ? (error.detail ?? error.message) : fallback;
}

export class Grants {
	items = $state<Granted[]>([]);
	loading = $state(true);
	failed = $state<string | null>(null);
	/** True while the dialog is open or a request is out, so a second click cannot start a second. */
	busy = $state(false);

	/** Whether this Sift can be handed a folder at all: true in the app, false in a browser. */
	get canAdd(): boolean {
		return bridge.canChooseFolder();
	}

	/**
	 * Read the folders again whenever the library moves: a folder handed over or taken back in
	 * another window is said on the library bell. Called by the screen that holds this list, while
	 * it sets up, so the listening ends with the screen.
	 */
	follow(): void {
		whenChanged(libraryChanges, () => void this.load());
	}

	async load(): Promise<void> {
		this.loading = true;
		try {
			const body = await api.get<components['schemas']['GrantsView']>('/library/grants');
			this.items = body.grants;
			this.failed = null;
		} catch (error) {
			this.failed = refusalFrom(error, "Sift couldn't read which folders it has been given.");
		} finally {
			this.loading = false;
		}
	}

	/**
	 * Make sure a folder Sift has just been pointed at is on the granted list. Says nothing.
	 *
	 * Deliberately silent, and it is not swallowing a real error. A grant is refused when the
	 * folder is already granted, or sits inside one that is, and in both of those cases the thing
	 * this call exists to guarantee is ALREADY TRUE, so there is nothing to report. The refusals
	 * that do matter (the folder is gone, it is one of Sift's own) are raised again by the step
	 * that follows, about the thing the person actually asked for, which is the better sentence to
	 * show them anyway.
	 *
	 * On the machine itself handing over a folder and adding a library are one job: the operating
	 * system's dialog is the consent, and this records it.
	 */
	async ensure(path: string): Promise<void> {
		try {
			await api.post('/library/grants', { body: { path } });
			await this.load();
		} catch {
			/* Already granted, or inside one that is. See above. */
		}
	}
}
