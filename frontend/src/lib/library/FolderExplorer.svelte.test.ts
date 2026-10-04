/* The explorer's own drawing: the folders in a folder, and what it says when it cannot read one.
 *
 * What is asserted here is the handful it can get wrong in a way nobody would report. Where you
 * are is owned by the ADDRESS, so this component asks to move rather than moving itself. Get
 * that wrong and the back button walks out of the explorer instead of up a folder. A failed read
 * is not an empty folder. And a folder whose parent the viewer cannot see still has to be
 * reachable, or a share silently did nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import FolderExplorer from './FolderExplorer.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

const FOLDERS = {
	folders: [
		{ id: 'top-1', root_id: 'r1', parent_id: null, name: 'Videos', rel_path: '' },
		{ id: 'inner-1', root_id: 'r1', parent_id: 'top-1', name: 'Holidays', rel_path: 'Holidays' },
		{ id: 'inner-2', root_id: 'r1', parent_id: 'top-1', name: 'Archive', rel_path: 'Archive' }
	]
};

const originalFetch = globalThis.fetch;

function json(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

/** The read this draws from: the folder tree. The files are the wall's, not this component's. */
function serving(over: { folders?: unknown | 'refuse'; assets?: unknown } = {}) {
	const mock = vi.fn(async (url: URL | RequestInfo): Promise<Response> => {
		const path = new URL(String(url), 'http://sift.test').pathname;
		if (path.endsWith('/library/folders')) {
			if (over.folders === 'refuse') return json({ detail: 'no' }, 500);
			return json(over.folders ?? FOLDERS);
		}
		// Still answered, because the properties panel counts what is in a folder through it, but
		// nothing this component DRAWS comes from here.
		if (path.endsWith('/assets')) return json(over.assets ?? { items: [], total: 0 });
		if (path.endsWith('/sharing/reach')) {
			return json({
				concealed: false,
				hidden: false,
				outside: null,
				subject_id: 'inner-1',
				subject_type: 'folder',
				users: []
			});
		}
		return json({});
	});
	globalThis.fetch = mock as unknown as typeof fetch;
	return mock;
}

/** Let the mount's own fetch land. `flushSync` alone only drains Svelte, not the network. */
async function settle(times = 4) {
	for (let round = 0; round < times; round += 1) {
		await new Promise((done) => setTimeout(done, 0));
		flushSync();
	}
}

function shown(): (string | undefined)[] {
	return [...host.querySelectorAll('.what')].map((one) => one.textContent?.trim());
}

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	globalThis.fetch = originalFetch;
});

