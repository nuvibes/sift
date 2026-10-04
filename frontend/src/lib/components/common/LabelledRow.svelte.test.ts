/* The row a settings pane is made of: where its foot line stands.
 *
 * A wide row's control column is 26rem, so its name's column is narrower than the reading measure
 * on most windows, and a stage line on Importing would wrap narrower than the lines beside it. The foot
 * of a wide row takes a line of the row's own under both columns; every other row keeps its foot
 * under the help. Read from the compiled rules as well as the markup.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import LabelledRow from './LabelledRow.svelte';
import source from './LabelledRow.svelte?raw';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	removeStyles();
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(wide: boolean, help = 'Finds new files.'): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(LabelledRow, {
		target: host,
		props: {
			label: 'When it runs',
			help,
			wide,
			foot: createRawSnippet(() => ({ render: () => '<p class="facts">Last ran 3 hours ago</p>' })),
			children: createRawSnippet(() => ({
				render: () => '<span class="picked">As files arrive</span>'
			}))
		}
	}) as Record<string, unknown>;
	flushSync();
	return host.querySelector('.row') as HTMLElement;
}

describe('the foot line', () => {
	it('stands under both columns of a wide row, at the reading measure', () => {
		const row = draw(true);
		applyStyles(source, row);
		const foot = row.querySelector(':scope > .foot') as HTMLElement;

		expect(foot?.textContent).toContain('Last ran 3 hours ago');
		expect(row.querySelector('.named .foot')).toBeNull();
		expect(getComputedStyle(foot).gridColumn).toBe('1 / -1');
		expect(getComputedStyle(foot).maxWidth).toBe('var(--reading-measure)');
	});

	it('stays under the help of any other row', () => {
		const row = draw(false);
		expect(row.querySelector('.named .foot')?.textContent).toContain('Last ran 3 hours ago');
		expect(row.querySelector(':scope > .foot')).toBeNull();
	});
});

describe('the name', () => {
	/* A wide row's foot is a line of the row's own, so a name with no help stands alone in its
	   column, and must not sit at the top of the control beside it, half a line above its middle. */
	it('stands level with its control when nothing is under it, foot or not', () => {
		const row = draw(true, '');
		applyStyles(source, row);
		expect(getComputedStyle(row).alignItems).toBe('center');
	});

	it('starts at the top of a row whose name carries help or a foot under it', () => {
		const helped = draw(true);
		applyStyles(source, helped);
		expect(getComputedStyle(helped).alignItems).toBe('start');
		removeStyles();
		unmount(drawn!);
		drawn = null;
		host.remove();

		const footed = draw(false, '');
		applyStyles(source, footed);
		expect(getComputedStyle(footed).alignItems).toBe('start');
	});

	it('gives a field beside its press what the press leaves, so the press stays on the line', () => {
		// The box in a column fills it (TextInput says so), so without this rule the press would wrap
		// under the box on the left, the one place a control never ends. Read as written: the
		// rule is scoped to the beside-field row and reaches only the box standing directly in
		// its control column.
		expect(source).toMatch(
			/\.row\.beside-field \.control > :global\(\.text-input\) \{[^}]*flex: 1 1 0;/
		);
		expect(source).toMatch(/class:beside-field=\{besideField\}/);
	});
});

/*
 * A group of choices (a picker's rows: how a record is drawn, which units) separates its rows by
 * space: no line between them. Anywhere else the lower of two rows draws the line between them.
 */
describe('the line between two rows', () => {
	const mounted: Record<string, unknown>[] = [];
	afterEach(() => {
		for (const one of mounted.splice(0)) unmount(one);
	});

	function twoRows(choices: boolean): HTMLElement[] {
		const holder = document.createElement('section');
		if (choices) holder.className = 'choices';
		document.body.append(holder);
		for (const label of ['Show every field on a record', 'Units']) {
			mounted.push(
				mount(LabelledRow, {
					target: holder,
					props: {
						label,
						children: createRawSnippet(() => ({ render: () => '<span>a choice</span>' }))
					}
				})
			);
		}
		flushSync();
		const rows = [...holder.querySelectorAll<HTMLElement>('.row')];
		applyStyles(source, rows[0]);
		return rows;
	}

	const lined = (row: HTMLElement) =>
		/\bsolid\b/.test(getComputedStyle(row).getPropertyValue('border-block-start'));

	it('is drawn by the lower row in an ordinary group', () => {
		const rows = twoRows(false);
		expect(lined(rows[1])).toBe(true);
	});

	it('is not drawn inside a group of choices', () => {
		const rows = twoRows(true);
		expect(lined(rows[1])).toBe(false);
	});
});
