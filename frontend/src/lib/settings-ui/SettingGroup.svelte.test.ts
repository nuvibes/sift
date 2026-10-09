/* Only a page's first group goes without a heading: a later group with no heading continues the
 * group before it, closing the gap and drawing the line between two rows, so two unheaded groups
 * never sit a band apart with nothing between them. */
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { afterEach, expect, it } from 'vitest';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import SettingGroup from './SettingGroup.svelte';
import source from './SettingGroup.svelte?raw';

let host: HTMLElement | undefined;
const mounted: Record<string, unknown>[] = [];

afterEach(() => {
	for (const one of mounted.splice(0)) unmount(one);
	host?.remove();
	host = undefined;
	removeStyles();
});

function group(heading?: string): HTMLElement {
	host ??= document.createElement('div');
	document.body.append(host);
	const target = document.createElement('div');
	host.append(target);
	mounted.push(
		mount(SettingGroup, {
			target,
			props: {
				heading,
				children: createRawSnippet(() => ({ render: () => '<div class="row">a row</div>' }))
			}
		}) as Record<string, unknown>
	);
	flushSync();
	const section = target.querySelector<HTMLElement>('section.group');
	if (!section) throw new Error('no group drawn');
	return section;
}

it('marks a group with no heading as continuing the one before it', () => {
	expect(group('Storage').classList.contains('continues')).toBe(false);
	expect(group().classList.contains('continues')).toBe(true);
});

it('closes the gap above a continuing group and draws the line between two rows', () => {
	const style = source.slice(source.indexOf('<style>'));
	expect(style).toMatch(
		/\.group:has\(> \.rows\)\) \+ \.group\.continues \{[^}]*margin-block-start: calc\(-1 \* var\(--space-8\)\)/
	);
	expect(style).toMatch(
		/\.group\.continues > \.rows > :global\(\.row:first-child\) \{[^}]*border-block-start: 1px solid var\(--sift-line\)/
	);
});

/** Groups drawn one after another in ONE parent, the way a pane draws them, with the styles on. */
function siblings(...groups: { heading?: string; rows: boolean }[]): HTMLElement[] {
	host ??= document.createElement('div');
	document.body.append(host);
	const target = document.createElement('div');
	host.append(target);
	for (const one of groups) {
		mounted.push(
			mount(SettingGroup, {
				target,
				props: {
					heading: one.heading,
					help: one.heading ? 'What the rows below are for.' : undefined,
					children: one.rows
						? createRawSnippet(() => ({ render: () => '<div class="row">a row</div>' }))
						: undefined
				}
			}) as Record<string, unknown>
		);
	}
	flushSync();
	const drawn = [...target.querySelectorAll<HTMLElement>('section.group')];
	applyStyles(source, drawn[0]);
	return drawn;
}

it('follows a heading alone at the ordinary gap, never pulled up over its sentence', () => {
	/* A heading with its sentence and no rows, then the rows it introduces in a group of their
	   own: the rows sit under the sentence. */
	const [, rows] = siblings({ heading: 'Log', rows: false }, { rows: true });
	expect(rows.classList.contains('continues')).toBe(true);
	expect(getComputedStyle(rows).marginBlockStart).not.toMatch(/-1/);
});

it('still closes the gap under a group that has rows', () => {
	const [, rows] = siblings({ heading: 'Storage', rows: true }, { rows: true });
	expect(getComputedStyle(rows).marginBlockStart).toMatch(/calc\(-1 \* var\(--space-8\)\)/);
});

it('keeps the trailing space of the rows inside the group, so pulling the next group up cannot eat it', () => {
	const [first] = siblings({ heading: 'Storage', rows: true }, { rows: true });
	const rows = first.querySelector('.rows');
	if (!rows) throw new Error('no rows drawn');
	expect(getComputedStyle(rows).display).toBe('flow-root');
});
