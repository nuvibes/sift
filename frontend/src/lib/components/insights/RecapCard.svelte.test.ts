/* One card of a recap: its sentence drawn from the server's pieces, and the locked tile.
 *
 * The rule held here is the vault's: a card the server marks `hidden` is a locked tile and draws
 * NOTHING it was handed, not its words, not its picture, even if words came with it. The server
 * sends such a card empty; this is the second lock on the same door, and the one a mutation that
 * "draws the card plainly while locked" has to get past.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import type { RecapCard as Card } from '$lib/library/recaps.svelte';

import RecapCard, { savable } from './RecapCard.svelte';
import source from './RecapCard.svelte?raw';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function clear(): void {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
}

afterEach(clear);

function piece(text: string, over: Partial<Card['statement'][number]> = {}) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '', ...over };
}

function card(over: Partial<Card> = {}): Card {
	return {
		id: 'top_person',
		kind: 'top_person',
		statement: [
			piece('Your most-viewed person in September was '),
			piece('Elina Sorrel', { kind: 'person', id: 'p1', href: '/people/p1' }),
			piece(': 6 hours.')
		],
		figure: {
			label: 'Viewed',
			value: 21_600_000,
			unit: 'ms',
			hidden_part: 0,
			caption: [],
			defines: [],
			said: '',
			hidden_said: '',
			trend: []
		},
		cover: '/api/people/p1/cover',
		chart: null,
		rows: [],
		calendar: null,
		figures: [],
		hidden_things: [],
		hidden: false,
		...over
	};
}

function draw(one: Card, size?: 'micro' | 'story' | 'hero'): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(RecapCard, { target: host, props: { card: one, size, foot: 'September 2026' } });
	return host;
}

describe('a card', () => {
	it('says its sentence from the pieces, with the person as a way to them', () => {
		const drawn = draw(card());
		expect(drawn.textContent).toContain(
			'Your most-viewed person in September was Elina Sorrel: 6 hours.'
		);
		expect(drawn.querySelector('a[href="/people/p1"]')?.textContent).toBe('Elina Sorrel');
		expect(drawn.querySelector('img')?.getAttribute('src')).toBe('/api/people/p1/cover');
	});

	it('wears the hidden mark while the vault is open and it names something hidden', () => {
		expect(draw(card({ hidden_things: ['p1'] })).textContent).toContain('Names something hidden');
	});

	it('wears no mark when nothing it names is hidden', () => {
		expect(draw(card()).textContent).not.toContain('Names something hidden');
	});
});

describe('a locked tile', () => {
	it('draws the mark and none of the words, the picture or the link it was handed', () => {
		const drawn = draw(card({ hidden: true }));
		expect(drawn.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe('Hidden');
		expect(drawn.textContent).not.toContain('Elina Sorrel');
		expect(drawn.textContent).not.toContain('most-viewed');
		expect(drawn.querySelector('img')).toBeNull();
		expect(drawn.querySelector('a')).toBeNull();
	});
});

describe('a story card', () => {
	/* Stood up 9:16 at the story's width, whatever the screen: on a phone it is the phone's width. */
	it('is stood up as a story, and a card with the hours carries the ring', () => {
		const bars = Array.from({ length: 24 }, (_one, at) => ({
			label: String(at),
			said: '',
			parts: [{ kind: 'all', value: at === 19 ? 4 : 1, said: '' }],
			...(at === 0 ? { said: 'one tick' } : {})
		}));
		const drawn = draw(card({ cover: null, chart: { bars } as unknown as Card['chart'] }));
		expect(drawn.querySelector('.card.ringed .hour-ring')).not.toBeNull();
		expect(drawn.querySelector('.recap-card.story')).not.toBeNull();
		// The ring reads its hours in the words the server sent for them.
		const dial = drawn.querySelector('.hour-ring .dial') as HTMLElement;
		dial.focus();
		flushSync();
		expect(dial.getAttribute('aria-label')).toContain('one tick');
		expect(drawn.querySelector('.foot')?.textContent).toBe('September 2026');
	});

	/* The holder decides the width and the ratio the height, so every card of a deck is one size
	   whatever it says; a width of its own content's would make a short card a small one. */
	it("takes its holder's whole width at 9:16, never a width its words decide", () => {
		expect(source).toMatch(/\.story \{[^}]*inline-size: 100%;[^}]*aspect-ratio: 9 \/ 16;/);
		expect(source).toMatch(/\.story \{[^}]*container-type: inline-size;/);
	});

	it('may be saved as a picture, but never as a locked tile or naming something hidden', () => {
		expect(savable(card())).toBe(true);
		expect(savable(card({ hidden: true }))).toBe(false);
		expect(savable(card({ hidden_things: ['p1'] }))).toBe(false);
	});

	it("draws a cover through the picture that falls back to the thing's letter", () => {
		/* A tag with no picture answers its cover address 404; every caller of that address draws a
		   letter rather than the browser's broken-picture mark. */
		const drawn = draw(card());
		expect(drawn.querySelector('.avatar.portrait')).not.toBeNull();
		expect(drawn.querySelector('.monogram')?.textContent?.trim()).toBe('E');
	});

	it('leaves a card with a cover beside its words', () => {
		const drawn = draw(card());
		expect(drawn.querySelector('.card.pictured')).not.toBeNull();
		expect(drawn.querySelector('.card.ringed')).toBeNull();
	});

	/* A Site's picture is its mark, square and drawn to its own edges: cut to a portrait's 3:4 it
	   loses its top and foot. It is drawn whole on its own ground, as the Sites wall draws it. */
	it("frames a Site's mark whole and a song's art square, and a person's cover as a portrait", () => {
		const site = draw(card({ kind: 'top_site', cover: '/api/sites/s1/cover' }));
		expect(site.querySelector('.avatar.portrait.mark')).not.toBeNull();
		clear();
		const again = draw(
			card({
				kind: 'rediscovered',
				statement: [piece('You came back to '), piece('Quillhouse', { kind: 'site', id: 's1' })],
				cover: '/api/sites/s1/cover'
			})
		);
		expect(again.querySelector('.avatar.portrait.mark')).not.toBeNull();
		clear();
		const song = draw(card({ kind: 'top_song', cover: '/api/songs/s1/cover' }));
		expect(song.querySelector('.avatar.face')).not.toBeNull();
		expect(song.querySelector('.avatar.mark')).toBeNull();
		clear();
		const person = draw(card());
		expect(person.querySelector('.avatar.portrait')).not.toBeNull();
		expect(person.querySelector('.avatar.mark')).toBeNull();
	});

	it('sets a long sentence a step smaller, and a short one at its own size', () => {
		const long = 'You viewed the most on Saturdays: 7 hours. Your most-viewed hour began at 10 PM.';
		expect(
			draw(card({ statement: [piece(long), piece(' Two hours.')] })).querySelector('.said.long')
		).not.toBeNull();
		clear();
		expect(draw(card()).querySelector('.said.long')).toBeNull();
	});
});

