/* A folder's own answers, each row called by what it answers.
 *
 * The keys a folder may answer come from the server, and several are retired into the task Whens:
 * a folder's answer is still stored under the old key, and a retired key has no registered row,
 * so labelled off the rows the pane has loaded it would read as a raw key. The names come with
 * the list, and a retired key is called by the task it became.
 */

import { words } from '$lib/design/testing.svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({ fetchFolderAnswers: vi.fn() }));

vi.mock('$lib/library/importing', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/library/importing')>()),
	fetchFolderAnswers: mocks.fetchFolderAnswers,
	setFolderAnswers: vi.fn()
}));

import ImportingFolders from './ImportingFolders.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';

let host: HTMLDivElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	drilldown.close();
});

async function open(list: Record<string, unknown>): Promise<void> {
	mocks.fetchFolderAnswers.mockResolvedValue(list);
	drawn = mount(ImportingFolders, { target: host }) as Record<string, unknown>;
	mount(DrilldownPage, { target: host, props: { behind: 'Importing' } });
	await vi.waitFor(() => expect(host.querySelector('button')).not.toBeNull(), { interval: 1 });
	const edit = [...host.querySelectorAll('button')].find((one) => words(one) === 'Edit');
	expect(edit, 'the folder row has no Edit').toBeDefined();
	edit!.click();
	for (let turn = 0; turn < 4; turn += 1) await tick();
	flushSync();
}

it('calls each key by the name the list sends, a retired one by the task it became', async () => {
	await open({
		folders: [{ root_id: 'root-1', name: 'Holiday', answers: {} }],
		keys: ['importing.generate', 'performance.generate_previews'],
		labels: {
			'importing.generate': 'Generate thumbnails and previews',
			'performance.generate_previews': 'Hover previews'
		}
	});

	const text = host.textContent ?? '';
	expect(text).toContain('Generate thumbnails and previews');
	expect(text).toContain('Hover previews');
	expect(text, 'a raw key reached the page').not.toContain('importing.generate');
});

it("names each folder's Edit by its folder, so a screen reader can tell them apart", async () => {
	// A pane of them all called "Edit" is a list of identical names to a screen reader.
	mocks.fetchFolderAnswers.mockResolvedValue({
		folders: [
			{ root_id: 'root-1', name: 'Holiday', answers: {} },
			{ root_id: 'root-2', name: 'Beach', answers: {} }
		],
		keys: [],
		labels: {}
	});
	drawn = mount(ImportingFolders, { target: host }) as Record<string, unknown>;
	await vi.waitFor(() => expect(host.querySelector('button')).not.toBeNull(), { interval: 1 });

	const names = [...host.querySelectorAll('button')]
		.filter((one) => words(one) === 'Edit')
		.map((one) => one.getAttribute('aria-label'));
	expect(names).toEqual(['Edit Holiday', 'Edit Beach']);
});

it('falls back to the key only when the list names nothing for it', async () => {
	await open({
		folders: [{ root_id: 'root-1', name: 'Holiday', answers: {} }],
		keys: ['importing.generate']
	});

	expect(host.textContent ?? '').toContain('importing.generate');
});

it("draws the server's folder words for a key and the help under it", async () => {
	/* The server names a retired key by what a folder's answer does (only what happens as a file
	   ARRIVES there), declared where the key was retired, and sends the help with it. The page
	   draws both as sent and keeps no words of its own. */
	await open({
		folders: [{ root_id: 'root-1', name: 'Holiday', answers: {} }],
		keys: ['music.fingerprint'],
		labels: { 'music.fingerprint': 'Fingerprint music as files arrive' },
		helps: {
			'music.fingerprint':
				"Reads the file's sound once as it arrives, on this device, when Generate runs for this folder."
		}
	});

	const text = (host.textContent ?? '').replace(/\s+/g, ' ');
	expect(text).toContain('Fingerprint music as files arrive');
	expect(text).toContain('when Generate runs for this folder.');
	// And a folder that has said nothing follows the library, which is off out of the box.
	expect(text).toContain('Follow the default');
});

it('folds the folders under words that count them, so the pane reads as its own rows first', async () => {
	mocks.fetchFolderAnswers.mockResolvedValue({
		folders: [
			{ root_id: 'root-1', name: 'Holiday', answers: {} },
			{ root_id: 'root-2', name: 'Beach', answers: {} }
		],
		keys: [],
		labels: {}
	});
	drawn = mount(ImportingFolders, { target: host }) as Record<string, unknown>;
	await vi.waitFor(() => expect(host.querySelector('details')).not.toBeNull(), { interval: 1 });

	const fold = host.querySelector('details') as HTMLDetailsElement;
	expect(fold.open, 'the list starts folded').toBe(false);
	expect(fold.querySelector('summary')?.textContent).toContain('Show all 2 library folders');
	expect(fold.textContent).toContain('Holiday');
});
