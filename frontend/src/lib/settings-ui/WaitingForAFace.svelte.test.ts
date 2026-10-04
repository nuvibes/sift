/*
 * Settings > Faces, Waiting for a face: the people a fingerprints file or a folder brought whom no
 * face matches yet, and what the list says when reading it, creating one or removing one fails.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const held = vi.hoisted(() => ({
	waiting: vi.fn(),
	make: vi.fn(),
	remove: vi.fn(),
	goto: vi.fn(),
	show: vi.fn()
}));

vi.mock('$lib/people/fingerprint-offers', () => ({
	waitingFingerprints: held.waiting,
	makePersonFromFingerprints: held.make,
	removeWaitingFingerprints: held.remove
}));
vi.mock('$app/navigation', () => ({ goto: held.goto }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: held.show } }));

import WaitingForAFace from './WaitingForAFace.svelte';
import { ApiError } from '$lib/api/client';

const WREN = {
	entry_id: 'e1',
	name: 'Wren Halloway',
	faces: 4,
	confirmed: null,
	source: 'Studio Faces',
	added_at: Date.now()
};

let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	held.waiting.mockResolvedValue({ items: [WREN] });
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

async function settle(): Promise<void> {
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();
}

async function draw(onchanged = vi.fn()): Promise<HTMLElement> {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(WaitingForAFace, {
		target: host,
		props: { enabled: true, read: 0, onchanged }
	}) as Record<string, unknown>;
	await settle();
	return host;
}

function pressNamed(root: ParentNode, words: string): HTMLButtonElement {
	const button = [...root.querySelectorAll<HTMLButtonElement>('button')].find(
		(one) => one.textContent?.trim() === words
	);
	if (!button) throw new Error(`no ${words} press`);
	return button;
}

it('says no one is waiting when the list cannot be read, rather than failing the pane', async () => {
	held.waiting.mockRejectedValue(new Error('offline'));
	const host = await draw();

	expect(host.textContent).toContain('No one is waiting.');
});

it("creates the person, says so, and the toast's Open goes to their page", async () => {
	held.make.mockResolvedValue('p9');
	const onchanged = vi.fn();
	const host = await draw(onchanged);

	pressNamed(host, 'Create a person').click();
	await settle();

	expect(held.make).toHaveBeenCalledWith('e1');
	expect(onchanged).toHaveBeenCalled();
	const [said, options] = held.show.mock.calls[0];
	expect(said).toBe('Created Wren Halloway.');
	options.action.run();
	expect(held.goto).toHaveBeenCalledWith('/people/p9');
});

it("says a refused create in the server's words", async () => {
	held.make.mockRejectedValue(new ApiError(409, 'refused', 'Turn on face recognition first.'));
	const host = await draw();

	pressNamed(host, 'Create a person').click();
	await settle();

	expect(held.show).toHaveBeenCalledWith('Turn on face recognition first.', { tone: 'error' });
});

it('removes them after the confirm and reads the list again', async () => {
	held.remove.mockResolvedValue(undefined);
	const host = await draw();
	expect(host.textContent).toContain('From Studio Faces');
	held.waiting.mockResolvedValue({ items: [] });

	pressNamed(host, 'Remove').click();
	await settle();
	pressNamed(
		document.body.querySelector('[role="alertdialog"], [role="dialog"]')!,
		'Remove'
	).click();
	await settle();

	expect(held.remove).toHaveBeenCalledWith('e1');
	expect(host.textContent).toContain('No one is waiting.');
});

it('says a failed remove, after the confirm', async () => {
	held.remove.mockRejectedValue(new Error('offline'));
	const host = await draw();

	pressNamed(host, 'Remove').click();
	await settle();
	const dialog = document.body.querySelector('[role="alertdialog"], [role="dialog"]');
	if (!dialog) throw new Error('the confirm did not open');
	pressNamed(dialog, 'Remove').click();
	await settle();

	expect(held.remove).toHaveBeenCalledWith('e1');
	expect(held.show).toHaveBeenCalledWith("Couldn't remove them.", { tone: 'error' });
});

it('narrows the list as the box is typed into, by any part of the name, and says how many', async () => {
	held.waiting.mockResolvedValue({
		items: [WREN, { ...WREN, entry_id: 'e2', name: 'Orla Tennant' }]
	});
	const host = await draw();
	expect(host.textContent).toContain('2 people.');
	const box = host.querySelector<HTMLInputElement>('input[type="search"]')!;
	expect(box.placeholder).toBe('Type to filter');

	box.value = 'TENN';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();

	expect(host.textContent).toContain('Orla Tennant');
	expect(host.textContent).not.toContain('Wren Halloway');
	expect(host.textContent).toContain('1 person.');

	box.value = 'Quill';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();
	expect(host.textContent).toContain('No one matches "Quill".');
});

it('draws the list in a scroll of its own, Remove first and Create a person last', async () => {
	const host = await draw();

	expect(host.querySelector('.held-box .scroll-root .held')).not.toBeNull();
	const presses = [...host.querySelectorAll('.held li .presses button')].map((one) =>
		one.textContent?.trim()
	);
	expect(presses).toEqual(['Remove', 'Create a person']);
});
