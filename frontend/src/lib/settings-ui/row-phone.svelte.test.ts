/*
 * A settings row at a phone's width: the control goes under the name and its help, the whole width
 * of the row. Beside the fixed control column the name would be squeezed to a word a line next to
 * a column that was mostly empty.
 *
 * The unit environment answers only a plain `screen` media rule, so the phone-width rule is read
 * twice: as written, where it must leave a desktop row alone, and with its condition swapped for
 * `screen`, which is the stylesheet a 390-wide window applies.
 */
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import LabelledRow from '$lib/components/common/LabelledRow.svelte';
import labelledSource from '$lib/components/common/LabelledRow.svelte?raw';
import ActionRow from './ActionRow.svelte';
import actionSource from './ActionRow.svelte?raw';

const PHONE = '@media (max-width: 767px)';

/** The stylesheet as a window under the phone width applies it. */
function atPhoneWidth(source: string): string {
	expect(source, 'the row has no phone-width rule').toContain(PHONE);
	return source.replace(PHONE, '@media screen');
}

let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	removeStyles();
	document.body.innerHTML = '';
});

function labelled(props: { wide?: boolean; looseColumn?: boolean } = {}): HTMLElement {
	drawn = mount(LabelledRow, {
		target: document.body,
		props: {
			label: 'Where downloads go',
			help: 'The folder a finished download is saved in.',
			children: createRawSnippet(() => ({ render: () => '<span>a control</span>' })),
			...props
		}
	});
	flushSync();
	return document.body.querySelector<HTMLElement>('.row')!;
}

function action(): HTMLElement {
	drawn = mount(ActionRow, {
		target: document.body,
		props: {
			label: 'Sign out',
			help: 'Signs you out on this browser.',
			action: 'Sign out',
			onclick: () => {}
		}
	});
	flushSync();
	return document.body.querySelector<HTMLElement>('.row')!;
}

describe('LabelledRow', () => {
	it('keeps the control column beside the name on a desktop window', () => {
		const row = labelled();
		applyStyles(labelledSource, row);
		expect(getComputedStyle(row).gridTemplateColumns).toContain('--settings-control-col');
	});

	for (const kind of ['plain', 'wide', 'loose'] as const) {
		it(`puts a ${kind} row's control under its name, the full width, under the phone width`, () => {
			const row = labelled({ wide: kind === 'wide', looseColumn: kind === 'loose' });
			applyStyles(atPhoneWidth(labelledSource), row);
			expect(getComputedStyle(row).gridTemplateColumns).toBe('minmax(0, 1fr)');
			const control = row.querySelector<HTMLElement>('.control')!;
			expect(getComputedStyle(control).justifyContent).toBe('flex-start');
		});
	}
});

describe('ActionRow', () => {
	it('puts its press under its name under the phone width', () => {
		const row = action();
		applyStyles(atPhoneWidth(actionSource), row);
		expect(getComputedStyle(row).gridTemplateColumns).toBe('minmax(0, 1fr)');
		expect(getComputedStyle(row.querySelector('.control')!).justifyContent).toBe('flex-start');
	});

	it('keeps its press in the control column on a desktop window', () => {
		const row = action();
		applyStyles(actionSource, row);
		expect(getComputedStyle(row).gridTemplateColumns).toContain('--settings-control-col');
	});
});
