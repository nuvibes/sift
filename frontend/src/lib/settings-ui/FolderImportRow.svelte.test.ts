/*
 * The folder of people is imported as a task: the press hands Sift the folder and answers at once,
 * and the row follows the task to its report.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const calls = vi.hoisted(() => ({
	post: vi.fn(),
	get: vi.fn()
}));

vi.mock('$lib/api/client', async (actual) => ({
	...(await actual<typeof import('$lib/api/client')>()),
	api: { post: calls.post, get: calls.get }
}));

import FolderImportRow from './FolderImportRow.svelte';
import { ApiError } from '$lib/api/client';
import { endedWith, folderImport } from '$lib/people/folder-import.svelte';

let drawn: Record<string, unknown> | null = null;
let rows: Record<string, unknown>[] = [];

beforeEach(() => {
	vi.useFakeTimers();
	rows = [];
	calls.post.mockImplementation(async (path: string) =>
		path.startsWith('/faces/references/folder') ? { job_id: 'j1' } : {}
	);
	calls.get.mockImplementation(async (path: string) => {
		if (path === '/jobs') return { jobs: rows, total: rows.length };
		if (path === '/library/browse') {
			return { path: null, entries: [], breadcrumb: [], file_count: 0, nothing_granted: true };
		}
		return { grants: [] };
	});
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	folderImport.couldNotStart('');
	folderImport.outcome = null;
	document.body.innerHTML = '';
	delete (window as { sift?: unknown }).sift;
	calls.post.mockReset();
	calls.get.mockReset();
	vi.useRealTimers();
});

function draw(): HTMLElement {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FolderImportRow, {
		target: host,
		props: {
			id: 'faces.folder-import',
			label: 'Import a structured folder to create facial fingerprints',
			help: 'Choose a folder that holds one subfolder of photos for each person.',
			action: 'Import folder',
			busy: 'Importing\u2026'
		}
	}) as Record<string, unknown>;
	flushSync();
	return host;
}

function pressOf(host: HTMLElement): HTMLButtonElement {
	const button = host.querySelector<HTMLButtonElement>(
		'[id="faces.folder-import"] .control button'
	);
	if (!button) throw new Error('no press on the row');
	return button;
}

async function settle(): Promise<void> {
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();
}

function row(state: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		id: 'j1',
		type: 'face_folder_import',
		state,
		progress: 0,
		note: null,
		error: null,
		...extra
	};
}

it('sends only the path the desktop dialog chose and follows the task it answers with', async () => {
	(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Gallery') };
	const host = draw();

	pressOf(host).click();
	await settle();

	expect(calls.post).toHaveBeenCalledWith('/library/grants', { body: { path: 'D:\\Gallery' } });
	expect(calls.post).toHaveBeenCalledWith('/faces/references/folder/path', {
		body: { path: 'D:\\Gallery' }
	});
	expect(folderImport.jobId).toBe('j1');
	expect(pressOf(host).textContent).toContain('Cancel import');

	rows = [row('running', { progress: 0.25, note: 'Importing 3 of 12 people\u2026' })];
	await vi.advanceTimersByTimeAsync(2100);
	await settle();

	const status = host.querySelector('[id="faces.folder-import"] p.status')?.textContent;
	expect(status).toBe('Importing 3 of 12 people\u2026');
	expect(host.querySelector('[role="progressbar"]')).not.toBeNull();
});

it("says the task's report on the row when it ends", async () => {
	(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Gallery') };
	const host = draw();
	pressOf(host).click();
	await settle();

	rows = [row('done', { progress: 1, note: 'Kept 12 faces from 3 people.' })];
	await vi.advanceTimersByTimeAsync(2100);
	await settle();

	expect(folderImport.running).toBe(false);
	expect(host.textContent).toContain('Kept 12 faces from 3 people.');
	expect(pressOf(host).textContent).toContain('Import folder');
});

it('folds the files under each count of the report, as many as the report says', async () => {
	(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Gallery') };
	const host = draw();
	pressOf(host).click();
	await settle();
	const said = 'Kept 0 faces from 2 people. 3 photos left out: 2 with no face in it, 1 too small.';
	const left = {
		left_out: [
			{
				reason: 'no_face',
				words: 'with no face in it',
				files: ['Bryn Calloway/a.jpg', 'Elina Sorrel/b.jpg']
			},
			{ reason: 'too_small', words: 'too small', files: ['Bryn Calloway/c.jpg'] }
		],
		near_copies: []
	};
	const reads = calls.get.getMockImplementation();
	calls.get.mockImplementation(async (path: string, ...rest: unknown[]) =>
		path === '/faces/references/folder/j1/left-out' ? left : reads?.(path, ...rest)
	);

	rows = [row('done', { progress: 1, note: said })];
	await vi.advanceTimersByTimeAsync(2100);
	await settle();

	const folds = [...host.querySelectorAll('[id="faces.folder-import"] details')];
	expect(folds.map((one) => one.querySelector('summary')?.textContent?.trim())).toEqual([
		'2 with no face in it',
		'1 too small'
	]);
	for (const fold of folds) {
		const [count, ...words] = (fold.querySelector('summary')?.textContent ?? '').trim().split(' ');
		expect(said).toContain(`${count} ${words.join(' ')}`);
		expect(fold.querySelectorAll('li')).toHaveLength(Number(count));
	}
});

it('draws no fold when the files cannot be read', async () => {
	(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Gallery') };
	const host = draw();
	pressOf(host).click();
	await settle();
	const reads = calls.get.getMockImplementation();
	calls.get.mockImplementation(async (path: string, ...rest: unknown[]) => {
		if (path.endsWith('/left-out')) throw new Error('offline');
		return reads?.(path, ...rest);
	});

	rows = [
		row('done', { progress: 1, note: 'Kept 0 faces from 1 person. 1 photo left out: 1 too small.' })
	];
	await vi.advanceTimersByTimeAsync(2100);
	await settle();

	expect(host.textContent).toContain('1 photo left out');
	expect(host.querySelector('[id="faces.folder-import"] details')).toBeNull();
});

it("wears the import's upload glyph on the press that sends a folder from this device", async () => {
	const host = draw();
	pressOf(host).click();
	await settle();

	const buttons = [...document.querySelectorAll('button')];
	const glyphOf = (words: string) =>
		buttons.find((one) => one.textContent?.includes(words))?.querySelector('.icon')?.textContent;
	expect(glyphOf('Import from this device')).toBeTruthy();
	expect(glyphOf('Import from this device')).toBe(glyphOf('Import this folder'));
});

it('cancels the task from the row, and says what a cancel keeps', async () => {
	(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Gallery') };
	const host = draw();
	pressOf(host).click();
	await settle();
	expect(host.textContent).toContain('Canceling keeps every face already imported.');

	pressOf(host).click();
	await settle();
	expect(calls.post).toHaveBeenCalledWith('/jobs/j1/cancel');

	rows = [row('canceled')];
	await vi.advanceTimersByTimeAsync(2100);
	await settle();
	expect(host.textContent).toContain(
		'Import canceled. The faces imported before it stopped are kept.'
	);
});

it("shows a refused folder in the server's words and follows nothing", async () => {
	(window as { sift?: unknown }).sift = {
		chooseFolder: vi.fn().mockResolvedValue('D:\\Elsewhere')
	};
	calls.post.mockImplementation(async (path: string) => {
		if (path === '/faces/references/folder/path') {
			throw new ApiError(400, 'refused', "Sift hasn't been given that folder.");
		}
		return {};
	});
	const host = draw();

	pressOf(host).click();
	await settle();

	expect(folderImport.jobId).toBeNull();
	expect(host.textContent).toContain("Sift hasn't been given that folder.");
});

it('opens the folders Sift has in a browser, with a way to send a folder from this device', async () => {
	const host = draw();

	pressOf(host).click();
	await settle();

	expect(calls.post).not.toHaveBeenCalled();
	expect(document.body.textContent).toContain('Folders Sift has');
	expect(document.body.querySelector('input[webkitdirectory]')).not.toBeNull();
	expect(document.body.textContent).toContain('Every photo is sent to Sift first');
});

it('sends a folder from this device as its files alone, each with its path under the folder', async () => {
	const host = draw();
	pressOf(host).click();
	await settle();
	const input = document.body.querySelector<HTMLInputElement>('input[webkitdirectory]')!;
	const file = new File(['x'], 'one.jpg', { type: 'image/jpeg' });
	Object.defineProperty(file, 'webkitRelativePath', { value: 'Gallery/Wren Halloway/one.jpg' });
	Object.defineProperty(input, 'files', { value: [file], configurable: true });

	input.dispatchEvent(new Event('change', { bubbles: true }));
	await settle();

	const [path, { body }] = calls.post.mock.calls.at(-1) as [string, { body: FormData }];
	expect(path).toBe('/faces/references/folder');
	expect([...body.keys()]).toEqual(['files']);
	expect((body.get('files') as File).name).toBe('Gallery/Wren Halloway/one.jpg');
	expect(folderImport.jobId).toBe('j1');
});

it('says a failed task in its own words and a cap in the words the server chose', () => {
	expect(endedWith({ state: 'failed', note: null, error: 'Import it in parts.' })).toBe(
		'Import it in parts.'
	);
	expect(endedWith({ state: 'done', note: 'Kept 1 face from 1 person.', error: null })).toBe(
		'Kept 1 face from 1 person.'
	);
});

/** The folder sheet's form, opened from the row in a browser. */
async function openTheSheet(host: HTMLElement): Promise<HTMLFormElement> {
	pressOf(host).click();
	await settle();
	const form = document.body.querySelector<HTMLFormElement>('form.folder-import');
	if (!form) throw new Error('the folder sheet did not open');
	return form;
}

