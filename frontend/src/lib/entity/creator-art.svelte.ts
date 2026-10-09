/* Which names Sift has a picture for, asked once. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { arrivals, downloadChanges } from '$lib/library/changes.svelte';

class CreatorArt {
	/** The names, lowercased, or null until the one request has come back. */
	#names = $state<Set<string> | null>(null);
	#asking: Promise<void> | null = null;
	/* A download may have brought a picture since: the next card drawn asks again, and none before. */
	#stale = false;

	constructor() {
		arrivals.subscribe(() => (this.#stale = true));
		downloadChanges.subscribe(() => (this.#stale = true));
	}

	/** Whether this name has a picture. Starts the one request if nothing has yet. */
	has(name: string): boolean {
		if (this.#names === null || this.#stale) void this.load();
		if (this.#names === null) return false;
		return this.#names.has(name.trim().toLowerCase());
	}

	/** Ask, once per page. A second caller waits on the first rather than making a second request. */
	load(): Promise<void> {
		if (this.#stale) {
			this.#stale = false;
			this.#asking = null;
		}
		this.#asking ??= api
			.get<components['schemas']['CreatorsWithArt']>('/creator-art')
			.then((answer) => {
				this.#names = new Set(answer.usernames.map((username) => username.trim().toLowerCase()));
			})
			.catch(() => {
				// Refused, or the server is briefly away. An empty set means every card keeps its
				// monogram.
				this.#names = new Set();
			});
		return this.#asking;
	}
}

export const creatorArt = new CreatorArt();

/** The picture a username wears beside its handle, or null where Sift keeps none. */
export function usernameArt(one: {
	username: string;
	site_name?: string | null;
	url?: string | null;
}): string | null {
	const handle = one.username.trim();
	if (!handle || !one.site_name || !creatorArt.has(handle)) return null;
	const asked = new URLSearchParams({ site: one.site_name });
	if (one.url) asked.set('address', one.url);
	return `/api/creator-art/${encodeURIComponent(handle)}?${asked.toString()}`;
}
