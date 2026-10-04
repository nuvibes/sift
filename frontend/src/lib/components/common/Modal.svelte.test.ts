import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import Modal from './Modal.svelte';

/*
 * The one sheet every dialog in the app is built out of.
 *
 * Checked here: the heading and the sentence arrive on the sheet, a sheet's own class reaches the
 * element its stylesheet is keyed on, and the two flavours announce themselves differently. The
 * last is why this is a test: a plain and an insistent sheet look identical and differ only to
 * assistive technology.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

/** A snippet drawing one line, for the case where the sentence carries markup. */
const written = createRawSnippet(() => ({
	render: () => '<span>Everything inside it goes too.</span>'
}));

const body = createRawSnippet(() => ({ render: () => '<p class="inside">contents</p>' }));

function render(overrides: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);

	const props = reactiveProps({
		open: true,
		title: 'Add a folder',
		description: 'What is on the disk, inside the folders your library covers.',
		children: body,
		onback: undefined as (() => void) | undefined,
		...overrides
	});

	mount(Modal, { target: host, props: props as never });
	flushSync();

	// Portalled to the end of the document rather than drawn where it was written, so everything is
	// found from the body and not from the host.
	return {
		props,
		sheet: document.querySelector('.sheet') as HTMLElement,
		title: document.querySelector('.title') as HTMLElement,
		description: document.querySelector('.consequence') as HTMLElement
	};
}

describe('what the sheet carries', () => {
	it('draws the heading it was given', () => {
		const { title } = render();

		expect(title.textContent?.trim()).toBe('Add a folder');
	});

	it('draws the sentence under it', () => {
		const { description } = render();

		expect(description.textContent).toContain('inside the folders your library covers');
	});

	it('takes the sentence as markup where a caller writes one', () => {
		const { description } = render({ description: written });

		expect(description.textContent).toContain('Everything inside it goes too');
	});

	it('leaves the sentence out entirely when there is none', () => {
		render({ description: undefined });

		// Absent, not empty. An empty paragraph still takes the space its margin asks for, and a
		// sheet with a gap where a sentence is not is a sheet that looks like it failed to load one.
		expect(document.querySelector('.consequence')).toBeNull();
	});

	it('draws what it was handed', () => {
		render();

		expect(document.querySelector('.inside')?.textContent).toBe('contents');
	});
});

describe('the classes a sheet is dressed by', () => {
	/* These matter more than they look. A sheet's own stylesheet is keyed on the extra class, and
	   the element it lands on is drawn HERE rather than in the file holding those rules, so a
	   class that failed to arrive would leave that sheet wearing only the shared chrome, at the
	   shared width, with nothing anywhere to say so. */
	it('puts the sheet class the caller asked for beside the shared one', () => {
		const { sheet } = render({ sheetClass: 'pick-sheet' });

		expect(sheet.classList.contains('sheet')).toBe(true);
		expect(sheet.classList.contains('pick-sheet')).toBe(true);
	});

	it('puts the sentence class the caller asked for beside the shared one', () => {
		const { description } = render({ descriptionClass: 'subject' });

		expect(description.classList.contains('consequence')).toBe(true);
		expect(description.classList.contains('subject')).toBe(true);
	});
});

describe('the two flavours', () => {
	it('is an ordinary dialog by default', () => {
		render();

		expect(document.querySelector('[role="dialog"]')).not.toBeNull();
		expect(document.querySelector('[role="alertdialog"]')).toBeNull();
	});

	it('says it interrupted on purpose when it is insistent', () => {
		render({ insistent: true });

		expect(document.querySelector('[role="alertdialog"]')).not.toBeNull();
	});
});

describe('Escape', () => {
	/* A real keypress, at the document, where the library listens for it. Cancelable because a
	   browser's is, and preventing it is how a sheet says it answered the key itself. */
	function escape() {
		document.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
		);
		flushSync();
	}

	it('closes a sheet that has no step to go back to', () => {
		const { props } = render();

		escape();

		expect(props.open).toBe(false);
	});

	it('goes back a step instead, and stays open, when the sheet has one', () => {
		let backs = 0;
		const { props } = render({ onback: () => (backs += 1) });

		escape();

		expect(backs).toBe(1);
		expect(props.open).toBe(true);
	});

	it('closes again once the sheet takes its way back away', () => {
		let backs = 0;
		const { props } = render({ onback: () => (backs += 1) });

		props.onback = undefined;
		flushSync();
		escape();

		expect(backs).toBe(0);
		expect(props.open).toBe(false);
	});
});
