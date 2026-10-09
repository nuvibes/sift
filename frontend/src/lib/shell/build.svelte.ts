/*
 * Whether the page on screen is still the one this server would serve: a desktop window never
 * reloads by itself. Asked again whenever the live connection comes back; the shell offers a reload
 * and never takes one.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/* LIVE: followed by routes/+layout.svelte (asks again each time the live connection comes up, the one moment a new server can have started) */

type VersionReport = components['schemas']['VersionReport'];

export class BuildWatch {
	#loaded = $state<string | null>(null);

	stale = $state(false);

	get loaded(): string | null {
		return this.#loaded;
	}

	/**
	 * The first answer is the baseline. An empty build is a source checkout with no client built
	 * in, so it is ignored.
	 */
	async check(): Promise<void> {
		let report: VersionReport;
		try {
			report = await api.get<VersionReport>('/update/version');
		} catch {
			// The next reconnect asks again; a banner for a network blip would be wrong.
			return;
		}
		const build = report.build ?? '';
		if (!build) return;
		if (this.#loaded === null) {
			this.#loaded = build;
			return;
		}
		if (build !== this.#loaded) this.stale = true;
	}

	reload(): void {
		location.reload();
	}

	forget(): void {
		this.#loaded = null;
		this.stale = false;
	}
}

export const build = new BuildWatch();
