/*
 * The answers behind a question's chevron come in their parts, a line between each two: the
 * answers decided here, then the ones that go somewhere to look first, and last, alone, any answer
 * that destroys something. Drawn for real, with the group's own stylesheet in the document, so
 * what is asserted is the lines a person would see.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Answers, { type Answer } from './Answers.svelte';
import groupSource from '../common/ContextMenuGroup.svelte?raw';
import answersSource from './Answers.svelte?raw';

for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	removeStyles();
	document.body.innerHTML = '';
});

const run = () => {};

const words = (row: Element) => (row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();

async function open(rest: Answer[]) {
	const host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(Answers, {
		target: host,
		props: { yes: { label: 'Yes', icon: 'check', run }, rest, about: 'this group' }
	});
	flushSync();
	applyStyles(groupSource);
	host.querySelector<HTMLButtonElement>('button[aria-label="More for this group"]')?.click();
	await vi.waitFor(() => expect(document.querySelector('[role="menuitem"]')).not.toBeNull(), {
		timeout: 5000
	});
}

/** Each drawn part's rows, in order. */
const parts = () =>
	[...document.querySelectorAll('[role="menu"] .menu-group')].map((group) =>
		[...group.querySelectorAll('[role="menuitem"]')].map(words)
	);

/** The lines a person sees: separators the stylesheet has not taken away. */
const lines = () =>
	[...document.querySelectorAll('[role="menu"] .menu-separator')].filter(
		(line) => getComputedStyle(line).display !== 'none'
	);

describe('the answers behind the chevron', () => {
	it('draws the answer that destroys last, alone, below its own line', async () => {
		await open([
			{ label: 'Delete', icon: 'delete', destructive: true, run },
			{ label: 'Discard', icon: 'remove', run }
		]);

		expect(parts()).toEqual([['Discard'], ['Delete']]);
		expect(lines()).toHaveLength(1);
	});

	it('puts the answers that go somewhere in a part of their own, after the ones decided here', async () => {
		await open([
			{ label: 'No', icon: 'close', run },
			{ label: 'Show me', icon: 'arrow_forward', run },
			{ label: 'Delete', icon: 'delete', destructive: true, run },
			{ label: 'Not now', run }
		]);

		expect(parts()).toEqual([['No', 'Not now'], ['Show me'], ['Delete']]);
		expect(lines()).toHaveLength(2);
	});

	it('draws answers of one kind as one part with no line', async () => {
		await open([
			{ label: 'No', icon: 'close', run },
			{ label: 'Not now', run }
		]);

		expect(parts()).toEqual([['No', 'Not now']]);
		expect(lines()).toHaveLength(0);
	});
});

describe('the answers alone', () => {
	it('draw their one control and nothing laid unseen beside it to set a width', () => {
		/* Nothing hands out a width: the board is cards, and each card's answers start their own
		   line. */
		const host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(Answers, {
			target: host,
			props: { yes: { label: 'Yes', icon: 'check', run }, about: 'this group' }
		});
		flushSync();

		const answers = host.querySelector('.answers');
		expect(answers?.children.length).toBe(1);
		expect(host.querySelector('[inert]')).toBeNull();
		expect(answersSource).not.toMatch(/\bwidths\b|AnswerWidth|class:matched/);
	});
});
