/*
 * The studios a stash-box made Sites of that may be one person's own store: one card each, the
 * numbers the question rests on, and its two answers, each told with the Undo its receipt carries.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), show: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post }
}));

vi.mock('$lib/shell/toasts.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/shell/toasts.svelte')>();
	return { ...real, toasts: { ...real.toasts, show: mocks.show } };
});

import StudiosPanel from './StudiosPanel.svelte';

function question(over: Record<string, unknown> = {}) {
	return {
		id: 'site-9',
		name: 'Esme Wrenfield',
		files: 4,
		scenes: 4,
		credited: 2,
		others: 1,
		site: 'ManyVids',
		handle: 'Esme-Wrenfield',
		store: true,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.get.mockResolvedValue({ items: [question()], total: 1, offset: 0 });
	mocks.post.mockResolvedValue({
		receipt_id: 'r-1',
		said: 'Esme Wrenfield is a username on ManyVids, not a Site'
	});
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function draw(): Promise<void> {
	drawn = mount(StudiosPanel, { target: host }) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

it('asks about each studio with the numbers behind the question and where Yes puts its files', async () => {
	await draw();

	expect(mocks.get).toHaveBeenCalledWith('/stash-boxes/creator-sites');
	const said = words(host);
	expect(said).toContain('Is Esme Wrenfield a username on ManyVids rather than a Site?');
	expect(said).toContain('4 files. Esme Wrenfield is named on 2 of 4.');
	expect(said).toContain('Its store page is Esme-Wrenfield on ManyVids.');
	expect(said).toContain('1 Site to check');
});

it('Yes moves the studio and says so with an Undo', async () => {
	await draw();

	const yes = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Yes, a username')
	);
	expect(yes).toBeDefined();
	yes!.click();
	await tick();
	await tick();
	flushSync();

	expect(mocks.post).toHaveBeenCalledWith('/stash-boxes/creator-sites/site-9/username', {});
	expect(mocks.show).toHaveBeenCalledWith(
		'Esme Wrenfield is a username on ManyVids, not a Site',
		expect.objectContaining({ action: expect.objectContaining({ label: 'Undo' }) })
	);
});

it('says so plainly when there is nothing to check', async () => {
	mocks.get.mockResolvedValue({ items: [], total: 0, offset: 0 });

	await draw();

	expect(words(host)).toContain('No Sites to check');
});
