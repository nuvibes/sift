/*
 * The row of numbers on an entity's cards: what the "+n" at its end says aloud, and the worded
 * figures of the hover card.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import EntityCounts from './EntityCounts.svelte';
import SOURCE from './EntityCounts.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { cellSaid, type CountCell } from './entity-counts';

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	removeStyles();
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

function cell(id: string, label: string, count: number | undefined): CountCell {
	return { id, label, icon: 'sell', href: `/tags/t-1?show=${id}`, count } as unknown as CountCell;
}

function draw(props: {
	cells: readonly CountCell[];
	fold?: string;
	worded?: boolean;
}): HTMLElement {
	const host = document.createElement('div');
	document.body.append(host);
	instance = mount(EntityCounts, { target: host, props: { name: 'Harbor', ...props } });
	flushSync();
	return host;
}

describe('the "+n" at the end of a folded row', () => {
	it('says the number it shows', () => {
		const host = draw({ cells: [cell('loops', 'Loops', 3)], fold: '/tags/t-1' });
		const more = host.querySelector('[data-more] a') as HTMLElement;
		const shown = (more.textContent ?? '').trim();
		expect(shown).toMatch(/^\+\d+$/);
		expect(more.getAttribute('aria-label')?.startsWith(`${shown} more`)).toBe(true);
	});
});

describe('the worded figures', () => {
	it('says each number with what it counts', () => {
		const host = draw({
			cells: [cell('loops', 'Loops', 1), cell('sites', 'Sites', 4)],
			worded: true
		});
		const said = [...host.querySelectorAll('.figure')].map((one) => one.textContent?.trim());
		expect(said).toEqual(['1 loop', '4 Sites']);
		expect(host.querySelector('.count')?.getAttribute('aria-label')).toBe('1 loop');
	});

	it('says the two tabs whose label is not their noun by their label', () => {
		expect(cellSaid(cell('files', 'Pictures', 1))).toBe('1 picture');
		expect(cellSaid(cell('people', 'Seen with', 2))).toBe('Seen with 2 people');
		expect(cellSaid(cell('files', 'Files', 1957))).toBe((1957).toLocaleString() + ' files');
		expect(cellSaid(cell('loops', 'Loops', undefined))).toBeNull();
	});
});

describe('a row with nothing to count', () => {
	/* A card whose row is empty would stand shorter than the cards beside it if the row's floor
	   were a line of the card's text, while a cell is a line of the figure's text plus the link's
	   padding. The strut is built from the cell's own two measures. */
	it('holds the height of a cell, built from the same line and padding', () => {
		const host = draw({ cells: [], fold: '/sites/s-1' });
		const nav = host.querySelector('nav') as HTMLElement;
		applyStyles(SOURCE, nav);
		const strut = host.querySelector('.strut') as HTMLElement;
		expect(strut).not.toBeNull();
		const held = getComputedStyle(strut);
		expect(held.getPropertyValue('block-size').replace(/\s+/g, '')).toBe(
			'calc(1lh+2*var(--space-1))'
		);
		expect(held.getPropertyValue('inline-size')).toBe('0px');
		expect(SOURCE).toMatch(/\.figure \{\s*font: var\(--text-data\);/);
		expect(SOURCE).toMatch(/\.strut \{[^}]*font: var\(--text-data\);/);
		expect(SOURCE).toMatch(/\.count \{[^}]*padding: var\(--space-1\) var\(--space-2\);/);
	});
});