it('refuses Import this folder on the list of folders Sift has, and says to click into one first', async () => {
	const host = draw();
	const form = await openTheSheet(host);

	form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }));
	await settle();

	expect(document.body.querySelector('[role="alert"]')?.textContent).toContain(
		'Click into a folder first'
	);
	expect(calls.post).not.toHaveBeenCalled();
});

it('imports the folder the browser picker stands in, by its path on the machine Sift runs on', async () => {
	calls.get.mockImplementation(async (path: string) => {
		if (path === '/jobs') return { jobs: rows, total: rows.length };
		if (path === '/library/browse') {
			const gallery = { name: 'Gallery', path: 'D:\\Gallery' };
			return { path: 'D:\\Gallery', entries: [], breadcrumb: [gallery], file_count: 3 };
		}
		return { grants: [] };
	});
	const host = draw();
	const form = await openTheSheet(host);

	form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }));
	await settle();

	expect(calls.post).toHaveBeenCalledWith('/faces/references/folder/path', {
		body: { path: 'D:\\Gallery' }
	});
	expect(folderImport.jobId).toBe('j1');
});

it("says it is reading the folder once the device's dialog gives focus back, and stops on its cancel", async () => {
	const host = draw();
	await openTheSheet(host);
	const input = document.body.querySelector<HTMLInputElement>('input[webkitdirectory]')!;

	// The click that opens the device's dialog arms the listener; focus coming back is its close.
	input.click();
	window.dispatchEvent(new Event('focus'));
	await settle();
	expect(host.querySelector('p.status')?.textContent).toBe('Reading the folder\u2026');
	expect(document.body.querySelector('form.folder-import [role="status"]')?.textContent).toBe(
		'Reading the folder\u2026'
	);

	// A dialog closed with Cancel says so with a cancel event: nothing is being read any more.
	input.click();
	window.dispatchEvent(new Event('cancel'));
	await settle();
	expect(host.querySelector('p.status')).toBeNull();
});

it('says a refused cancel in words that point to Activity', async () => {
	(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Gallery') };
	const host = draw();
	pressOf(host).click();
	await settle();
	calls.post.mockRejectedValueOnce(new ApiError(500, 'boom', 'no'));

	pressOf(host).click();
	await settle();

	expect(host.textContent).toContain("Couldn't cancel the import. Cancel it in Activity.");
});