describe('the folder explorer', () => {
	it('lists what is directly inside the folder it was told to open', async () => {
		const asked = serving();
		mounted = mount(FolderExplorer, { target: host, props: { here: 'top-1' } });
		await settle();

		expect(asked).toHaveBeenCalled();
		expect(shown()).toContain('Holidays');
		expect(shown()).toContain('Archive');
	});

	/*
	 * One hover box for every tile: a folder's marks sit on its name's line, the tile's foot, and
	 * never on a line of their own. Stacked, a shared folder's tile would grow a third row and its
	 * hover wash come out taller than its neighbours'. The layout itself is the browser's; what is pinned
	 * is that the marks have no row of their own.
	 */
	it("puts a tile's marks on its name's line, so every tile is one height", async () => {
		serving({
			folders: {
				folders: [...FOLDERS.folders.slice(0, 2), { ...FOLDERS.folders[2], shared: true }]
			}
		});
		mounted = mount(FolderExplorer, { target: host, props: { here: 'top-1', view: 'thumbs' } });
		await settle();

		const tiles = [...host.querySelectorAll('.thumbs li')];
		expect(tiles.length).toBe(2);
		for (const tile of tiles) {
			const pressed = tile.querySelector('button');
			const kids = [...(pressed?.children ?? [])].map((one) => one.className.split(' ')[0]);
			// The picture, then the foot: nothing else is a row of the tile.
			expect(kids).toEqual(['art', 'foot']);
		}
		expect(host.querySelector('.foot .marks')).not.toBeNull();
	});

	it('asks to move rather than moving itself, because the address owns where you are', async () => {
		serving();
		const onmove = vi.fn();
		mounted = mount(FolderExplorer, { target: host, props: { here: 'top-1', onmove } });
		await settle();

		const row = [...host.querySelectorAll<HTMLElement>('.row')].find((one) =>
			one.textContent?.includes('Holidays')
		);
		row?.click();
		await settle();

		// The component does not decide. Left to decide, the address would not change and neither
		// reloading nor the back button would follow anybody up a folder.
		expect(onmove).toHaveBeenCalledWith('inner-1');
	});

	it('hands the way back up to the screen that draws the trail', async () => {
		serving();
		const ontrail = vi.fn();
		mounted = mount(FolderExplorer, { target: host, props: { here: 'inner-1', ontrail } });
		await settle();

		const steps = ontrail.mock.calls.at(-1)?.[0] as { name: string }[];
		expect(steps.map((step) => step.name)).toEqual(['Videos', 'Holidays']);
	});

	it('does not call a folder list it could not read an empty one', async () => {
		/*
		 * THE SAME INVARIANT, ABOUT THE READ THIS COMPONENT MAKES.
		 *
		 * The files are the real wall (`AssetGrid`, scoped to the folder), so this component
		 * does not ask for them at all. What is left is the folder tree it does read, and the fault
		 * is identical in shape: a folder tree that could not be fetched must not be reported as a
		 * folder with nothing in it. Somebody told their folders are empty stops looking; somebody
		 * told Sift could not be asked tries again.
		 */
		serving({ folders: 'refuse' });
		mounted = mount(FolderExplorer, { target: host, props: { here: 'inner-1' } });
		await settle();
		await settle();

		const said = host.textContent ?? '';
		expect(said).toContain("couldn't be read");
		expect(said).not.toContain('No folders');
	});

	it('names the folders inside this one, so the wall below can leave them out', async () => {
		/* `in:` means a folder's whole SUBTREE to the server, so the wall under a folder would draw
		 * every file beneath it: a folder holding only folders would come up as a wall of its
		 * grandchildren's tiles. Filtering it to what is IN the folder means naming the children to exclude, and
		 * they are these rows: this component has already read the tree and the screen has not. */
		serving();
		const onlisted = vi.fn();
		mounted = mount(FolderExplorer, { target: host, props: { here: 'top-1', onlisted } });
		await settle();

		const listed = onlisted.mock.calls.at(-1)?.[0] as string[];
		expect([...listed].sort()).toEqual(['inner-1', 'inner-2']);
	});

	it('says nothing about the children until the tree has landed', async () => {
		/* Before the read, "no folders" is not an answer: it is the absence of one. Acted on, the
		 * wall would ask for the whole subtree once and for the folder again a moment later. */
		serving({ folders: 'refuse' });
		const onlisted = vi.fn();
		mounted = mount(FolderExplorer, { target: host, props: { here: 'inner-1', onlisted } });
		flushSync();

		expect(onlisted).not.toHaveBeenCalled();
	});

	it('shows a folder whose parent the viewer cannot see', async () => {
		/* Restrict a folder, share one folder inside it, and the inner one is in the answer while its
		 * parent is not: nearest-wins working as intended, and the whole reason restrict is worth
		 * having. Filtered only on "my parent is where you are", it would be in the answer and
		 * reachable from nowhere, so the share would silently do nothing. */
		serving({
			folders: {
				folders: [
					{ id: 'top-1', root_id: 'r1', parent_id: null, name: 'Videos', rel_path: '' },
					{
						id: 'orphan',
						root_id: 'r1',
						parent_id: 'not-in-the-answer',
						name: 'Shared',
						rel_path: 'Hidden/Shared'
					}
				]
			}
		});
		mounted = mount(FolderExplorer, { target: host, props: { here: null } });
		await settle();

		expect(shown()).toContain('Shared');
	});
});

