import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';

/* The save dialog: what it says it keeps is the wall on screen, and Save sends exactly that. */

const mocks = vi.hoisted(() => ({
	get: vi.fn(async () => ({ items: [] })),
	post: vi.fn(async (_path: string, options?: { body?: { name?: string } }) => ({
		id: 'w9',
		name: options?.body?.name
	})),
	patch: vi.fn(async () => ({ id: 'w1', name: 'late show' }))
}));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post, patch: mocks.patch, del: vi.fn() }
}));

import SaveLayout from './SaveLayout.svelte';
import { presets, type Preset } from '$lib/theater/presets.svelte';
import { showing } from '$lib/theater/wall.svelte';

let running: Record<string, unknown> | undefined;

afterEach(() => {
	if (running) void unmount(running);
	running = undefined;
	presets.asking = null;
	showing.wall = null;
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

function draw() {
	const wall = showing.ensure();
	const host = document.createElement('div');
	document.body.append(host);
	running = mount(SaveLayout, { target: host });
	flushSync();
	return wall;
}

function type(name: string) {
	const field = document.querySelector<HTMLInputElement>('.sheet input');
	field!.value = name;
	field!.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

/* The wall also posts its session, so only the Saved Layouts route is read. */
function kept(mock: { mock: { calls: unknown[][] } }) {
	return mock.mock.calls.filter((call) => String(call[0]).startsWith('/theater/arrangements'));
}

function submit() {
	document.querySelector<HTMLFormElement>('#save-layout')!.requestSubmit();
}

it('stays shut until it is asked', () => {
	draw();

	expect(document.querySelector('.sheet')).toBeNull();
});

it('shows what the wall on screen keeps, and what it does not', () => {
	draw();
	presets.ask();
	flushSync();

	const sheet = document.querySelector('.sheet')!;
	expect(sheet.querySelector('.title')?.textContent).toBe('Add to Saved Layouts');
	expect(sheet.textContent).toContain('1x3');
	expect(sheet.querySelectorAll('.behaviour')).toHaveLength(3);
	expect(sheet.querySelector('.behaviour')?.textContent).toContain('Video and GIF');
	expect(sheet.textContent).toContain('Not kept:');
});

it('says Everything once for a cell drawing everything of every kind', () => {
	const wall = draw();
	wall.all[0].mediaKind = 'all';
	presets.ask();
	flushSync();

	const line = document.querySelector('.sheet .behaviour')?.textContent ?? '';
	expect(line.startsWith('Random'), `the kind repeated the source: ${line}`).toBe(true);
	expect(document.querySelectorAll('.sheet .behaviour')[1]?.textContent).toContain('Video and GIF');
});

it('saves the wall on screen, strip and all, under the name typed', async () => {
	const wall = draw();
	presets.ask();
	flushSync();

	type('Evening wall');
	submit();

	await vi.waitFor(() => expect(kept(mocks.post)).toHaveLength(1));
	expect(kept(mocks.post)[0]).toEqual([
		'/theater/arrangements',
		{ body: { name: 'Evening wall', ...wall.preset } }
	]);
	await vi.waitFor(() => expect(presets.asking).toBeNull());
});

it('updates the one it was opened over, starting from its name', async () => {
	const wall = draw();
	presets.ask({ id: 'w1', name: 'late show' } as Preset);
	flushSync();

	expect(document.querySelector('.sheet .title')?.textContent).toBe('Update late show');
	expect(document.querySelector<HTMLInputElement>('.sheet input')?.value).toBe('late show');

	submit();

	await vi.waitFor(() => expect(kept(mocks.patch)).toHaveLength(1));
	expect(kept(mocks.patch)[0]).toEqual([
		'/theater/arrangements/w1',
		{ body: { name: 'late show', ...wall.preset } }
	]);
	expect(kept(mocks.post)).toHaveLength(0);
});

it('is drawn inside the filled box while the screen is filled', () => {
	// A dialog at the end of the document is not painted while the browser fills the screen.
	const source = readFileSync('src/lib/components/theater/SaveLayout.svelte', 'utf8');

	expect(source).toContain('portalTo={stage.whatFillsTheWindow}');
});
