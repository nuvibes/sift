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

function render(count = 2) {
	host = document.createElement('div');
	document.body.append(host);

	const onconfirm = vi.fn();
	const props = reactiveProps({ open: true, count, folders: FOLDERS, onconfirm });
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

		expect(onconfirm).toHaveBeenCalledWith('f2');
	});
});
