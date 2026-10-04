/*
 * Where to move files to, and the one sentence about it somebody can be wrong about.
 *
 * "It moves the file on your disk" is otherwise discovered later by an application that can no
 * longer find its files, so the sheet says it under the question every time, and asks every time,
 * with no way to turn the asking off.
 *
 * What is proved: the sentence is there, the count is in the question, and nothing goes until a
 * destination has been chosen, since a destination filled in for somebody is one they did not pick.
 *
 * Rendered rather than read from the source: matching the `confirmDisabled` line out of the
 * component would be a second copy of it, not a check.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import MoveDialog from './MoveDialog.svelte';

/*
 * The destination picker, stood in for, because without a destination this file can never reach
 * an enabled Move button.
 *
 * The real one is a listbox that positions itself against the trigger, which needs a layout jsdom
 * does not have: the trigger can be clicked and no options ever appear. So it is replaced by the
 * smallest thing that has the same effect on this component: something that writes a value back
 * through the same binding. What it looks like is not this component's business and is tested where
 * that component lives.
 *
 * A component in Svelte is a function of an anchor and its props, and `bind:value` hands over a
 * property with a setter, so writing to it is exactly what choosing an option does.
 */
const picker = vi.hoisted(() => ({ props: null as { value: string } | null }));

vi.mock('$lib/components/common', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	Select: (_anchor: unknown, props: { value: string }) => {
		picker.props = props;
	}
}));

/* Choose a destination, as somebody does: after the sheet is on screen, not while it is being
   drawn. The sheet clears the choice every time it opens, so a value written during the render is
   wiped a moment later by the component doing exactly what it should. */
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

	// Portalled to the end of the document rather than rendered where it was written.
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
		/* No box that turns the question off may come back. */
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
		/* Carried over, a destination from the last move would be one this selection did not pick. */
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
