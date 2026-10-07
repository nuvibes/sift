/* Adding a folder, from either screen that offers it.
 *
 * The component is shared by Settings and the empty Browse wall, so what is held here is the part
 * both depend on: the desktop's one gesture hands the folder over AND adds it, with the reading
 * decided by the screen (`scan`), and a browser opens the picker instead. The `scan` case is the
 * one that matters most: a `false` passed for any other meaning is a folder added from Settings
 * and never read.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const choose = vi.fn<() => Promise<string | null>>();
vi.mock('$lib/bridge', () => ({ bridge: { chooseFolder: () => choose() } }));

import AddFolder from './AddFolder.svelte';
import type { Library } from './library.svelte';
import type { Grants } from './grants-state.svelte';
import type { Picker } from './picker.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	document.body.innerHTML = '';
	choose.mockReset();
});

function doubles(canAdd: boolean) {
	const library = { busy: false, addRoot: vi.fn().mockResolvedValue(null) };
	const grants = { canAdd, ensure: vi.fn().mockResolvedValue(undefined) };
	const picker = {
		open: vi.fn().mockResolvedValue(undefined),
		look: vi.fn(),
		scope: 'granted',
		selected: null,
		atTopLevel: true,
		writable: true,
		readOnlyMount: false,
		entries: [],
		trail: [],
		loading: false,
		failed: null,
		canGoUp: false
	};
	return { library, grants, picker };
}

function render(canAdd: boolean, scan?: boolean, offer?: boolean) {
	const made = doubles(canAdd);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AddFolder, {
		target: host,
		props: {
			library: made.library as unknown as Library,
			grants: made.grants as unknown as Grants,
			picker: made.picker as unknown as Picker,
			...(scan === undefined ? {} : { scan }),
			...(offer === undefined ? {} : { offer })
		}
	});
	flushSync();
	return made;
}

function press(label: string) {
	const found = [...document.querySelectorAll('button')].find((one) =>
		one.textContent?.includes(label)
	);
	expect(found, label).toBeDefined();
	found!.click();
}

async function settle() {
	for (let i = 0; i < 5; i += 1) await tick();
	flushSync();
}

describe('on the desktop', () => {
	it('hands the chosen folder over and adds it, read immediately by default', async () => {
		choose.mockResolvedValue('D:\\media');
		const { library, grants } = render(true);

		press('Add a folder');
		await settle();

		expect(grants.ensure).toHaveBeenCalledWith('D:\\media');
		expect(library.addRoot).toHaveBeenCalledWith('D:\\media', true);
	});

	it('adds without reading where the screen says so: the empty Browse wall', async () => {
		choose.mockResolvedValue('D:\\media');
		const { library } = render(true, false);

		press('Add a folder');
		await settle();

		expect(library.addRoot).toHaveBeenCalledWith('D:\\media', false);
	});

	it('does nothing when the dialog is closed without choosing', async () => {
		choose.mockResolvedValue(null);
		const { library, grants } = render(true);

		press('Add a folder');
		await settle();

		expect(grants.ensure).not.toHaveBeenCalled();
		expect(library.addRoot).not.toHaveBeenCalled();
	});
});

describe('in a browser', () => {
	it('opens the picker instead of a dialog it cannot open', async () => {
		const { picker } = render(false);

		press('Add a folder');
		await settle();

		expect(choose).not.toHaveBeenCalled();
		expect(picker.open).toHaveBeenCalled();
		expect(document.body.textContent).toContain('Folders Sift already has');
	});

	it('names the list it opens on, with the way to the whole computer beside it', async () => {
		render(false);
		press('Add a folder');
		await settle();

		const head = document.querySelector('.list-head');
		expect(head?.querySelector('#picker-label')?.textContent).toBe('Folders Sift already has');
		expect(head?.querySelector('button')?.textContent?.trim()).toBe('Browse this device');
	});

	it('warns about the list only once Add is pressed on it, and adds nothing', async () => {
		const { library } = render(false);
		press('Add a folder');
		await settle();
		const warning = () => document.querySelector('.warn');
		expect(warning()).toBeNull();

		const add = [...document.querySelectorAll<HTMLButtonElement>('button[type="submit"]')][0];
		expect(add.disabled).toBe(false);
		add.click();
		await settle();

		expect(warning()?.textContent).toContain('not a folder itself');
		expect(library.addRoot).not.toHaveBeenCalled();
	});

	/* The picker stands where its last listing put it until the next one answers, so an Add
	   pressed while a folder is opening would add the folder above it. */
	const INSIDE = { atTopLevel: false, selected: { name: 'Holiday', path: 'D:\\media\\Holiday' } };

	async function submitWith(picker: Record<string, unknown>, state: Record<string, unknown>) {
		Object.assign(picker, INSIDE, state);
		press('Add a folder');
		await settle();
		const add = [...document.querySelectorAll<HTMLButtonElement>('button[type="submit"]')][0];
		const was = add.disabled;
		add.form!.dispatchEvent(new SubmitEvent('submit', { cancelable: true, bubbles: true }));
		await settle();
		return was;
	}

	it('adds the folder the picker stands in', async () => {
		const { library, picker } = render(false);

		const off = await submitWith(picker, { loading: false });

		expect(off).toBe(false);
		expect(library.addRoot).toHaveBeenCalledWith('D:\\media\\Holiday', true);
	});

	it('and adds nothing while a pressed folder is still opening', async () => {
		const { library, picker } = render(false);

		const off = await submitWith(picker, { loading: true });

		expect(off, 'Add folder is off until the folder has opened').toBe(true);
		expect(library.addRoot).not.toHaveBeenCalled();
	});
});

/* The empty Browse wall draws this under the glyph and the sentence `Empty` centres, so there it
   is one centred column (the name, the sentence, the button) and never a pane's two-column row,
   which stretches across the wall and puts the button a screen's width from its words. */
describe('where it stands', () => {
	it('is one column on the empty wall: the name, the sentence, then the button', () => {
		render(true, false, true);
		expect(host.querySelector('.row'), 'a pane row on the wall').toBeNull();
		const offer = host.querySelector('.offer');
		expect(offer).not.toBeNull();
		const parts = [...(offer?.children ?? [])].map((one) =>
			one.tagName === 'BUTTON' ? 'button' : one.textContent?.trim()
		);
		expect(parts).toEqual([
			'Add a folder',
			'Its files are imported, and new ones as they arrive.',
			'button'
		]);
	});

	it('is a pane row in Settings, the name beside the button', () => {
		render(true);
		expect(host.querySelector('.offer')).toBeNull();
		expect(host.querySelector('#library\\.add.row')).not.toBeNull();
	});
});
