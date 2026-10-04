/*
 * A link dropped ON something: fetch it, and file what comes back under that thing.
 *
 * ## Why this is a download and not the ordinary import
 *
 * Dropping a link anywhere in the window already works: the overlay takes it and it lands wherever
 * downloads land. What that path cannot carry is an AIM. The filing happens minutes later, when
 * there is a file to file, by which time the page that took the drop may be closed; so the target is
 * written onto the download's own row when it is queued and read back when it lands.
 *
 * That is why this posts a download directly rather than going through capture: capture is for a
 * drop that might be a file OR a link and has nowhere in particular to go. A link dropped on a
 * person is unambiguous about both.
 *
 * A FILE dropped on a card is deliberately not handled here. It falls through to the window overlay
 * and is imported the ordinary way. Swallowing it to do something half-built with it would be worse
 * than the ordinary import.
 *
 * ## Why the message says what it says
 *
 * The fetch takes as long as it takes, and nothing on the card can show that. The toast is the only
 * acknowledgement, so it names the thing the file is being filed under. Otherwise a drop on the
 * wrong card looks exactly like a drop on the right one.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { toasts } from '$lib/shell/toasts.svelte';

/** What a link can be dropped on. The server's own list, so the two cannot drift apart. */
export type AimedAt = NonNullable<components['schemas']['SubmitDownloadRequest']['aimed_kind']>;

/**
 * Queue the fetch, aimed at one thing.
 *
 * `id` is null only for Favorites, which is a place rather than a row. The server refuses an id
 * with it, and refuses a missing one for every other kind, so the two halves cannot be mixed up
 * silently here or there.
 */
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

/**
 * What the offer says while something is held over a drop target.
 *
 * One sentence in one place, because there are two surfaces drawing it at two sizes (a card, and
 * the wash over a whole entity page), and they must say the same thing about the same act.
 *
 * ## Why a Site says more than the other four
 *
 * A dropped link ADDS, on every kind. For a person, a tag, a collection and a Photo Set nobody
 * would read it any other way: nothing about a link says who is in it. A SITE is the one aim that
 * answers a question the LINK also answers, so a drop there reads as "file it here INSTEAD", which
 * it does not.
 *
 * So this is where the screen says it adds. A promise made at the moment of the gesture is the only
 * one that can change what somebody does with it.
 *
 * `named` is the thing being aimed at, where the surface has room to name it. A card is the name,
 * so repeating it inside the card would be saying it twice.
 */
export function dropOffer(kind: AimedAt, named?: string): string {
	const lead = named ? `Drop to add \u2014 it goes to ${named}` : 'Drop to add';
	if (kind !== 'site') return lead;
	return named
		? `${lead}, and the Site the link came from is kept too`
		: `${lead} \u2014 its own Site is kept too`;
}
