/*
 * Creating a PIN where somebody pressed, rather than sending them off to find the form.
 *
 * A PIN is six digits, so the dialog does not offer to save anything shorter, and it closes only
 * once the PIN is really saved.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount, unmount } from 'svelte';

import CreatePin from './CreatePin.svelte';
import { vault, vaultPrompt } from '$lib/shell/vault.svelte';

let host: HTMLElement | null = null;
let component: Record<string, unknown> | null = null;

afterEach(() => {
	if (component) unmount(component);
	component = null;
	host?.remove();
	host = null;
	vaultPrompt.creating = null;
	vi.restoreAllMocks();
});

function draw(why: 'first' | 'longer'): void {
	vaultPrompt.creating = why;
	host = document.createElement('div');
	document.body.append(host);
	component = mount(CreatePin, { target: host }) as Record<string, unknown>;
	flushSync();
}

function fill(selector: string, value: string): void {
	const input = document.querySelector(selector) as HTMLInputElement | null;
	if (!input) throw new Error(`nothing matches ${selector}`);
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function save(): HTMLButtonElement {
	const button = [...document.querySelectorAll<HTMLButtonElement>('form button')].find(
		(one) => wordsOn(one) === 'Save PIN'
	);
	if (!button) throw new Error('there is no Save PIN button');
	return button;
}

describe('creating a PIN', () => {
	it('asks for one with a dialog of its own', () => {
		draw('first');

		expect(document.body.textContent).toContain('Create a PIN');
		expect(document.querySelector('.pin-box input')).not.toBeNull();
	});

	it('offers to save only a whole six-digit PIN with the password', () => {
		draw('first');

		fill('input[type="password"]', 'the password');
		fill('.pin-box input', '2468');
		expect(save().disabled, 'four digits were offered to the server').toBe(true);

		fill('.pin-box input', '246810');
		expect(save().disabled).toBe(false);
	});

	it('saves through the vault and closes once the PIN is saved', async () => {
		const setPin = vi.spyOn(vault, 'setPin').mockResolvedValue(null);
		draw('first');
		fill('input[type="password"]', 'the password');
		fill('.pin-box input', '246810');

		document
			.querySelector('form')
			?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(setPin).toHaveBeenCalledWith('246810', 'the password');
		expect(vaultPrompt.creating).toBeNull();
	});

	it('stays open, saying so, when the password is wrong', async () => {
		vi.spyOn(vault, 'setPin').mockResolvedValue('wrong-password');
		draw('first');
		fill('input[type="password"]', 'wrong');
		fill('.pin-box input', '246810');

		document
			.querySelector('form')
			?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(vaultPrompt.creating).toBe('first');
		expect(document.querySelector('[role="alert"]')?.textContent).toContain('password');
	});

	it('can be put off by somebody whose shorter PIN still works', () => {
		draw('longer');

		expect(document.body.textContent).toContain('Choose a six-digit PIN');
		const later = [...document.querySelectorAll('form button')].find(
			(one) => one.textContent?.trim() === 'Not now'
		);
		expect(later).toBeDefined();
	});
});
