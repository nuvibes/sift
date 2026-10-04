/* Which names Sift has a picture for, asked once.
 *
 * A person with no chosen cover is drawn with the picture the site they were downloaded from shows
 * them with. Asking per card would be one request per person on a screen that draws a hundred of
 * them, nearly all answered "no picture": a slow screen built entirely out of correct answers. So
 * the set of names is fetched once and every card reads it.
 *
 * Admin-only, like everything about downloading. For anybody else the ask is refused and the set
 * stays empty, which is the same thing as having no pictures: every card falls through to its
 * monogram.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

class CreatorArt {
	/** The names, lowercased, or null until the one request has come back. */
	#names = $state<Set<string> | null>(null);
	#asking: Promise<void> | null = null;

	/** Whether this name has a picture. Starts the one request if nothing has yet. */
	has(name: string): boolean {
		if (this.#names === null) {
			void this.load();
			return false;
		}
		return this.#names.has(name.trim().toLowerCase());
	}

	/** Ask, once per page. A second caller waits on the first rather than making a second request. */
	load(): Promise<void> {
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

/**
 * The picture a username wears beside its handle, or null where Sift keeps none.
 *
 * Asked by the Site as well as the name, because the same name on another Site may be somebody
 * else. Only for a name the one list says has a picture, so a column of handles asks nothing
 * about the many that have none.
 */
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
