/*
 * What a thing kept by its ID is called NOW.
 *
 * Two screens keep things by id and draw them by name: a picker's memory of what was picked last
 * (`$lib/search/frequent`) and swap mode's picks (`$lib/swap/mode.svelte`). A copy of the name taken
 * when the thing was picked goes stale at the first rename: a tag renamed on its own page would
 * go on being offered under its old name at the top of every picker until it was picked again. So the id is what is kept, and the name is asked for here, live,
 * from the one place that names things by id for a viewer (`/search/names-now`, scoped exactly as
 * a filter is).
 *
 * A thing the server does not name is gone, or not this viewer's to see, and the two are one
 * answer: `null`. A thing not asked about yet is `undefined`, and a screen draws nothing for it
 * rather than the old copy: an empty moment is better than a name the library no longer uses.
 */
import { SvelteMap } from 'svelte/reactivity';
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The kinds a screen keeps by id and names here. The picker's and the swap drawer's own words. */
type NamedKind = 'tag' | 'person' | 'site' | 'collection' | 'photo_set' | 'song';

/** The most ids one ask names. The server refuses more, with the same figure. */
const MOST_NAMED = 100;

function keyOf(kind: NamedKind, id: string): string {
	return `${kind}:${id}`;
}

/** What the server last said each asked-for thing is called, or `null` for one it did not name. */
const said = new SvelteMap<string, string | null>();

/**
 * Ask what these things are called now. Every id asked about is settled when this resolves: named,
 * or `null`. A failed ask settles nothing, so what was said before stands.
 */
export async function askNames(kind: NamedKind, ids: readonly string[]): Promise<void> {
	const wanted = [...new Set(ids)].slice(0, MOST_NAMED);
	if (wanted.length === 0) return;
	let answer: components['schemas']['NamesNow'];
	try {
		answer = await api.get<components['schemas']['NamesNow']>('/search/names-now', {
			query: { kind, id: wanted }
		});
	} catch {
		return;
	}
	// An answer in any other shape says nothing, and settles nothing, as a failed ask does.
	if (!Array.isArray(answer?.items)) return;
	const named = new Map(answer.items.map((one) => [one.id, one.name]));
	for (const id of wanted) said.set(keyOf(kind, id), named.get(id) ?? null);
}

/** What this thing is called now: its name, `null` for gone, `undefined` for not asked yet. */
export function nameNow(kind: NamedKind, id: string): string | null | undefined {
	return said.get(keyOf(kind, id));
}
