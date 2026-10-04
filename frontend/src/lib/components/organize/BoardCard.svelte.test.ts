/*
 * One card on the Organize board: the name, the one sentence saying what the pile is for, the
 * count leading, two rows of stills, and ONE button, Review, that opens the pile's page. Nothing
 * on the card decides anything: no question, no answers, no chips. The whole card is a press under
 * its face that opens what the button opens.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { goto } from '$app/navigation';
import BoardProbe from './BoardProbe.test.svelte';
import boardSource from './BoardCard.svelte?raw';
import { CAUGHT_UP, REVIEW, settled } from './BoardCard.svelte';
import { words } from '$lib/design/testing.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

function queue(over: Record<string, unknown> = {}) {
	return {
		name: 'shoots',
		title: 'Shoots',
		decision: 'Say whether pictures belong together.',
		purpose: 'Photos that look like one shoot, for you to create a Photo Set from.',
		icon: 'photo_library',
		count: 4,
		verb: 'shoots to review',
		verb_one: 'shoot to review',
		band: 'decision',
		group: null,
		group_title: null,
		pending: true,
		preview: [],
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	vi.mocked(goto).mockClear();
});

afterEach(() => {
	if (drawn) unmount(drawn, { outro: false });
	drawn = null;
	host?.remove();
});

async function draw(...queues: ReturnType<typeof queue>[]): Promise<void> {
	const work = queues.filter((one) => one.pending);
	drawn = mount(BoardProbe, {
		target: host,
		props: {
			card: {
				lead: queues[0],
				queues,
				count: work.reduce((sum, one) => sum + one.count, 0)
			} as never
		}
	}) as Record<string, unknown>;
	flushSync();
	await tick();
	flushSync();
}

describe('the card, top to bottom', () => {
	it('stands the name, the purpose, the lead and the stills in that order, the button at the foot', async () => {
		await draw(queue({ preview: [{ kind: 'asset', id: 'a1', href: '/asset/a1' }] }));

		const panel = host.querySelector('.board-card .panel') as HTMLElement;
		expect([...panel.children].map((one) => one.classList[0])).toEqual([
			'section-heading',
			'purpose',
			'lead',
			'strip',
			'foot'
		]);
		expect(words(host.querySelector('.purpose') as HTMLElement)).toBe(
			'Photos that look like one shoot, for you to create a Photo Set from.'
		);
		expect(host.querySelector('.n')?.textContent).toBe('4');
		expect(host.querySelector('.verb')?.textContent).toBe('shoots to review');
	});

	it('leads with the count in the display face and what it counts beside it', () => {
		const style = /<style>([\s\S]*)<\/style>/.exec(boardSource)?.[1] ?? '';
		expect(style).toMatch(/\.n \{[^}]*font: var\(--text-display\);/);
		expect(style).toMatch(/\.verb \{[^}]*font: var\(--text-h2\);/);
	});

	it('names the pile in plain words, never a link of its own', async () => {
		await draw(queue());

		expect(words(host.querySelector('.section-heading') as HTMLElement)).toContain('Shoots');
		expect(host.querySelector('a')).toBeNull();
	});

	it('keeps the two lines of the purpose when the server sends none', async () => {
		await draw(queue({ purpose: undefined }));

		expect(host.querySelector('.purpose')?.textContent).toBe('');
		const style = /<style>([\s\S]*)<\/style>/.exec(boardSource)?.[1] ?? '';
		expect(style).toMatch(/\.purpose \{[^}]*min-block-size: 2lh;[^}]*\}/);
		expect(style).toMatch(/\.purpose \{[^}]*line-clamp: 2;[^}]*\}/);
	});

	it('reads the singular at one', async () => {
		await draw(queue({ count: 1 }));

		expect(host.querySelector('.verb')?.textContent).toBe('shoot to review');
	});

	it('says a group of queues that are work as waiting, their counts added up, and no chips', async () => {
		await draw(
			queue({
				name: 'duplicates',
				title: 'Near duplicates',
				group: 'duplicates',
				group_title: 'Duplicates',
				count: 40,
				verb: 'groups that look alike'
			}),
			queue({
				name: 'copies',
				title: 'Exact duplicates',
				group: 'duplicates',
				count: 12,
				band: 'cleanup'
			})
		);

		expect(host.querySelector('.n')?.textContent).toBe('52');
		expect(host.querySelector('.verb')?.textContent).toBe('waiting');
		expect(words(host.querySelector('.section-heading') as HTMLElement)).toContain('Duplicates');
		expect(host.querySelector('.chip, .parts')).toBeNull();
	});

	it("keeps the lead queue's own words where the group's other part is a record", async () => {
		await draw(
			queue({
				name: 'folders',
				title: 'Folders to review',
				group: 'folders',
				group_title: 'Folders',
				count: 11,
				verb: 'folders to name'
			}),
			queue({
				name: 'filed',
				title: 'Added without asking',
				group: 'folders',
				count: 28,
				band: 'record',
				pending: false
			})
		);

		expect(host.querySelector('.n')?.textContent).toBe('11');
		expect(host.querySelector('.verb')?.textContent).toBe('folders to name');
		expect(words(host.querySelector('.section-heading') as HTMLElement)).toContain('Folders');
	});
});

describe('the one button', () => {
	it('says Review, opens the pile and does nothing else', async () => {
		await draw(queue());

		const buttons = [...host.querySelectorAll<HTMLButtonElement>('.face button')];
		expect(buttons).toHaveLength(1);
		expect(words(buttons[0])).toContain(REVIEW);
		expect(buttons[0].getAttribute('aria-label')).toBe('Review Shoots');
		buttons[0].click();
		expect(goto).toHaveBeenCalledWith('/organize/shoots');
		expect(goto).toHaveBeenCalledTimes(1);
	});

	it('is the word every card says', () => {
		expect(REVIEW).toBe('Review');
	});

	it('goes where the queue says it opens, for a pile that is not a page of its own', async () => {
		await draw(
			queue({ name: 'music', title: 'Music', opens: '/settings/schedule#tasks.music.when' })
		);

		host.querySelector<HTMLButtonElement>('.face button')?.click();
		expect(goto).toHaveBeenCalledWith('/settings/schedule#tasks.music.when');
	});

	it('is not drawn, and the card is no press, for a queue this version cannot open', async () => {
		await draw(queue({ name: 'from-a-later-version', title: 'Something newer' }));

		expect(host.querySelector('.face button')).toBeNull();
		expect(host.querySelector('.press')).toBeNull();
		expect(host.textContent).toContain('Something newer');
	});
});

describe('the stills', () => {
	it('draws two rows of six at most, each a picture and never a link', async () => {
		await draw(
			queue({
				preview: Array.from({ length: 20 }, (_unused, at) => ({
					kind: 'asset',
					id: `a${at}`,
					href: `/asset/a${at}`
				}))
			})
		);

		expect(host.querySelectorAll('.strip img')).toHaveLength(12);
		expect(host.querySelector('.strip a')).toBeNull();
	});

	it('leaves a still that cannot be drawn for the next one', async () => {
		await draw(
			queue({
				preview: Array.from({ length: 14 }, (_unused, at) => ({ kind: 'asset', id: `a${at}` }))
			})
		);

		host.querySelector<HTMLImageElement>('.strip img')?.dispatchEvent(new Event('error'));
		flushSync();

		const shown = [...host.querySelectorAll<HTMLImageElement>('.strip img')];
		expect(shown).toHaveLength(12);
		expect(shown.some((one) => one.src.includes('/a0/'))).toBe(false);
	});

	it('draws no strip for a pile with no stills', async () => {
		await draw(queue());

		expect(host.querySelector('.strip')).toBeNull();
	});
});

describe('the press and the state layer', () => {
	it('lays one press under the face, out of tab order, that opens the pile', async () => {
		await draw(queue());

		const press = host.querySelector<HTMLButtonElement>('.board-card > .press');
		expect(press?.getAttribute('aria-hidden')).toBe('true');
		expect(press?.tabIndex).toBe(-1);
		press?.click();
		expect(goto).toHaveBeenCalledWith('/organize/shoots');
	});

	it('answers the pointer with the state layer, stepping the hairline up with it', () => {
		const style = /<style>([\s\S]*)<\/style>/.exec(boardSource)?.[1] ?? '';
		expect(style).toMatch(
			/\.board-card\.opens:hover :global\(\.panel\) \{\s*background: var\(--sift-card-hover-layer\), var\(--sift-card-fill\);\s*--sift-line: var\(--sift-card-hover-line\);/
		);
		expect(style).toMatch(
			/\.press:active\)\) :global\(\.panel\) \{\s*background: var\(--sift-card-press-layer\), var\(--sift-card-fill\)/
		);
	});
});

describe('which piles are settled', () => {
	it('is a pile at nought, and no other', () => {
		expect(settled({ lead: {}, queues: [], count: 0 })).toBe(true);
		expect(settled({ lead: {}, queues: [], count: 3 })).toBe(false);
	});

	it('keeps its card at nought and says there is nothing to review where its stills would be', async () => {
		/* A pile that left the wall when it emptied could not be told from one that was never
		   turned on. It stays, leads with its nought, says so, and its button still opens the page
		   the records are on. */
		await draw(queue({ count: 0 }));

		expect(host.querySelector('.n')?.textContent).toBe('0');
		expect(host.querySelector('.verb')?.textContent).toBe('shoots to review');
		expect(words(host.querySelector('.caught-up') as HTMLElement)).toBe(CAUGHT_UP);
		expect(host.querySelector('.strip')).toBeNull();
		expect(host.querySelector('.door button')).not.toBeNull();
	});

	it('says nothing of the kind on a pile with something in it', async () => {
		await draw(queue({ count: 2 }));

		expect(host.querySelector('.caught-up')).toBeNull();
	});
});
