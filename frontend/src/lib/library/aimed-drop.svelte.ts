/* A link dropped ON something: fetch it, and file what comes back under that thing. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { toasts } from '$lib/shell/toasts.svelte';

/** What a link can be dropped on. The server's own list, so the two cannot drift apart. */
export type AimedAt = NonNullable<components['schemas']['SubmitDownloadRequest']['aimed_kind']>;

/** Queue the fetch, aimed at one thing. `id` is null only for Favorites, which is a place rather
 * than a row. */
export async function fetchOnto(
	url: string,
	kind: AimedAt,
	id: string | null,
	where: string
): Promise<void> {
	try {
		await api.post('/downloads', {
			body: { url, aimed_kind: kind, aimed_id: id }
		});
		toasts.show(`Downloading — it goes to ${where}`, { icon: 'download' });
	} catch {
		toasts.show("That link couldn't be queued", { tone: 'error' });
	}
}

/** What the offer says while something is held over a drop target. */
export function dropOffer(kind: AimedAt, named?: string): string {
	const lead = named ? `Drop to add \u2014 it goes to ${named}` : 'Drop to add';
	if (kind !== 'site') return lead;
	return named
		? `${lead}, and the Site the link came from is kept too`
		: `${lead} \u2014 its own Site is kept too`;
}
