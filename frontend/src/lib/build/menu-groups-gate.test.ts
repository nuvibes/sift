/** What the menu-groups gate refuses, driven with lines written for the purpose.
 *
 * `scripts/check_menu_groups.js` holds every menu to its groups: the line between two parts is
 * drawn by `ContextMenuGroup` and by nothing else, and a menu of two kinds of row, however short,
 * says which part each row is in.
 */

import { describe, expect, it } from 'vitest';

import { menuFaultsIn } from '../../../scripts/lib/menu-groups.js';

const faults = (code: string, primitive = false) =>
	menuFaultsIn(code, { primitive }).map((one: { what: string }) => one.what);

const rows = (count: number) =>
	Array.from(
		{ length: count },
		(_, at) => `<ContextMenuItem label="Row ${at}" onselect={go} />`
	).join('\n');

const pushes = (count: number, group = true) =>
	[
		'const verbs: Verb[] = [];',
		...Array.from(
			{ length: count },
			(_, at) =>
				`verbs.push({ id: 'v${at}', label: 'V${at}', icon: 'add', ${group ? "group: 'change', " : ''}run });`
		),
		"verbs.push({ id: 'delete', label: 'Delete', icon: 'delete', destructive: true, run });"
	].join('\n');

describe('the menu-groups gate', () => {
	it('refuses a line placed by hand', () => {
		expect(faults('<ContextMenuItem label="A" onselect={go} />\n<ContextMenuSeparator />')).toEqual(
			[expect.stringContaining('a line placed by hand')]
		);
	});

	it('lets the primitives draw the line', () => {
		expect(faults('<ContextMenuSeparator />', true)).toEqual([]);
	});

	it('refuses a long menu written as markup with no group', () => {
		expect(faults(rows(6))).toEqual([
			expect.stringContaining('6 menu rows and no ContextMenuGroup')
		]);
	});

	it('lets a long menu through once it is grouped, and a short one without', () => {
		expect(faults(`<ContextMenuGroup>\n${rows(6)}\n</ContextMenuGroup>`)).toEqual([]);
		expect(faults(rows(5))).toEqual([]);
	});

	it('refuses a long declared list whose verbs name no group', () => {
		expect(faults(pushes(6, false))).toEqual([
			expect.stringContaining('7 verbs in `verbs`, 6 with no group')
		]);
	});

	it('refuses a long literal list with one verb left out of the groups', () => {
		const list = [
			'const rows: Verb[] = [',
			...Array.from({ length: 5 }, (_, at) => `{ id: 'r${at}', label: 'R', group: 'keep', run },`),
			"{ id: 'loose', label: 'Loose', run }",
			'];'
		].join('\n');
		expect(faults(list)).toEqual([expect.stringContaining('6 verbs in `rows`, 1 with no group')]);
	});

	it('lets a grouped list through, the destructive verb and one handed over by name included', () => {
		expect(
			faults(`${pushes(6)}\nverbs.push(running);\nverbs.push(favoriting(subject, run));`)
		).toEqual([]);
	});

	it('refuses a short declared list with a verb that names no group', () => {
		const list = [
			'const rows: Verb[] = [',
			"{ id: 'rename', label: 'Rename', group: 'change', run },",
			"{ id: 'loose', label: 'Loose', run }",
			'];'
		].join('\n');
		expect(faults(list)).toEqual([expect.stringContaining('2 verbs in `rows`, 1 with no group')]);
		expect(faults(pushes(1, false))).toEqual([
			expect.stringContaining('2 verbs in `verbs`, 1 with no group')
		]);
	});

	it('lets a short declared list through once every verb names its part, and one verb alone', () => {
		expect(faults(pushes(1))).toEqual([]);
		expect(faults("const rows: Verb[] = [{ id: 'one', label: 'One', run }];")).toEqual([]);
	});

	it('refuses a short menu whose row that destroys sits beside the others with no group', () => {
		const menu = [
			'<ContextMenuItem label="Discard" icon="remove" onselect={go} />',
			'<ContextMenuItem label="Delete" icon="delete" destructive onselect={() => gone()} />'
		].join('\n');
		expect(faults(menu)).toEqual([
			expect.stringContaining('a row that destroys beside the others')
		]);
		expect(faults(`<ContextMenuGroup>\n${menu}\n</ContextMenuGroup>`)).toEqual([]);
	});

	it('refuses a row whose destroying is decided as it is drawn, with no group', () => {
		const menu =
			'{#each rest as one}<ContextMenuItem label={one.label} destructive={one.destructive} onselect={one.run} />{/each}';
		expect(faults(menu)).toEqual([expect.stringContaining('a row that may destroy')]);
		expect(faults(`<ContextMenuGroup>${menu}</ContextMenuGroup>`)).toEqual([]);
	});

	it('refuses a short menu with a setting beside a row that acts, with no group', () => {
		const menu = [
			'<ContextMenuItem label="Compact rows" checked={compact} onselect={flip} />',
			'<ContextMenuItem label="Retry" icon="replay" onselect={go} />'
		].join('\n');
		expect(faults(menu)).toEqual([expect.stringContaining('a setting beside a row that acts')]);
		expect(faults(`<ContextMenuGroup>\n${menu}\n</ContextMenuGroup>`)).toEqual([]);
		expect(
			faults('<RatingChoices {value} />\n<ContextMenuItem label="Clear" onselect={go} />')
		).toEqual([expect.stringContaining('a setting beside a row that acts')]);
	});

	it('leaves a short menu of one kind alone, and a row that is plainly not destructive', () => {
		expect(faults(rows(3))).toEqual([]);
		expect(
			faults(
				'<ContextMenuItem label="A" destructive={false} onselect={go} />\n<ContextMenuItem label="Delete" destructive onselect={go} />'
			)
		).toEqual([expect.stringContaining('a row that destroys beside the others')]);
		expect(faults('<ContextMenuItem label="Delete" destructive={true} onselect={go} />')).toEqual(
			[]
		);
	});
});
