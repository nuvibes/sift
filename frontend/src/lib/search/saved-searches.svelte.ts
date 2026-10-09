/*
 * The searches somebody kept under names: one list for the account, loaded the first time it is
 * asked for.
 */

import { SvelteMap } from 'svelte/reactivity';
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { libraryChanges, mine } from '$lib/library/changes.svelte';
import { asNamedFilters } from '$lib/search/query-parts';

type KeptName = components['schemas']['KeptNameOut'];

/* The notes are optional here: only this store reads them. */
export type SavedSearch = Omit<components['schemas']['SavedSearchOut'], 'named'> & {
	named?: KeptName[];
};

/*
 * What a kept filter's chip says where the address cannot: a name two things share, a deleted
 * thing, a name nothing answers to. Rebuilt whole on every load.
 */
const SAID = new SvelteMap<string, string>();

const THING: Record<string, string> = {
	tags: 'a tag',
	people: 'a person',
	sites: 'a Site',
	platforms: 'a Site',
	collections: 'a collection',
	photo_sets: 'a Photo Set',
	songs: 'a song',
	song: 'a song',
	in: 'a folder',
	folder: 'a folder'
};

const AN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

export function goneLabel(field: string, value: string): string {
	const thing = THING[field] ?? 'a thing';
	const which = field === 'people' ? 'who' : 'that';
	return AN_ID.test(value)
		? `${thing} ${which} no longer exists`
		: `${thing} named ${value} no longer exists`;
}

function remember(named: readonly KeptName[]): void {
	for (const one of named) {
		SAID.set(`${one.field} ${one.value}`, one.name ?? goneLabel(one.field, one.value));
	}
}

export function keptValueLabel(field: string, value: string): string | undefined {
	return SAID.get(`${field} ${value}`);
}

/** The wall a kept filter is about when nothing says otherwise: the server's default. */
export const ASSET_WALL = 'asset';

class SavedSearches {
	items = $state<SavedSearch[]>([]);
	loaded = $state(false);
	busy = $state(false);

	/**
	 * The filters kept on ONE wall, newest first: spelled in that wall's vocabulary, so only there.
	 */
	on(kind: string): SavedSearch[] {
		return this.items.filter((item) => (item.kind ?? ASSET_WALL) === kind);
	}

	async ensure(): Promise<void> {
		if (this.loaded || this.busy) return;
		await this.reload();
	}

	/*
	 * Normalised on the way in AND out (`asNamedFilters`), so a filter saved with typed text holds
	 * named filters.
	 */
	async reload(): Promise<void> {
		this.busy = true;
		try {
			const answer = await api.get<components['schemas']['SavedSearches']>('/search/saved');
			this.items = await Promise.all(
				answer.items.map(async (item) => ({ ...item, query: await asNamedFilters(item.query) }))
			);
			SAID.clear();
			for (const item of answer.items) remember(item.named ?? []);
			this.loaded = true;
		} finally {
			this.busy = false;
		}
	}

	/* The server upserts on the name, so the list is reloaded rather than appended to. */
	async save(name: string, query: string, kind: string = ASSET_WALL): Promise<void> {
		await api.post('/search/saved', {
			body: { name, kind, query: await asNamedFilters(asAQuestion(query)) }
		});
		await this.reload();
	}

	/* Optimistic; a failure is put back by the caller's reload. */
	async rename(id: string, name: string): Promise<void> {
		const before = this.items;
		this.items = this.items.map((item) => (item.id === id ? { ...item, name } : item));
		try {
			await api.patch(`/search/saved/${id}`, { body: { name } });
		} catch (failure) {
			this.items = before;
			throw failure;
		}
	}

	/*
	 * Which one is being edited, and its draft: the edit never touches the address, so the screen
	 * keeps its own filters. Here, because the panel unmounts every time the menu shuts.
	 */
	editing = $state<{ id: string; name: string; draft: string; kind: string } | null>(null);

	async update(name: string, query: string, kind: string = ASSET_WALL): Promise<void> {
		await this.save(name, query, kind);
	}

	async remove(id: string): Promise<void> {
		// Optimistic; a failed delete is reconciled by the next load.
		this.items = this.items.filter((item) => item.id !== id);
		await api.del(`/search/saved/${id}`);
	}
}

/** Without `from` or `offset`: a saved search is a question, not a place in the answer. */
export function asAQuestion(query: string): string {
	const params = new URLSearchParams(query);
	params.delete('from');
	params.delete('offset');
	return params.toString();
}

export const savedSearches = new SavedSearches();

/* Saved elsewhere; only once this list has been read at all. */
mine.subscribe(() => {
	if (savedSearches.loaded) void savedSearches.reload();
});

/* A kept filter reads today's names, so a rename elsewhere reaches its chips. */
libraryChanges.subscribe(() => {
	if (savedSearches.loaded) void savedSearches.reload();
});
