/*
 * The sentence about the disk is always there, the count is in the question, and nothing goes until
 * a destination is chosen.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import MoveDialog from './MoveDialog.svelte';

/*
 * The picker stood in for: jsdom cannot lay out the listbox, so this writes the bound value
 * directly.
 */
const picker = vi.hoisted(() => ({ props: null as { value: string } | null }));

const stored = vi.hoisted(() => ({
	copies: [] as { location_id: string; path: string; reason: string | null }[]
}));
vi.mock('$lib/organize/copies', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	copiesOf: async () => stored.copies
}));

vi.mock('$lib/components/common', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	Select: (_anchor: unknown, props: { value: string }) => {
		picker.props = props;
	}
}));

/* After the sheet is on screen: it clears the choice on every open. */
function choose(folderId: string) {
	picker.props!.value = folderId;
	flushSync();
}

const FOLDERS = [
	{ id: 'f1', name: 'Stills', path: 'Pictures/Stills' },
	{ id: 'f2', name: 'Clips', path: 'Pictures/Clips' }
];

let host: HTMLElement;

beforeEach(() => {
	picker.props = null;
});

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

function render(count = 2, assetId?: string) {
	host = document.createElement('div');
	document.body.append(host);

	const onconfirm = vi.fn();
	const props = reactiveProps({ open: true, count, folders: FOLDERS, onconfirm, assetId });
	mount(MoveDialog, { target: host, props });
	flushSync();

	return {
		props,
		onconfirm,
		dialog: document.querySelector('[role="alertdialog"]') as HTMLElement,
		confirm: document.querySelector('.confirm') as HTMLButtonElement
	};
}

describe('what the sheet says', () => {
	it('states what a move does, in the dialog rather than somewhere else', () => {
		const { dialog } = render();

		expect(dialog.textContent).toContain('on your disk');
	});

	it('asks it as a question, with the count in it', () => {
		const { dialog } = render(30);

		expect(dialog.textContent).toContain('Move 30 files?');
	});

	it('offers no way to stop being asked', () => {
		const { dialog } = render();

		expect(dialog.querySelector('input[type="checkbox"]')).toBeNull();
		expect(dialog.textContent).not.toContain("Don't ask me again");
	});
});

describe('until a destination has been chosen', () => {
	it('refuses to go', () => {
		const { confirm } = render();

		expect(confirm.disabled).toBe(true);
	});

	it('starts over each time the sheet opens', () => {
		const { confirm, props } = render();
		choose('f2');
		expect(confirm.disabled).toBe(false);

		props.open = false;
		flushSync();
		props.open = true;
		flushSync();

		expect((document.querySelector('.confirm') as HTMLButtonElement).disabled).toBe(true);
	});
});

describe('once a destination has been chosen', () => {
	it('goes, to the folder chosen', () => {
		const { confirm, onconfirm } = render();
		choose('f2');

		expect(confirm.disabled).toBe(false);
		confirm.click();
		flushSync();

		expect(onconfirm).toHaveBeenCalledWith('f2', null);
	});
});

describe('one file stored in more than one place', () => {
	it('asks which copy, offering only those Sift may move, and hands back the one chosen', async () => {
		stored.copies = [
			{ location_id: 'l1', path: '/media/Clips/clip.mp4', reason: null },
			{ location_id: 'l2', path: '/media/Kept/clip.mp4', reason: null },
			{ location_id: 'l3', path: '/media/Read only/clip.mp4', reason: 'read-only' }
		];
		const { onconfirm, confirm } = render(1, 'asset-1');
		await vi.waitFor(() => {
			flushSync();
			expect(document.querySelectorAll('[role="radio"]')).toHaveLength(2);
		});
		(document.querySelectorAll('[role="radio"]')[1] as HTMLElement).click();
		flushSync();
		choose('f2');
		confirm.click();

		expect(onconfirm).toHaveBeenCalledWith('f2', 'l2');
	});

	it('names no copy for a file stored in one place', async () => {
		stored.copies = [{ location_id: 'l1', path: '/media/Clips/clip.mp4', reason: null }];
		const { onconfirm, confirm } = render(1, 'asset-1');
		await Promise.resolve();
		flushSync();
		expect(document.querySelector('[role="radio"]')).toBeNull();
		choose('f2');
		confirm.click();

		expect(onconfirm).toHaveBeenCalledWith('f2', null);
	});
});
