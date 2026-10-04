import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick } from 'svelte';
import NamingTemplate from './NamingTemplate.svelte';
import { ApiError } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';

/*
 * What downloaded files are called and where they land.
 *
 * The rule worth a test: the two fields are stored together, so a write that carries one and not
 * the other is not a partial save: it is a save of the missing one as empty. Leaving the naming
 * box would write a blank destination over whatever was there, every time, so the per-site list
 * could never hold anything and the folder would never stay set.
 *
 * The rest is the shape the routing panel next door already has: everything follows a default, a
 * site can be given its own, and it can be put back.
 */

const get = vi.fn();
const put = vi.fn();
const post = vi.fn();
const del = vi.fn();

vi.mock('$lib/api/client', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
	return {
		...actual,
		api: {
			get: (path: string, options?: unknown) => get(path, options),
			put: (path: string, options?: unknown) => put(path, options),
			post: (path: string, options?: unknown) => post(path, options),
			del: (path: string, options?: unknown) => del(path, options)
		}
	};
});

let host: HTMLElement;

/* The four words every download fills, then `creator` where the Site names a person, then the
   Site's own: the shape `/supported-sites` sends as `name_words`. */
const EVERY = ['site', 'name', 'date', 'time'];

const SITES = [
	{
		key: 'pornhub',
		name: 'Pornhub',
		names_creators: true,
		default_naming: '',
		name_words: [...EVERY, 'creator', 'id']
	},
	{
		key: 'youtube',
		name: 'YouTube',
		names_creators: true,
		default_naming: '{creator} - {name}',
		name_words: [...EVERY, 'creator', 'id']
	},
	{
		key: 'discord',
		name: 'Discord',
		names_creators: false,
		default_naming: '',
		name_words: [...EVERY, 'posted']
	}
];

/** What the server holds. `sites` is what has been given its own answer, which starts empty. */
function stored(sites: unknown[] = []) {
	return {
		default: {
			scope: '*default*',
			naming: '{site} - {name}',
			dest_folder_id: 'f1',
			downloader: null
		},
		sites,
		tokens: {
			site: 'The site it came from',
			creator: 'Whoever posted it',
			name: 'The name the site gave it',
			date: 'The download date',
			time: 'The download time',
			id: 'The post ID',
			posted: 'The posting date'
		},
		// Required on the wire, so a double without it is a double the server can never produce.
		downloaders: [
			{ value: '', label: 'Sift', help: 'Whatever suits the Site.' },
			{ value: 'ytdlp', label: 'yt-dlp', help: 'Reads a great many video sites.' },
			{ value: 'gallerydl', label: 'gallery-dl', help: 'Reads image galleries.' }
		]
	};
}

beforeEach(() => {
	get.mockReset();
	put.mockReset().mockResolvedValue(undefined);
	post.mockReset().mockResolvedValue({ example: 'A file name.mp4' });
	del.mockReset().mockResolvedValue(undefined);
});

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

async function render(sites: unknown[] = []) {
	get.mockImplementation((path: string) => {
		if (path === '/site-options') return Promise.resolve(stored(sites));
		if (path === '/supported-sites') return Promise.resolve(SITES);
		// The writable-folder list, asked for by the destination chooser.
		return Promise.resolve({ roots: [], folders: [] });
	});
	host = document.createElement('div');
	document.body.append(host);
	mount(NamingTemplate, { target: host });
	flushSync();
	await tick();
	await tick();
	await tick();
	flushSync();
	return host;
}

/* By its accessible name, not by "the first one on the screen".
   There are two template boxes (the default's and one inside each Site card), because
   both are the same shared field. Picking by position is a locator that goes on finding SOMETHING
   after the screen changes, which is worse than one that fails. */
function templateBox(label = 'Name template for other addresses'): HTMLInputElement {
	const found = host.querySelector(`input[aria-label="${label}"]`);
	if (!found) throw new Error(`no template box labelled ${label}`);
	return found as HTMLInputElement;
}

function button(label: string): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((element) =>
		element.textContent?.includes(label)
	) as HTMLButtonElement | undefined;
}

