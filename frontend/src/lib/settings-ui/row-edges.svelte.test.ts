/*
 * The two edges every row on a settings pane stands on: the names start on one line and the
 * controls end on another. A list of data rows pads its words in from its own hover ground, so a
 * pane that holds one pulls the list out by that padding; read here from the compiled stylesheets
 * with the spacing tokens the app declares, because the unit environment lays nothing out.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { flushSync, mount, tick, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import dataRow from '$lib/components/common/DataRow.svelte?raw';
import dataRows from '$lib/components/common/DataRows.svelte?raw';
import Users from './Users.svelte';

const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	session: { viewer: { id: 'u1', username: 'kate', role: 'admin' }, isAdmin: true }
}));

vi.mock('$lib/api/client', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
	return { ...actual, api: { get: mocks.get, post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});
vi.mock('$lib/shell/session.svelte', () => ({ session: mocks.session }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const TOKENS = readFileSync(resolve('src/app.css'), 'utf8');

/** A length as the stylesheet writes it, in pixels: spacing tokens, and `calc(-1 * x)`. */
function pixels(value: string): number {
	let text = value.trim();
	for (let found = /var\((--[\w-]+)\)/.exec(text); found; found = /var\((--[\w-]+)\)/.exec(text)) {
		const declared = new RegExp(`${found[1]}:\\s*([\\d.]+)px`).exec(TOKENS)?.[1];
		if (declared === undefined) throw new Error(`no ${found[1]} in the token layer`);
		text = text.replace(found[0], `${declared}px`);
	}
	const negated = /^calc\(\s*-1\s*\*\s*([\d.]+)px\s*\)$/.exec(text);
	if (negated) return -Number(negated[1]);
	if (text === '0' || text === '') return 0;
	const plain = /^(-?[\d.]+)px$/.exec(text);
	if (!plain) throw new Error(`cannot read "${value}"`);
	return Number(plain[1]);
}

/**
 * One side of a logical box. The unit environment keeps a logical shorthand as written and does
 * not fold it into the sides, which it reports at their initial `0`; so a side still at `0` is read
 * from the shorthand, and one a rule wrote is read as written.
 */
function side(element: Element, box: 'margin' | 'padding', end: 'start' | 'end'): number {
	const style = getComputedStyle(element);
	const longhand = style.getPropertyValue(`${box}-inline-${end}`).trim();
	if (longhand && longhand !== '0' && longhand !== '0px') return pixels(longhand);
	const shorthand = style.getPropertyValue(`${box}-inline`).trim();
	const parts = shorthand.match(/calc\([^)]*\)|var\([^)]*\)|\S+/g) ?? [];
	return pixels((end === 'start' ? parts[0] : (parts[1] ?? parts[0])) ?? '0');
}

let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	removeStyles();
	document.body.innerHTML = '';
});

describe('the guests on Users', () => {
	it('start their names and end their three dots on the edges every other row stands on', async () => {
		mocks.get.mockResolvedValue([
			{ id: 'u1', username: 'kate', role: 'admin', disabled: false, created_at: 0 },
			{ id: 'u2', username: 'sam', role: 'guest', disabled: false, created_at: 1 }
		]);
		drawn = mount(Users, { target: document.body });
		flushSync();
		await tick();
		await tick();
		flushSync();

		/* The list's own option, not a rule of this pane's: every list on a pane stands the same. */
		const list = document.querySelector('ul[aria-label="Guests"]');
		const row = list?.querySelector('.line .row');
		expect(row, 'no guest row drawn').toBeTruthy();
		expect(list?.classList.contains('edges'), 'the guests are not on the pane edges').toBe(true);
		applyStyles(dataRow, row);
		applyStyles(dataRows, list);

		const guestList = list as Element;
		const guestRow = row as Element;
		/* Where the words start and the last control ends, against the list's own box. */
		expect(side(guestList, 'margin', 'start') + side(guestRow, 'padding', 'start')).toBe(0);
		expect(side(guestList, 'margin', 'end') + side(guestRow, 'padding', 'end')).toBe(0);
		/* And the ground under the pointer still reaches past the words, on both sides. */
		expect(side(guestRow, 'padding', 'start')).toBeGreaterThan(0);
		expect(side(guestRow, 'padding', 'end')).toBeGreaterThan(0);
	});
});

describe('every list of data rows on a settings pane', () => {
	/* Read from the panes' sources: a list mounted without the option sits 12 px inside every other
	   row on its pane, which nobody sees until two lists stand one above the other. The tag runs to
	   the first `>` that ends a line and closes no arrow function. */
	const PANES = resolve('src/lib/settings-ui');
	const mounts = readdirSync(PANES)
		.filter((name) => name.endsWith('.svelte'))
		.flatMap((name) =>
			[
				...readFileSync(resolve(PANES, name), 'utf8').matchAll(/<DataRows\b[\s\S]*?[^=]>\s*$/gm)
			].map((found) => ({ name, tag: found[0] }))
		);

	it('is found', () => {
		expect(mounts.length).toBeGreaterThanOrEqual(3);
	});

	it.each(mounts.map((one) => [one.name, one.tag]))('stands on the pane edges: %s', (_, tag) => {
		expect(tag).toMatch(/\sedges[\s>]/);
	});
});
