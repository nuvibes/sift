import { redirect } from '@sveltejs/kit';

import { movedTo } from '$lib/organize/panels';

/*
 * A queue that has been renamed is not a dead address.
 *
 * `/organize/unidentified`, `/organize/look-alikes` and `/organize/ignored` are one list, and
 * `/organize/identified` is the record beside it. Those addresses are in links, in bookmarks, in a
 * tab somebody left open and in every note anybody wrote down. Left alone they would reach the
 * screen this application draws for a queue it has never heard of ("There is nothing of that name
 * here"), which is the right answer for a queue from another version and a wrong one for a screen
 * that is still here under a different name.
 *
 * 308 rather than 307: the move is permanent, and this is a GET. What is preserved is the query,
 * because a link into the ignored groups or a filter somebody shared is part of where they were
 * going, and dropping it would land them on the list with their filter quietly gone.
 *
 * Which name became which lives in `panels.ts` beside the registry that says what draws what: one
 * place, so the day another queue moves there is one thing to edit.
 */
export function load({ params, url }: { params: Record<string, string>; url: URL }): void {
	const now = movedTo(params.queue);
	if (now) redirect(308, `/organize/${now}${url.search}`);
}
