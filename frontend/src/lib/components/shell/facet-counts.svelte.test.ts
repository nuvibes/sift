/* The facet counts held for the tab: asked once a question, coalesced, refreshed in place. */

import { afterEach, describe, expect, it, vi } from 'vitest';

const sent = vi.hoisted(() => ({
	asked: [] as string[],
	/* Holds every answer back until the test lets them go. */
	gate: null as Promise<void> | null,
	count: 7
}));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (_path: string, options?: { query?: Record<string, unknown> }) => {
			const facet = String(options?.query?.facet ?? '');
			sent.asked.push(`${facet}:${JSON.stringify(options?.query?.media ?? null)}`);
			if (sent.gate) await sent.gate;
			if (facet === 'broken') throw new Error('refused');
			return { facet, values: [{ value: 'video', count: sent.count }] };
		})
	}
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));

import type { FacetQuestion } from './facet-counts.svelte';

const { FacetCountStore, columnQuery, rememberedColumns, columnsKey } =
	await import('./facet-counts.svelte');
const { libraryChanges } = await import('$lib/library/changes.svelte');
const { vault } = await import('$lib/shell/vault.svelte');

const settle = () => new Promise((done) => setTimeout(done, 0));

function held() {
	let open = () => {};
	sent.gate = new Promise((resolve) => (open = resolve));
	return () => {
		sent.gate = null;
		open();
	};
}

const question = (media?: string): FacetQuestion => ({
	noun: 'asset' as const,
	facets: ['media', 'tags'],
	query: media ? { media } : {},
	within: {}
});

afterEach(() => {
	sent.asked = [];
	sent.gate = null;
	sent.count = 7;
	localStorage.clear();
});

describe('asking', () => {
	it('asks each column once for a question, however often it is asked', async () => {
		const store = new FacetCountStore();
		store.ask(question());
		store.ask(question());
		await settle();
		store.ask(question());
		await settle();
		expect(sent.asked).toEqual(['media:null', 'tags:null']);
		expect(store.answerTo(question())?.media?.[0]?.count).toBe(7);
	});

	it('keeps one batch out and only the newest question waiting behind it', async () => {
		const store = new FacetCountStore();
		const open = held();
		store.ask(question());
		store.ask(question('video'));
		store.ask(question('image'));
		open();
		await settle();
		await settle();
		expect(sent.asked).toEqual(['media:null', 'tags:null', 'media:null', 'tags:"image"']);
		expect(store.answerTo(question('video'))).toBeUndefined();
		expect(store.answerTo(question('image'))).toBeDefined();
	});

	it('draws nothing for a question never asked, and an empty column for one refused', async () => {
		const store = new FacetCountStore();
		expect(store.answerTo(question())).toBeUndefined();
		store.ask({ ...question(), facets: ['broken'] });
		store.ask({ ...question(), facets: [] });
		await settle();
		expect(store.answerTo({ ...question(), facets: ['broken'] })).toEqual({ broken: [] });
	});
});

describe('the live feed', () => {
	it('re-asks on a bell and keeps the old numbers drawn until the new ones land', async () => {
		const store = new FacetCountStore();
		store.ask(question());
		await settle();
		sent.count = 9;
		libraryChanges.generation += 1;
		const open = held();
		store.ask(question());
		expect(store.answerTo(question())?.media?.[0]?.count).toBe(7);
		open();
		await settle();
		expect(store.answerTo(question())?.media?.[0]?.count).toBe(9);
	});

	it('never draws counts taken with the vault open once it has locked', async () => {
		const store = new FacetCountStore();
		store.ask(question());
		await settle();
		vault.generation += 1;
		expect(store.answerTo(question())).toBeUndefined();
	});
});

describe('the columns', () => {
	it('opens on the remembered columns, refusing what the noun does not offer', () => {
		expect(rememberedColumns('asset')).toHaveLength(5);
		localStorage.setItem(columnsKey('asset'), 'nonsense,rating,rating');
		const columns = rememberedColumns('asset');
		expect(columns[0]).toBe('rating');
		expect(columns).toHaveLength(5);
		expect(new Set(columns).size).toBe(5);
	});

	it("counts a column without its own pick, within the target's", () => {
		expect(
			columnQuery(
				{ noun: 'asset', facets: [], query: { media: 'video', tags: 'a' }, within: { tags: 'b' } },
				'media'
			)
		).toEqual({ tags: ['a', 'b'] });
	});
});
