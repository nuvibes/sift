/*
 * The panel behind the crossed-out eye: why this is hidden, and the way back out.
 *
 * Somebody pressing that glyph is asking what keeps this off their own screen, not who else may see
 * it, so the first claim is a negative one: no account, no grant and no sharing word appears in
 * this panel.
 *
 * The second is the empty list. The names in it are the concealed things, so the server withholds
 * them while Hidden is shut, and an empty answer carries two different facts only the vault can
 * tell apart. Drawing "nothing is" over "not telling you" would assert the library is clear when
 * the panel has not been allowed to look.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import HiddenDialog from './HiddenDialog.svelte';
import type { ShareTarget, VaultSource } from '$lib/library/sharing';

const mocks = vi.hoisted(() => ({
	fetchVaultSources: vi.fn(),
	unhide: vi.fn(),
	changed: vi.fn(),
	show: vi.fn(),
	/* The vault's own store, stood in for so the two readings of an empty list can both be produced.
	   Hoisted with the rest: a `vi.mock` factory runs before anything at the top level of this file,
	   so a plain `const` declared below is still in its temporal dead zone when the factory reads it.
	   `generation` is a plain number rather than a rune: the panel only reads it to know when to
	   ask again, and nothing here moves it. */
	vaultState: { unlocked: false, generation: 0 },
	loadCounts: vi.fn(async (): Promise<Record<string, number>> => ({}))
}));

vi.mock('$lib/library/sharing', async () => {
	const actual =
		await vi.importActual<typeof import('$lib/library/sharing')>('$lib/library/sharing');
	return { ...actual, fetchVaultSources: mocks.fetchVaultSources, unhide: mocks.unhide };
});
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.show } }));
/* Only the signal this file drives is stood in for; the rest of the module comes through as it
   really is. A total stand-in goes stale the moment the module gains an export something under
   test imports, and the failure is this file refusing to load rather than anything it asserts. */
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	libraryChanges: { changed: mocks.changed, subscribe: () => () => {} }
}));

vi.mock('$lib/shell/vault.svelte', () => ({ vault: mocks.vaultState }));
vi.mock('$lib/entity/related.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/entity/related.svelte')>(
		'$lib/entity/related.svelte'
	)),
	loadCounts: mocks.loadCounts
}));

const vaultState = mocks.vaultState;

const TARGET: ShareTarget = { type: 'item', id: 'a1', label: 'holiday.mp4' };

function source(overrides: Partial<VaultSource> = {}): VaultSource {
	return {
		source_type: 'person',
		source_id: 'p1',
		source_name: 'Elina Sorrel',
		here: false,
		...overrides
	};
}

let host: HTMLElement;
let panelInstance: Record<string, unknown> | null = null;

async function open(sources: VaultSource[], target: ShareTarget | null = TARGET) {
	mocks.fetchVaultSources.mockResolvedValue(sources);
	host = document.createElement('div');
	document.body.append(host);
	panelInstance = mount(HiddenDialog, { target: host, props: { open: true, target } });
	flushSync();
	await tick();
	await tick();
	flushSync();
}

/* Portalled to the end of the document, so it is not inside the host it was mounted into. */
function panel(): HTMLElement | null {
	return document.querySelector('.hidden-sheet');
}