/* One row, three answers, and a write that carries ALL of them.
 *
 * Exercised on a Site card rather than on the default, and not by choice: the default's only
 * remaining control is a chooser, and this one is a `bits-ui` Select, which cannot be opened or
 * picked from under jsdom. Both go through the SAME `store()` (one row, one write body, one
 * rule), so the card is where the rule can actually be driven.
 *
 * The fields are stored together, so a write carrying one and not the others is not a partial
 * save: it is a save of the missing ones as empty, the folder cleared on every edit of the name
 * beside it, and any answer added to the row open to the same.
 */
const A_SITE_WITH_ITS_OWN = [
	{ scope: 'pornhub', naming: '{name}', dest_folder_id: 'f2', downloader: 'ytdlp' }
];

describe('saving one Site', () => {
	it('carries the folder and the downloader it did not touch', async () => {
		await render(A_SITE_WITH_ITS_OWN);

		const box = templateBox('Name template');
		box.value = '{site}';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		box.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
		await tick();

		expect(put).toHaveBeenCalledWith('/site-options/pornhub', {
			body: { naming: '{site}', dest_folder_id: 'f2', downloader: 'ytdlp' }
		});
	});

	it('sends an empty template as nothing rather than as an empty name', async () => {
		await render(A_SITE_WITH_ITS_OWN);

		const box = templateBox('Name template');
		box.value = '   ';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		box.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
		await tick();

		// Empty means "follow the default", which is a null and not an empty string, and the other
		// two answers survive it.
		expect(put).toHaveBeenCalledWith('/site-options/pornhub', {
			body: { naming: null, dest_folder_id: 'f2', downloader: 'ytdlp' }
		});
	});
});

describe('per site', () => {
	it('says so when nothing has its own answer', async () => {
		await render();

		expect(host.textContent).toContain('No Site has its own settings yet');
	});

	it('lists a site that does, by name rather than by key', async () => {
		await render([{ scope: 'youtube', naming: '{name}', dest_folder_id: null }]);

		// Read off the row itself rather than off the whole panel. Anything on this screen could
		// happen to contain the word (the worked example can), and a test reading the panel would
		// pass against a row showing the raw key.
		const named = [...host.querySelectorAll('.sites summary')].map((one) =>
			one.textContent?.trim()
		);
		expect(named).toContain('YouTube');
		expect(host.textContent).not.toContain('No Site has its own settings yet');
	});

	it('writes both fields when one of them is edited', async () => {
		await render([{ scope: 'youtube', naming: '{name}', dest_folder_id: 'f2' }]);

		// By its accessible name, not "the second one on the screen". Both boxes are the same
		// shared field, and index order is not a property this test is about.
		const box = templateBox('Name template');
		box.value = '{site}';
		// `input` before `blur`, which is what a browser does and what the two tests above model.
		// Setting `.value` alone leaves the field's bound state untouched, so the assertion would
		// be against a write of the value it started with.
		box.dispatchEvent(new Event('input', { bubbles: true }));
		box.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
		await tick();

		expect(put).toHaveBeenCalledWith('/site-options/youtube', {
			body: { naming: '{site}', dest_folder_id: 'f2', downloader: null }
		});
	});

	it('puts a site back to following the default', async () => {
		await render([{ scope: 'youtube', naming: '{name}', dest_folder_id: null }]);

		button('Remove its own settings')?.click();
		await tick();

		// The endpoint this row is the one caller of.
		expect(del).toHaveBeenCalledWith('/site-options/youtube', undefined);
	});

	it('takes the row off the list once it follows the default again', async () => {
		await render([{ scope: 'youtube', naming: '{name}', dest_folder_id: null }]);

		button('Remove its own settings')?.click();
		await tick();
		flushSync();

		expect(host.textContent).toContain('No Site has its own settings yet');
	});

	it('says what each of the three answers follows, and never "default"', async () => {
		await render([{ scope: 'youtube', naming: '{name}', dest_folder_id: null }]);

		const card = host.querySelector('.sites li');
		expect(card?.textContent).toContain(
			"uses the download folder above, Sift's default name and the downloader Sift chooses."
		);
		expect(card?.textContent).not.toContain('Reset to default');
	});
});

