// SPDX-License-Identifier: AGPL-3.0-or-later
/* A list whose order is stored can be put in order.
 *
 * A song's artists are credited in the order the recording names them, and that order is what the
 * song's save sends. A list marked `ordered` in the registry offers Move up and Move down on each
 * entry, refused at its ends; a list whose order means nothing (other names) offers neither.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { FieldDescription } from '$lib/entity/records.svelte';

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(async () => ({ items: [], total: 0 })),
		post: vi.fn(),
		put: vi.fn(),
		del: vi.fn()
	}
}));

import FieldEditor from './FieldEditor.svelte';

function field(ordered: boolean): FieldDescription {
	return {
		key: 'artists',
		subject: 'song',
		label: 'Artists',
		kind: 'names',
		shown: 'more',
		group: 'record',
		editable: true,
		links_to: null,
		help: null,
		imported: false,
		suggests: null,
		entry: 'an artist',
		...(ordered ? { ordered: true } : {})
	} as FieldDescription;
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(ordered: boolean, value: string[]) {
	const changed = vi.fn();
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FieldEditor, {
		target: host,
		props: { field: field(ordered), value, onchange: changed }
	}) as Record<string, unknown>;
	flushSync();
	return changed;
}

function press(label: string): HTMLButtonElement {
	const found = host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
	if (!found) throw new Error(`no button named ${label}`);
	return found;
}

it('moves an entry of an ordered list up and down, one place at a time', () => {
	const changed = draw(true, ['Odo Venn', 'Ilsa Moor', 'Wren Hale']);
	press('Move Ilsa Moor up').click();
	expect(changed).toHaveBeenLastCalledWith(['Ilsa Moor', 'Odo Venn', 'Wren Hale']);
	press('Move Ilsa Moor down').click();
	expect(changed).toHaveBeenLastCalledWith(['Odo Venn', 'Wren Hale', 'Ilsa Moor']);
});

it('refuses a move past either end', () => {
	draw(true, ['Odo Venn', 'Ilsa Moor']);
	expect(press('Move Odo Venn up').disabled).toBe(true);
	expect(press('Move Ilsa Moor down').disabled).toBe(true);
	expect(press('Move Odo Venn down').disabled).toBe(false);
});

it('offers no move on a list whose order is not stored', () => {
	draw(false, ['Odo Venn', 'Ilsa Moor']);
	expect(host.querySelector('button[aria-label^="Move "]')).toBeNull();
	expect(host.querySelector('button[aria-label="Remove Odo Venn"]')).not.toBeNull();
});
