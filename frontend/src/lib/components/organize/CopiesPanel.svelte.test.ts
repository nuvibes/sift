/*
 * Exact copies, as the workbench draws them: which copy is kept, and how that can be changed.
 *
 * A copy in a group can be pressed to mark it as the one kept, as on the near-duplicate tab. What
 * is asserted is the mark: which tile is lit, that a press moves it, and that the page press then
 * keeps what is lit rather than what the rule would have chosen. These are one fact seen from three
 * places, so the screen and the press must not each keep their own copy of it.
 *
 * The store underneath is the real one with its network reads replaced: `copyKeeperOf` is the rule
 * being tested, and a stand-in for it would make the mark a fact about this file rather than about
 * the screen.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Copy, Redundancy } from '$lib/settings-ui/maintenance-state.svelte';

const shared = vi.hoisted(() => ({
	fillReclaim: vi.fn(async (_paging: unknown) => false),
	release: vi.fn(async () => undefined),
	releaseMany: vi.fn(async () => ({ released: 0, refused: 0 })),
	openAsset: vi.fn(),
	built: [] as unknown[],
	arrange: (_one: unknown) => {}
}));

vi.mock('$lib/settings-ui/maintenance-state.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/settings-ui/maintenance-state.svelte')>();
	/* Extended rather than replaced: everything this test is about (`copyKeeperOf`, `chooseCopy`,
	   and the state they read) stays the real implementation, and only the three methods that
	   would reach the network are stood in for. */
	class Fake extends real.Maintenance {
		constructor() {
			super();
			shared.arrange(this);
			shared.built.push(this);
		}
		fillReclaim = shared.fillReclaim;
		release = shared.release;
		releaseMany = shared.releaseMany;
	}
	return { ...real, Maintenance: Fake };
});

vi.mock('$lib/organize/organize.svelte', () => ({ answered: { changed: vi.fn() } }));
vi.mock('$lib/player/asset-view', () => ({ openAsset: shared.openAsset }));

import CopiesPanel from './CopiesPanel.svelte';

type Store = import('$lib/settings-ui/maintenance-state.svelte').Maintenance;

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;
let store: Store;

function copy(id: string, bytes: number): Copy {
	return {
		location_id: id,
		root_id: 'root',
		rel_path: `${id}.mp4`,
		filename: `${id}.mp4`,
		size_bytes: bytes,
		path: `Videos/${id}.mp4`
	};
}

function asset(over: Partial<Redundancy> = {}): Redundancy {
	return {
		asset_id: 'asset-a',
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 1000,
		copies: [copy('here', 2000), copy('there', 1000)],
		reclaimable_bytes: 1000,
		art: null,
		...over
	};
}

function draw(arrange: (one: Store) => void): HTMLElement {
	shared.arrange = arrange as (one: unknown) => void;
	shared.built.length = 0;
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(CopiesPanel, { target: host }) as Record<string, unknown>;
	flushSync();
	store = shared.built[0] as Store;
	return host;
}

/** The buttons of one tile, by the words on them. */
function pressed(words: string, at = 0): void {
	const found = [...host.querySelectorAll<HTMLButtonElement>('.file button')].filter((one) =>
		one.textContent?.includes(words)
	);
	found[at]?.click();
	flushSync();
}

/** Which tile is lit, by the copy it is a tile of. */
function lit(): string[] {
	return [...host.querySelectorAll('.file.keeping .name')].map((one) => one.textContent ?? '');
}

beforeEach(() => {
	shared.fillReclaim.mockClear();
	shared.releaseMany.mockClear();
});

afterEach(() => {
	if (mounted) unmount(mounted, { outro: false });
	mounted = null;
	host?.remove();
});

it('lights the largest copy when nobody has said otherwise', () => {
	/* The copy kept by doing nothing, and it is the one `reclaimable_bytes` counts everything else
	   against, so a screen that says how much removing the extras frees has already chosen one and
	   must say which. */
	draw((one) => {
		one.redundancies = [asset()];
		one.totalRedundant = 1;
		one.loaded = true;
	});

	expect(lit()).toEqual(['here.mp4']);
});

