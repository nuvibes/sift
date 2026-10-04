import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import ConfirmDialog from './ConfirmDialog.svelte';

/* The dialog that stands between someone and something they cannot undo.
 *
 * The thing being checked is that it says what will happen. A confirm that asks "Are you sure?"
 * teaches people that confirms are a key you press to continue, and the one that mattered gets the
 * same reflex as all the ones that did not.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

function render(overrides: Partial<Parameters<typeof ConfirmDialog>[1]> = {}) {
	host = document.createElement('div');
	document.body.append(host);

	const onconfirm = vi.fn();
	const props = reactiveProps({
		open: true,
		title: 'Delete 12 files from disk?',
		consequence: 'This removes them from the disk and cannot be undone.',
		confirmLabel: 'Delete',
		onconfirm,
		...overrides
	});

	mount(ConfirmDialog, { target: host, props });
	flushSync();

	// Portalled to the end of the document rather than rendered where it was written, so it is found
	// from the body and not from the host.
	return {
		props,
		onconfirm,
		dialog: document.querySelector('[role="alertdialog"]') as HTMLElement,
		confirm: document.querySelector('.confirm') as HTMLButtonElement,
		cancel: document.querySelector('.cancel') as HTMLElement
	};
}

describe('what it says', () => {
	it('names the consequence, not just the question', () => {
		const { dialog } = render();

		expect(dialog.textContent).toContain('cannot be undone');
	});

	it('names the action on the button rather than saying OK', () => {
		const { confirm } = render();

		expect(confirm.textContent?.trim()).toBe('Delete');
	});

	it('interrupts on purpose, and says so', () => {
		// alertdialog rather than dialog: this is not a panel that happens to be open, and a screen
		// reader should treat it as the thing that just took over.
		const { dialog } = render();

		expect(dialog).not.toBeNull();
	});
});

describe('confirming', () => {
	it('runs the action and closes', () => {
		const { confirm, onconfirm, props } = render();

		confirm.click();
		flushSync();

		expect(onconfirm).toHaveBeenCalledOnce();
		expect(props.open).toBe(false);
	});
});

describe('cancelling', () => {
	it('does not run the action', () => {
		// The whole point. Getting this wrong deletes someone's files because they said no.
		const { cancel, onconfirm } = render();

		cancel.click();
		flushSync();

		expect(onconfirm).not.toHaveBeenCalled();
	});

	it('closes', () => {
		const { cancel, props } = render();

		cancel.click();
		flushSync();

		expect(props.open).toBe(false);
	});
});

describe('while closed', () => {
	it('is not in the document at all', () => {
		render({ open: false });

		expect(document.querySelector('[role="alertdialog"]')).toBeNull();
	});
});

describe('a non-destructive confirm', () => {
	it('is not dressed as a destructive one', () => {
		// Red is failure and destruction. A confirm for something reversible must not borrow the
		// colour that means "this cannot be taken back".
		const { confirm } = render({ destructive: false, confirmLabel: 'Move' });

		expect(confirm.classList.contains('destructive')).toBe(false);
	});
});
