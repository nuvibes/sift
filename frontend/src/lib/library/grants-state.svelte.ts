/* The folders Sift has been given, and the two ways that list changes. */

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

	/** Read the folders again whenever the library moves: a folder handed over or taken back in
	 * another window is said on the library bell. */
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

	/** Make sure a folder Sift has just been pointed at is on the granted list. */
	async ensure(path: string): Promise<void> {
		try {
			await api.post('/library/grants', { body: { path } });
			await this.load();
		} catch {
			/* Already granted, or inside one that is. See above. */
		}
	}
}