it('moves the mark when a different copy is chosen, and writes nothing', () => {
	/*
	 * Pressing a copy moves which one is kept. It still writes nothing: the page press is what
	 * writes, as on the near-duplicate tab.
	 */
	draw((one) => {
		one.redundancies = [asset()];
		one.totalRedundant = 1;
		one.loaded = true;
	});

	pressed('Keep this instead');

	expect(lit()).toEqual(['there.mp4']);
	expect(shared.release).not.toHaveBeenCalled();
	expect(shared.releaseMany).not.toHaveBeenCalled();
});

it('and the page press then keeps what is lit rather than what the rule chose', async () => {
	/* The half that makes the mark mean anything. If the screen and the press each worked the
	   keeper out for themselves, a mark that moved on screen and a press that did not follow it
	   would be two answers to one question. */
	draw((one) => {
		one.redundancies = [asset()];
		one.totalRedundant = 1;
		one.loaded = true;
	});

	pressed('Keep this instead');
	[...host.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.includes('Delete the 1 extra'))
		?.click();
	flushSync();
	// The dialog is portalled, so its confirm is looked for in the document rather than in the host.
	[...document.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.trim() === 'Delete 1 copy')
		?.click();
	await Promise.resolve();
	flushSync();

	expect(shared.releaseMany).toHaveBeenCalledWith([{ asset_id: 'asset-a', location_id: 'here' }]);
});

it('names each group so a still on the board can point at it', () => {
	/* A row here has no address of its own (it is one asset in several places on a paged list)
	   so the board's still carries the page and this name. `_copy_anchor` writes the other half, and
	   the two have to spell it the same way or the link lands at the top of the page. */
	draw((one) => {
		one.redundancies = [asset()];
		one.totalRedundant = 1;
		one.loaded = true;
	});

	expect(host.querySelector('.group')?.id).toBe('copy-asset-a');
	expect(store.copyKeeperOf(asset())).toBe('here');
});

it('asks for the still by the address that names it, so a second visit asks nothing', () => {
	/* A bare `/thumb` is answered the careful way: one conditional request per tile on every
	   visit. With the token the browser keeps it. */
	const shown = draw((one) => {
		one.redundancies = [asset({ art: 'tok1' })];
		one.totalRedundant = 1;
		one.loaded = true;
	});

	expect(shown.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-a/thumb?v=tok1');
});

it('draws where each copy is as the server says it, never the path inside the library', () => {
	/* The server says a folder Hidden hides as "..."; the path inside the library still names it,
	   so drawing that in its place, or when the server sent none, would print the folder. */
	const hidden: Copy = { ...copy('here', 2000), rel_path: 'Secret/here.mp4', path: null };
	draw((one) => {
		one.redundancies = [asset({ copies: [hidden, copy('there', 1000)] })];
		one.totalRedundant = 1;
		one.loaded = true;
	});

	const where = [...host.querySelectorAll('.where')].map((one) => one.textContent ?? '');
	expect(where).toEqual(['here.mp4', 'Videos/there.mp4']);
	expect(host.textContent).not.toContain('Secret');
});

/* The presses stand at the tile's foot, so the copies of one file keep them on one line whatever
   their names took above. */
it("puts a copy's presses at the foot of its tile, with its name free to wrap between words", async () => {
	draw((one) => {
		one.redundancies = [asset()];
		one.totalRedundant = 1;
		one.loaded = true;
	});
	expect(host.querySelector('.file .name wbr')).not.toBeNull();
	const source = (await import('./CopiesPanel.svelte?raw')).default;
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const acts = host.querySelector('.file .acts') as HTMLElement;
	applyStyles(source, acts);
	expect(getComputedStyle(acts).marginBlockStart).toBe('auto');
	// The keeping mark is a control's height, so its words share the keep press's line.
	const kept = host.querySelector('.file .kept') as HTMLElement;
	expect(getComputedStyle(kept).getPropertyValue('min-block-size')).toBe('var(--control-height)');
	removeStyles();
});