/* THE FOLDER ROW WITH NOTHING SET. "Sift" there would look like a folder called Sift, and
   there is none: with no folder set a download has nowhere to land. The row says the state and
   what it means for the next download, in the phrase every other chooser of it uses. */
describe('the download folder, not set', () => {
	it('says each download asks, and names no folder', async () => {
		get.mockImplementation((path: string) => {
			if (path === '/site-options') {
				const answer = stored();
				return Promise.resolve({
					...answer,
					default: { ...answer.default, dest_folder_id: null }
				});
			}
			if (path === '/supported-sites') return Promise.resolve(SITES);
			return Promise.resolve({ roots: [], folders: [] });
		});
		host = document.createElement('div');
		document.body.append(host);
		mount(NamingTemplate, { target: host });
		for (let turn = 0; turn < 4; turn += 1) {
			flushSync();
			await tick();
		}
		const row = host.querySelector('[aria-label="Download folder"]');
		expect(row?.textContent).toContain('Not set, so each download asks');
		expect(row?.textContent?.trim()).not.toBe('Sift');
	});
});

/* THE DEFAULT DOWNLOADS FOLDER, SET FROM ANYWHERE, not only from the folders already in a
   library. It is chosen by pointing at
   it (the operating system's dialog in the application, the folder picker in a browser), never
   typed; it is made a library folder first, and the default row is written with it and with the
   two answers it did not touch. */