/* jsdom has no pointer capture and the menu primitives release it on the way down; absent, the
   handler throws and no menu ever opens. A gap in the test environment, not in the app. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

/** The words of a menu row, with the icon ligature's private-use codepoints taken out. */
function words(row: Element): string {
	return (row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();
}

function rows(): string[] {
	return [...document.querySelectorAll('[role="menuitem"]')].map(words);
}

describe("a folder's own menu", () => {
	beforeEach(() => {
		session.viewer = { role: 'admin', can_save_to_device: true } as Viewer;
	});
	afterEach(() => {
		session.viewer = undefined;
		document.body.innerHTML = '';
	});

	/*
	 * Add to, the door a file's menu and the selection bar open, with its six rows opening out, and
	 * no naming questions of its own: one way to put things on a person or a Site, on
	 * every screen. The rows are the declaration's (`grid/verbs.ts`), so the words asserted here are
	 * the ones every other Add to draws.
	 */
	it('offers Add to with the same rows as every other door, and no naming questions', async () => {
		serving();
		mounted = mount(FolderExplorer, { target: host, props: { here: 'top-1' } });
		await settle();

		const triggers = [...host.querySelectorAll('[data-context-menu-trigger]')].filter((one) =>
			one.textContent?.includes('Holidays')
		);
		triggers
			.at(-1)
			?.querySelector('button')
			?.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 40, clientY: 40 }));
		await vi.waitFor(() => expect(rows()).toContain('Add to'), { timeout: 5000 });

		expect(rows()).not.toContain('Who is this?');
		expect(rows()).not.toContain('Which Site is this?');

		const opener = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
			(row) => words(row) === 'Add to'
		) as HTMLElement;
		opener.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		opener.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
		opener.click();
		flushSync();

		await vi.waitFor(
			() =>
				expect(rows()).toEqual(
					expect.arrayContaining(['Person', 'Site', 'Collection', 'Photo Set', 'Tag', 'Favorites'])
				),
			{ timeout: 3000 }
		);
	});

	/*
	 * Who sees it, the rows every folder menu draws (`folderVerbs`): Visibility opens the same report
	 * the entity pages open, asked about THIS folder.
	 */
	it('offers Hide, Share and Visibility, and Visibility opens the report on this folder', async () => {
		const asked = serving();
		mounted = mount(FolderExplorer, { target: host, props: { here: 'top-1' } });
		await settle();

		const triggers = [...host.querySelectorAll('[data-context-menu-trigger]')].filter((one) =>
			one.textContent?.includes('Holidays')
		);
		triggers
			.at(-1)
			?.querySelector('button')
			?.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 40, clientY: 40 }));
		await vi.waitFor(() => expect(rows()).toContain('Visibility'), { timeout: 5000 });
		// The folder's own menu is the last opened: the ground's menu behind it has its own Visibility.
		const menu = [...document.querySelectorAll('[role="menu"]')].at(-1)!;
		const order = [...menu.querySelectorAll('[role="menuitem"]')]
			.map(words)
			.filter((row) => ['Hide', 'Share', 'Visibility'].includes(row));
		expect(order).toEqual(['Hide', 'Share', 'Visibility']);

		const visibility = [...menu.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
			(row) => words(row) === 'Visibility'
		) as HTMLElement;
		visibility.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		visibility.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
		visibility.click();
		flushSync();

		await vi.waitFor(
			() => {
				const reach = asked.mock.calls
					.map(([url]) => new URL(String(url), 'http://sift.test'))
					.find((url) => url.pathname.endsWith('/sharing/reach'));
				expect(reach?.searchParams.get('object_type')).toBe('folder');
				expect(reach?.searchParams.get('object_id')).toBe('inner-1');
			},
			{ timeout: 3000 }
		);
		await vi.waitFor(
			() => expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Holidays'),
			{
				timeout: 3000
			}
		);
	});
});
