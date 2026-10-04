/* One card of a recap: its sentence drawn from the server's pieces, and the locked tile.
 *
 * The rule held here is the vault's: a card the server marks `hidden` is a locked tile and draws
 * NOTHING it was handed, not its words, not its picture, even if words came with it. The server
 * sends such a card empty; this is the second lock on the same door, and the one a mutation that
 * "draws the card plainly while locked" has to get past.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';

import type { RecapCard as Card } from '$lib/library/recaps.svelte';

import RecapCard, { shareable } from './RecapCard.svelte';
import source from './RecapCard.svelte?raw';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

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
			said: '',
			hidden_said: '',
			trend: []
		},
		cover: '/api/people/p1/cover',
		chart: null,
		rows: [],
		hidden_things: [],
		hidden: false,
		...over
	};
}

function draw(one: Card): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(RecapCard, { target: host, props: { card: one } });
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
			parts: [{ kind: 'all', value: at === 19 ? 4 : 1, said: '' }]
		}));
		const drawn = draw(card({ cover: null, chart: { bars } as unknown as Card['chart'] }));
		expect(drawn.querySelector('.card.ringed .hour-ring')).not.toBeNull();
		expect(source).toMatch(/\.story \{[^}]*aspect-ratio: 9 \/ 16;/);
		expect(source).toMatch(/\.story \{[^}]*inline-size: min\(100%, var\(--story-width\)\);/);
	});

	it('may be taken as a picture, but never as a locked tile or naming something hidden', () => {
		expect(shareable(card())).toBe(true);
		expect(shareable(card({ hidden: true }))).toBe(false);
		expect(shareable(card({ hidden_things: ['p1'] }))).toBe(false);
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
});