describe('the three sizes', () => {
	it('draws a micro card as its figure and sentence, without the picture, list or ring', () => {
		const drawn = draw(card(), 'micro');
		expect(drawn.querySelector('.recap-card.micro')).not.toBeNull();
		expect(drawn.querySelector('.figure-card.small')).not.toBeNull();
		expect(drawn.textContent).toContain('Elina Sorrel');
		expect(drawn.querySelector('.avatar')).toBeNull();
		expect(drawn.querySelector('.head, .foot')).toBeNull();
	});

	it("draws a hero card as the story card, up to the saved picture's width", () => {
		const drawn = draw(card(), 'hero');
		expect(drawn.querySelector('.recap-card.hero .figure-card.hero')).not.toBeNull();
		expect(source).toMatch(
			/\.hero \{[^}]*inline-size: min\(100%, calc\(var\(--story-width\) \* 3\)\);/
		);
	});

	/* Inside the card the page's sizes are restated in the card's own width, so a card 1080 wide
	   is the card 360 wide three times over; each must equal the page's own at 360, or the deck's
	   card and the page beside it are set in two scales. */
	it("restates each page size it reads in the card's width, equal to the page's at 360", () => {
		const css = readFileSync(resolve('src/app.css'), 'utf8');
		const token = (name: string) => new RegExp(`\\n\\s*--${name}: ([^;]+);`).exec(css)?.[1] ?? '';
		const px = (value: string) =>
			value.endsWith('rem') ? Number.parseFloat(value) * 16 : Number.parseFloat(value);
		const restated = [...source.matchAll(/--([a-z0-9-]+): var\(--story-\1\);/g)].map((m) => m[1]);
		expect(restated.length).toBeGreaterThanOrEqual(20);
		for (const name of restated) {
			const page = token(name);
			const story = token(`story-${name}`);
			const at360 = /calc\(([\d.]+) \* var\(--story-px\)\)/.exec(story)?.[1];
			expect(at360, name).toBeDefined();
			if (name.startsWith('text-')) {
				const [, weight, size, rest] = /^(\d+) ([\d.]+rem)\/(.+)$/.exec(page) ?? [];
				expect(story, name).toBe(`${weight} calc(${px(size)} * var(--story-px)) / ${rest}`);
			} else {
				expect(Number(at360), name).toBe(px(page));
			}
		}
		expect(token('story-px')).toBe('calc(100cqi / 360)');
		expect(token('story-width')).toBe('360px');
	});
});
