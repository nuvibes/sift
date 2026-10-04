import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Field from './Field.svelte';
import fieldSource from './Field.svelte?raw';

/* The wiring, which is the whole reason this is a component rather than three elements.
 *
 * A label that is not attached to its control is a word floating next to a box: clicking it does
 * nothing, and a screen reader reaches an input with no name. Help and errors have the same problem
 * one step further along: they are on screen, and unless the control points at them they are not
 * said to anyone who cannot see them. None of that is visible when you look at the page.
 */

const control = createRawSnippet<
	[{ id: string; describedBy: string | undefined; invalid: boolean }]
>((args) => ({
	render: () => {
		const { id, describedBy, invalid } = args();
		const described = describedBy ? ` aria-describedby="${describedBy}"` : '';
		return `<input id="${id}"${described} aria-invalid="${invalid}" />`;
	}
}));

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: { label: string; help?: string; error?: string }) {
	host = document.createElement('div');
	document.body.append(host);

	const all = reactiveProps({ ...props, control });
	mount(Field, { target: host, props: all });
	flushSync();

	return {
		props: all,
		label: host.querySelector('label') as HTMLLabelElement,
		input: host.querySelector('input') as HTMLInputElement,
		help: host.querySelector('.help'),
		error: host.querySelector('.error')
	};
}

describe('the label', () => {
	it('is attached to the control', () => {
		const { label, input } = render({ label: 'Library folder' });

		expect(label.htmlFor).toBe(input.id);
		expect(input.id).not.toBe('');
	});

	it('gives every field its own id, so two on a page do not collide', () => {
		// Two fields with the same id means clicking the second label focuses the first field. A
		// hardcoded id looks fine until a screen has two of anything.
		const first = render({ label: 'One' });
		const firstId = first.input.id;
		first.input.closest('div')?.remove();

		const second = render({ label: 'Two' });

		expect(second.input.id).not.toBe(firstId);
	});
});

describe('help', () => {
	it('is on the page, not in a tooltip', () => {
		const { help } = render({
			label: 'Folder',
			help: 'Sift reads this folder and never writes to it'
		});

		expect(help?.textContent).toBe('Sift reads this folder and never writes to it');
	});

	it('is pointed at by the control', () => {
		const { input, help } = render({ label: 'Folder', help: 'Anything here is left where it is' });

		expect(input.getAttribute('aria-describedby')).toBe(help?.id);
	});
});

describe('an error', () => {
	it('marks the control invalid', () => {
		const { input } = render({ label: 'Folder', error: 'Pick a folder Sift can read' });

		expect(input.getAttribute('aria-invalid')).toBe('true');
	});

	it('is announced rather than only shown', () => {
		const { error } = render({ label: 'Folder', error: 'Pick a folder Sift can read' });

		expect(error?.getAttribute('role')).toBe('alert');
	});

	it('does not take the help away with it', () => {
		// Both are said: the error is what to do now, the help is still what the field is for.
		const { input, help, error } = render({
			label: 'Folder',
			help: 'Where Sift looks for media',
			error: 'Pick a folder Sift can read'
		});

		const described = input.getAttribute('aria-describedby');
		expect(described).toContain(help?.id);
		expect(described).toContain(error?.id);
	});

	it('leaves the control valid when there is no error', () => {
		const { input } = render({ label: 'Folder' });

		expect(input.getAttribute('aria-invalid')).toBe('false');
	});
});

describe('the width a control takes', () => {
	const chooser = createRawSnippet<[{ id: string }]>((args) => ({
		render: () => `<button id="${args().id}" class="ui-select">Add the next number</button>`
	}));

	afterEach(() => removeStyles());

	it('fills the column with a box, and leaves a select as wide as its answers', () => {
		const { input } = render({ label: 'New names' });
		const typed = input.closest('.control');
		applyStyles(fieldSource, typed);
		expect(getComputedStyle(input).getPropertyValue('inline-size')).toBe('100%');

		const second = document.createElement('div');
		document.body.append(second);
		mount(Field, { target: second, props: { label: 'When a name is taken', control: chooser } });
		flushSync();
		const select = second.querySelector('.ui-select') as HTMLElement;
		expect(getComputedStyle(select).getPropertyValue('inline-size')).toBe('fit-content');
		second.remove();
	});
});
