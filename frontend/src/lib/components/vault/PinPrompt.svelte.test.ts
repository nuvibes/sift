/*
 * A refused PIN leaves the box open.
 *
 * A prompt that disappears on a wrong PIN reads as the app having taken it and then shown nothing.
 * The message and the shake are worth nothing if the thing carrying them is gone before either can
 * be read.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import PinPrompt from './PinPrompt.svelte';
import { vault } from '$lib/shell/vault.svelte';

let host: HTMLElement | null = null;
let component: Record<string, unknown> | null = null;

afterEach(() => {
	if (component) unmount(component);
	component = null;
	host?.remove();
	host = null;
	vi.restoreAllMocks();
});

function open(reason?: string): void {
	host = document.createElement('div');
	document.body.append(host);
	component = mount(PinPrompt, { target: host, props: { open: true, reason } }) as Record<
		string,
		unknown
	>;
	flushSync();
}

/* `.pin-box input` rather than `input.pin`: the field is the shared `PinBox`, whose digits are
   cells over one hidden input. `.pin-box` is that component's own root class; the input inside it is
   the one everything here types into. */
function pinBox(): HTMLInputElement {
	const box = document.querySelector('.pin-box input') as HTMLInputElement | null;
	if (!box) throw new Error('the prompt is not on screen');
	return box;
}

async function type(value: string): Promise<void> {
	const box = pinBox();
	box.value = value;
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

async function press(): Promise<void> {
	const form = document.querySelector('form') as HTMLFormElement;
	form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
	// Two turns: the unlock resolves, then the state it set is rendered.
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

describe('the PIN prompt', () => {
	it('stays on screen and says so when the PIN is refused', async () => {
		vi.spyOn(vault, 'unlock').mockResolvedValue('wrong-pin');
		open();

		await type('0000');
		await press();

		expect(document.querySelector('.error')?.textContent).toContain('Incorrect PIN.');
		expect(
			document.querySelector('.pin-box input'),
			'the prompt closed on a refusal'
		).not.toBeNull();
	});

	it('says so when the tries run out, and still stays', async () => {
		vi.spyOn(vault, 'unlock').mockResolvedValue('too-many-attempts');
		open();

		await type('0000');
		await press();

		expect(document.querySelector('.error')?.textContent).toContain('Too many');
		expect(document.querySelector('.pin-box input')).not.toBeNull();
	});

	it('does not let Enter press Cancel', async () => {
		/* The refusal above is only worth anything if the box is still there to carry it, by the
		 * keyboard too. A button in a form with no type is a SUBMIT button, and implicit
		 * submission (Enter in the field) activates the first one in the form, which is Cancel.
		 * So Enter would dismiss the box and send the request anyway: a lone 401 in the console and
		 * nothing on screen. Pressing Show would be fine, which would make it look like the app
		 * eating a wrong PIN rather than the keyboard taking a different path from the mouse.
		 *
		 * Asserted on the type rather than by pressing Enter, because jsdom does not implement
		 * implicit submission: a test that pressed Enter would pass on the broken version. */
		open();

		const buttons = [...document.querySelectorAll('form button')];
		const cancel = buttons.find((one) => one.textContent?.trim() === 'Cancel');
		expect(cancel, 'the Cancel button is not inside the form any more').toBeDefined();
		expect((cancel as HTMLButtonElement).type).toBe('button');
	});

	it('closes only when the vault actually opens', async () => {
		vi.spyOn(vault, 'unlock').mockResolvedValue(null);
		open();

		await type('2468');
		await press();

		expect(document.querySelector('.pin-box input')).toBeNull();
	});
});

/* Asked so an Undo can put a file back, the prompt says what the PIN is for: "Show hidden items"
   would read wrong over a press that meant "put it back". Its own reason otherwise. */
describe('what the prompt says it is for', () => {
	it('says its own reason when nothing is waiting on it', () => {
		open();
		expect(document.body.textContent).toContain('Show hidden items');
	});

	it("says the waiting act's words when one raised it", () => {
		open('Put it back');
		expect(document.body.textContent).toContain('Put it back');
		expect(document.body.textContent).not.toContain('Show hidden items');
	});
});