function text(): string {
	return (panel()?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function lines(): string[] {
	return [...(panel()?.querySelectorAll('.by li .said') ?? [])].map((each) =>
		(each.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
}

function button(label: string): HTMLButtonElement | undefined {
	return [...(panel()?.querySelectorAll('button') ?? [])].find((each) =>
		each.textContent?.includes(label)
	);
}

beforeEach(() => {
	vaultState.unlocked = false;
	vaultState.generation = 0;
	mocks.fetchVaultSources.mockReset();
	mocks.unhide.mockReset();
	mocks.unhide.mockResolvedValue(undefined);
	mocks.changed.mockReset();
	mocks.show.mockReset();
});

afterEach(() => {
	/* Unmounted rather than removed. `host.remove()` hides the markup and leaves every listener and
	 * effect the component registered, so the next test in this file is answered by the last one's
	 * copy as well as its own. */
	if (panelInstance) void unmount(panelInstance, { outro: false });
	panelInstance = null;
	host?.remove();
	document.body.innerHTML = '';
});

describe('what the panel is about', () => {
	it('says what hiding IS, because a crossed-out eye reads as the opposite', async () => {
		await open([source()]);

		expect(text()).toContain('Hidden is about your own screen');
		expect(text()).toContain('Nobody else is affected');
	});

	it('names the thing it was opened on', async () => {
		await open([source()]);

		expect(text()).toContain('holiday.mp4');
	});

	it('carries no sharing vocabulary at all', async () => {
		/* The whole reason this panel exists rather than the sharing one. A word from the other
		 * panel appearing here means the split has been undone. */
		await open([source()]);

		for (const word of ['Shared', 'Restricted', 'Not shared', 'Apply']) {
			expect(text()).not.toContain(word);
		}
	});

	it('asks nothing at all while it is shut', async () => {
		mocks.fetchVaultSources.mockResolvedValue([source()]);
		host = document.createElement('div');
		document.body.append(host);
		panelInstance = mount(HiddenDialog, {
			target: host,
			props: { open: false, target: TARGET }
		});
		flushSync();
		await tick();

		expect(mocks.fetchVaultSources).not.toHaveBeenCalled();
	});

	it('asks nothing when it has nothing to be about', async () => {
		await open([source()], null);

		expect(mocks.fetchVaultSources).not.toHaveBeenCalled();
	});
});

describe('an empty answer', () => {
	it('reads as "not telling you" while Hidden is shut', async () => {
		/* The server withholds these names with the vault shut, because the names ARE the concealed
		 * things. Reporting that as "nothing is" would be the panel asserting the library is clear
		 * when it was never allowed to look. */
		vaultState.unlocked = false;
		await open([]);

		expect(text()).toContain('Unlock Hidden to see what is concealing this');
		expect(text()).not.toContain('Nothing you hid');
	});

	it('reads as "nothing is" once Hidden is open', async () => {
		vaultState.unlocked = true;
		await open([]);

		expect(text()).toContain('Nothing you hid is concealing this');
		expect(text()).not.toContain('Unlock Hidden');
	});
});

describe('the list of what is doing it', () => {
	it('says what each one is, with the name apart from the sentence', async () => {
		await open([source()]);

		expect(lines()).toEqual(['Hidden by the person Elina Sorrel']);
	});

	it('says so differently when the switch is on the thing itself', async () => {
		await open([source({ source_type: 'item', source_id: 'a1', source_name: null, here: true })]);

		expect(lines()).toEqual(['Hidden on this file itself']);
	});

	it('draws the glyph solid only where the switch is on the thing itself', async () => {
		// The same fill rule the marks on a tile use: solid means change it HERE, hollow means the
		// switch is on something above and this is where to go.
		await open([
			source({ source_id: 'p1', here: false }),
			source({ source_type: 'item', source_id: 'a1', source_name: null, here: true })
		]);

		/*
		 * Read as the class rather than the computed fill axis: the axis is set by a scoped rule
		 * and jsdom applies no stylesheet, so `getComputedStyle` answers the same empty string for
		 * a solid glyph and a hollow one. The browser suite measures the axis itself.
		 */
		const filled = [...(panel()?.querySelectorAll('.by li > .icon') ?? [])].map((glyph) =>
			glyph.classList.contains('filled')
		);
		expect(filled).toEqual([false, true]);
	});

	it('says it could not be read rather than showing an empty list', async () => {
		mocks.fetchVaultSources.mockRejectedValue(new Error('offline'));
		host = document.createElement('div');
		document.body.append(host);
		panelInstance = mount(HiddenDialog, { target: host, props: { open: true, target: TARGET } });
		flushSync();
		await tick();
		await tick();
		flushSync();

		expect(text()).toContain("couldn't be read");
	});
});

describe('taking one back out', () => {
	it('takes only the line that was pressed', async () => {
		await open([
			source({ source_id: 'p1', source_name: 'Elina Sorrel' }),
			source({ source_type: 'tag', source_id: 't1', source_name: 'private' })
		]);
		expect(lines()).toHaveLength(2);

		button('Unhide')?.click();
		await tick();
		await tick();
		flushSync();

		expect(mocks.unhide).toHaveBeenCalledTimes(1);
		expect(mocks.unhide.mock.calls[0][0]).toMatchObject({ source_id: 'p1' });
		expect(lines()).toHaveLength(1);
	});

	it('tells the rest of the app the library moved', async () => {
		// Whatever came back out is on a screen again, and so is the thing this panel is about if
		// that was the last thing hiding it.
		await open([source(), source({ source_type: 'tag', source_id: 't1', source_name: 'p' })]);

		button('Unhide')?.click();
		await tick();
		await tick();
		flushSync();

		expect(mocks.changed).toHaveBeenCalled();
	});

	it('says so rather than dropping the line when the write fails', async () => {
		mocks.unhide.mockRejectedValue(new Error('refused'));
		await open([source(), source({ source_type: 'tag', source_id: 't1', source_name: 'p' })]);

		button('Unhide')?.click();
		await tick();
		await tick();
		flushSync();

		expect(mocks.show).toHaveBeenCalled();
		expect(lines()).toHaveLength(2);
	});
});

/*
 * Hiding a network hides every Site within it, and the heading names only the network. The counts
 * are the route's own shape for a Site: the Sites within it under `sites_within`, `sites` null.
 */
describe('a network', () => {
	it('says how far hiding it reaches, in the words its card says', async () => {
		mocks.loadCounts.mockResolvedValueOnce({ files: 0, sites_within: 2 });
		await open([], { type: 'site', id: 's1', label: 'Northlight Media' });

		expect(mocks.loadCounts).toHaveBeenCalledWith('site', 's1');
		expect(text()).toContain('Includes the 2 Sites within it');
	});
});