describe('the default downloads folder', () => {
	it('is named for what it is, and each Site follows it rather than a row above', async () => {
		await render();
		expect(host.textContent).toContain('Download folder');
		expect(host.textContent).not.toContain('downloader above');
		expect(host.textContent).toContain('with the downloader Sift chooses for it');
	});

	it('draws no box to type a folder path into, and a browser chooses through the picker', async () => {
		await render();
		expect(host.querySelector('input[aria-label="Folder path"]')).toBeNull();
		expect(button('Choose a folder'), 'a browser opens the folder picker').toBeTruthy();
	});

	it('adds a chosen folder outside every library, then makes it the default', async () => {
		(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Grabs') };
		let added = false;
		get.mockImplementation((path: string) => {
			if (path === '/site-options') return Promise.resolve(stored());
			if (path === '/supported-sites') return Promise.resolve(SITES);
			if (path === '/library/roots') {
				return Promise.resolve({
					roots: added ? [{ id: 'r9', name: 'Grabs', path: 'D:\\Grabs' }] : []
				});
			}
			if (path === '/library/folders') {
				return Promise.resolve({
					folders: added
						? [{ id: 'f9', root_id: 'r9', parent_id: null, name: 'Grabs', rel_path: '' }]
						: []
				});
			}
			return Promise.resolve({});
		});
		post.mockImplementation((path: string) => {
			if (path === '/library/roots') added = true;
			return Promise.resolve({});
		});
		host = document.createElement('div');
		document.body.append(host);
		mount(NamingTemplate, { target: host });
		for (let turn = 0; turn < 4; turn += 1) {
			flushSync();
			await tick();
		}

		button('Choose a folder')?.click();
		await vi.waitFor(() =>
			expect(put).toHaveBeenCalledWith('/site-options/*default*', {
				body: { naming: '{site} - {name}', dest_folder_id: 'f9', downloader: null }
			})
		);
		expect(post).toHaveBeenCalledWith('/library/roots', {
			body: { abs_path: 'D:\\Grabs', scan: true }
		});
		delete (window as { sift?: unknown }).sift;
	});
});

/* One Site's card, by the name on it, so a control inside it is never confused with the same
   control on the box for other addresses above. */
function card(name: string): HTMLElement {
	const found = [...host.querySelectorAll<HTMLElement>('.sites li')].find(
		(one) => one.querySelector('summary')?.textContent?.trim() === name
	);
	if (!found) throw new Error(`no card for ${name}`);
	return found;
}

function words(within: HTMLElement): string[] {
	return [...within.querySelectorAll('.token')].map((one) => one.textContent ?? '');
}

/* The select primitive captures the pointer, which jsdom does not implement. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

/** Choose a ready-made name on a card's presets row: a choice on the row, like every setting. */
function presetIn(within: HTMLElement, label: string): void {
	const trigger = within.querySelector<HTMLElement>('.ui-select[aria-label^="Name presets"]');
	if (!trigger) throw new Error('no presets choice');
	open(trigger);
	const item = [...document.querySelectorAll('.ui-select-item-label')].find(
		(one) => one.textContent?.trim() === label
	);
	if (!item) throw new Error(`no preset ${label}`);
	open(item.closest('.ui-select-item'));
}

/** Press as a pointer does: the primitive opens and picks on pointer events, not on click alone. */
function open(element: Element | null | undefined): void {
	element?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	element?.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	(element as HTMLElement | null | undefined)?.click();
	flushSync();
}

function pressIn(within: HTMLElement, label: string): void {
	const found = [...within.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === label
	);
	if (!found) throw new Error(`no button ${label}`);
	found.click();
}

/* THE RULE FOR EVERY ADDRESS SIFT HAS NO SITE FOR. Stored, it has to be drawn. */
describe('the box for other addresses', () => {
	it('is drawn with what it covers and an example of its name', async () => {
		await render();

		const box = templateBox();
		expect(box.value).toBe('{site} - {name}');
		expect(host.textContent).toContain('For addresses Sift has no Site for.');
		expect(host.textContent).toContain('A file would be called A file name.mp4');
		expect(post).toHaveBeenCalledWith('/site-options/preview', {
			body: { naming: '{site} - {name}', scope: null }
		});
	});
});

describe('the example while typing', () => {
	/* The box asks for an example on every keystroke, and answers need not come back in the
	   order they were asked: with the first answer held back, the box could read `{site} x` and
	   the line under it "A file would be called {". Only the newest question's answer may be
	   shown, and a refusal of an older one must not replace it either. */
	it('shows the answer to what is in the box, not an older answer that arrived last', async () => {
		await render();
		const held: ((answer: { example: string }) => void)[] = [];
		const refused: ((error: unknown) => void)[] = [];
		post.mockImplementation(
			() =>
				new Promise((resolve, reject) => {
					held.push(resolve);
					refused.push(reject);
				})
		);
		const box = templateBox();
		for (const typed of ['{', '{site}', '{site} x']) {
			box.value = typed;
			box.dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
		}
		expect(held).toHaveLength(3);

		// The newest answer first, then the two older ones: one an answer, one a refusal.
		held[2]({ example: 'Example x' });
		await tick();
		held[0]({ example: '{' });
		refused[1](new ApiError(400, 'That request was not valid', 'An older refusal'));
		for (let turn = 0; turn < 3; turn += 1) {
			await tick();
			flushSync();
		}

		expect(host.textContent).toContain('A file would be called Example x');
		expect(host.textContent).not.toContain('A file would be called {');
		expect(host.textContent).not.toContain('An older refusal');
	});
});

describe('a Site row', () => {
	it('puts a separator between two words pressed one after the other', async () => {
		await render([{ scope: 'youtube', naming: '{site}', dest_folder_id: null }]);
		const youtube = card('YouTube');
		const box = templateBox('Name template');
		box.value = '';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		pressIn(youtube, '{creator}');
		flushSync();
		pressIn(youtube, '{name}');
		await tick();

		expect(put).toHaveBeenLastCalledWith('/site-options/youtube', {
			body: { naming: '{creator} - {name}', dest_folder_id: null, downloader: null }
		});
	});

	it('leaves a separator somebody typed as they typed it', async () => {
		await render([{ scope: 'youtube', naming: '{site}_', dest_folder_id: null }]);

		pressIn(card('YouTube'), '{name}');
		await tick();

		expect(put).toHaveBeenLastCalledWith('/site-options/youtube', {
			body: { naming: '{site}_{name}', dest_folder_id: null, downloader: null }
		});
	});

	it('offers only the words the Site can fill', async () => {
		await render([{ scope: 'discord', naming: null, dest_folder_id: null }]);

		const discord = words(card('Discord'));
		expect(discord).not.toContain('{creator}');
		expect(discord).toContain('{posted}');
		// Nor a pattern built on it.
		const trigger = card('Discord').querySelector<HTMLElement>(
			'.ui-select[aria-label^="Name presets"]'
		);
		open(trigger);
		const offeredPresets = [...document.querySelectorAll('.ui-select-item-label')].map((one) =>
			one.textContent?.trim()
		);
		expect(offeredPresets).toContain("The Site's own file name");
		expect(offeredPresets).not.toContain('Creator - Name');
		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		// The box for other addresses still offers it: the page read can name a creator there.
		expect(words(host)).toContain('{creator}');
	});

	it("says Sift's name for the Site while it has no rule, and previews that name", async () => {
		await render([{ scope: 'youtube', naming: null, dest_folder_id: null }]);

		expect(card('YouTube').textContent).toContain(
			'Sift names YouTube files {creator} - {name} unless you type a rule.'
		);
		expect(templateBox('Name template').placeholder).toBe('{creator} - {name}');
		expect(post).toHaveBeenCalledWith('/site-options/preview', {
			body: { naming: '{creator} - {name}', scope: 'youtube' }
		});
	});

	it('stores keep as an empty rule, apart from no rule, and says so', async () => {
		await render([{ scope: 'youtube', naming: null, dest_folder_id: null }]);

		presetIn(card('YouTube'), "The Site's own file name");
		await tick();
		flushSync();

		expect(put).toHaveBeenLastCalledWith('/site-options/youtube', {
			body: { naming: '', dest_folder_id: null, downloader: null }
		});
		expect(card('YouTube').textContent).toContain(
			'Keeps the name YouTube gave each file, as you chose.'
		);

		// And Sift's name is one press back, as no rule rather than as a copy of it.
		presetIn(card('YouTube'), "Sift's default name");
		await tick();
		expect(put).toHaveBeenLastCalledWith('/site-options/youtube', {
			body: { naming: null, dest_folder_id: null, downloader: null }
		});
	});

	it('does not turn keep into no rule when the empty box is only visited', async () => {
		await render([{ scope: 'youtube', naming: '', dest_folder_id: null }]);

		templateBox('Name template').dispatchEvent(new FocusEvent('blur', { bubbles: true }));
		await tick();

		expect(put).not.toHaveBeenCalled();
	});

	it('goes back to what is stored when the save is refused, in the words it was refused in', async () => {
		const refusal =
			"Discord doesn't say who posted a file, so {creator} would always be empty. Remove it from the name.";
		put.mockRejectedValue(new ApiError(400, 'That request was not valid', refusal));
		await render([{ scope: 'discord', naming: null, dest_folder_id: null }]);

		const box = templateBox('Name template');
		box.value = '{site} {creator}';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		box.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
		for (let turn = 0; turn < 3; turn += 1) {
			await tick();
			flushSync();
		}

		expect(toasts.items.map((one) => one.message).join(' ')).toContain('would always be empty');
		expect(card('Discord').textContent).toContain(
			'Sift keeps the name Discord gave each file unless you type a rule.'
		);
	});
});

/* The ready-made names are a row of the pane with one choice, never a strip of buttons under a
   paragraph, and the choice reads which answer is in force. */
describe('the name presets', () => {
	it('are one choice on a row, reading the answer in force', async () => {
		await render([{ scope: 'youtube', naming: '{site} - {name}', dest_folder_id: null }]);

		const youtube = card('YouTube');
		const trigger = youtube.querySelector<HTMLElement>('.ui-select[aria-label^="Name presets"]');
		expect(trigger?.closest('.row')).not.toBeNull();
		expect(trigger?.textContent).toContain('Site - Name');
		expect(youtube.querySelector('[role="group"][aria-label="Name presets"]')).toBeNull();
	});

	it("reads a typed rule as the person's own, which cannot be chosen", async () => {
		await render([{ scope: 'youtube', naming: '{name} x', dest_folder_id: null }]);

		const trigger = card('YouTube').querySelector<HTMLElement>(
			'.ui-select[aria-label^="Name presets"]'
		);
		expect(trigger?.textContent).toContain('Your own rule');
	});
});
