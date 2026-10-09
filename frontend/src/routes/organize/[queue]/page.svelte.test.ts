/* One queue's screen: the board it stands on, and what it draws when the board has no such queue.
 *
 * It looks the queue up by name and renders whatever the panel registry says draws it, and it
 * says so plainly when there is none. The queue's history and its Undo are Settings > History's
 * (`Ledger.svelte.test.ts`).
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { words } from '$lib/design/testing.svelte';
import { flushSync, mount, unmount } from 'svelte';

import Queue from './+page.svelte';

const mocks = vi.hoisted(() => ({
	board: vi.fn(),
	held: { found: null } as { found: unknown },
	/* Replaced by the factory below, which is the only place state the component can subscribe to
	   can be declared. The address has to be REACTIVE here rather than a plain object: the tabs
	   move between the queues of a group WITHOUT remounting this screen. */
	at: { params: { queue: 'folders' } } as { params: { queue: string } },
	/* Which component the registry hands back. A hairline by default; the tab-line tests swap in a
	   panel that reports a control. */
	panel: null as unknown,
	/* What the screen asked for when it asked again for some queues, and what it runs when the
	   library moves. */
	only: [] as string[][],
	onLibrary: null as (() => void) | null
}));

vi.mock('$app/state', async () => {
	const { reactiveProps } = await import('$lib/design/testing.svelte');
	const at = reactiveProps<{ params: { queue: string } }>({ params: { queue: 'folders' } });
	mocks.at = at;
	return {
		page: {
			get params() {
				return at.params;
			},
			/* No search words: the lit tab's count is the board's. */
			get url() {
				return new URL(`http://sift.test/organize/${at.params.queue}`);
			}
		}
	};
});

vi.mock('$lib/organize/organize.svelte', async (importOriginal) => {
	/* Built ON the real module rather than instead of it, so an export nobody here thought about is
	   still the real one rather than absent. The failure that shape produces is the whole file
	   refusing to mount, naming neither the export nor the double. */
	const real = await importOriginal<Record<string, unknown>>();
	const { reactiveProps } = await import('$lib/design/testing.svelte');
	const held = reactiveProps<{ found: unknown }>({ found: null });
	mocks.held = held;
	return {
		...real,
		/* The real store's two halves, kept to their real rule: `refresh` publishes its ask and never
		   joins one, `ensure` joins whatever is in flight. Without that rule here, a screen asking
		   twice for one board could not be told apart from one asking once. */
		heldBoard: {
			asking: null as Promise<unknown> | null,
			get found() {
				return held.found;
			},
			async refresh() {
				const asking = Promise.resolve(mocks.board()).then((answer) => {
					held.found = answer;
					return answer;
				});
				this.asking = asking;
				try {
					return await asking;
				} finally {
					if (this.asking === asking) this.asking = null;
				}
			},
			async ensure() {
				if (held.found !== null) return;
				await (this.asking ?? this.refresh());
			},
			async refreshOnly(names: string[]) {
				mocks.only.push(names);
				const answer = await Promise.resolve(mocks.board());
				held.found = answer;
				return answer;
			}
		}
	};
});

vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: (reload: () => void) => {
		mocks.onLibrary = reload;
	}
}));

/* A panel that draws a hairline and nothing else. A real one would drag the feature behind it into
   every assertion here. `Separator` is borrowed rather than a harness written, because what this
   needs is any component that mounts. The extra prop it is handed is ignored. */
vi.mock('$lib/organize/panels', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	const Separator = (await import('$lib/components/common/Separator.svelte')).default;
	return { ...real, panelFor: () => mocks.panel ?? Separator };
});

let host: HTMLElement;
/* The mounted screen, kept so it can be TAKEN DOWN again.
 *
 * REMOVING THE HOST ELEMENT IS NOT UNMOUNTING. A component mounted and never unmounted keeps its
 * effects alive for the rest of the file, so an announcement in a later test wakes every screen
 * the earlier ones left running.
 */
let screen: Record<string, unknown> | undefined;

function queue(over: Record<string, unknown> = {}) {
	return {
		name: 'folders',
		title: 'Folders',
		decision: 'Say who a folder is.',
		verb: 'folders to answer',
		verb_one: 'folder to answer',
		icon: 'folder',
		count: 1,
		band: 'decision',
		group: null,
		group_title: null,
		pending: true,
		preview: [],
		...over
	};
}

async function render() {
	host = document.createElement('div');
	document.body.append(host);
	screen = mount(Queue, { target: host });
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
	flushSync();
}

beforeEach(() => {
	mocks.panel = null;
	mocks.at.params = { queue: 'folders' };
	mocks.held.found = null;
	mocks.board.mockReset();
	mocks.only = [];
	mocks.onLibrary = null;
	mocks.board.mockResolvedValue({ queues: [queue()], decisions: [], decided: 0 });
});

afterEach(() => {
	if (screen) void unmount(screen);
	screen = undefined;
	host?.remove();
	vi.clearAllMocks();
});

it('asks the board and draws the panel the registry says belongs to this queue', async () => {
	/* Not a COUNT of the reads: the effect that loads the board also re-runs when the board it
	   loaded changes, which is the shape the announcement bus gives it and not a fault. What is
	   asserted is that the board was asked for and the queue's own panel is what came out. */
	await render();

	expect(mocks.board).toHaveBeenCalled();
	expect(host.querySelector('.separator')).not.toBeNull();
});

it('says plainly that there is no queue of that name, rather than drawing an error page', async () => {
	/* Not a fault: the feature behind it is switched off, or it belongs to a version this one is
	   not. Either way there is a way back rather than a stack trace. */
	mocks.at.params = { queue: 'nowhere' };

	await render();

	expect(host.textContent).toContain('No queue has that name');
	expect(host.textContent).toContain('Nothing in Organize has that name.');
	expect(host.querySelector('.separator')).toBeNull();
});

it("draws the queue's own sentence under its title, and the advice after it", async () => {
	/* The board card carries this sentence only as its title's tooltip, which a touch screen and a
	   screen reader never show, so the queue's own page is where it has to be on the screen. */
	mocks.board.mockResolvedValue({
		queues: [queue({ advice: 'Work from the top.' })],
		decisions: [],
		decided: 0
	});

	await render();

	expect(host.textContent).toContain('Say who a folder is. Work from the top.');
});

it('draws work waiting elsewhere as a line of its own under the lede, with its link', async () => {
	/* The IDs waiting for a username are a thing to do on another screen, not a way through this
	   one, so they are their own line with the one place they are done. */
	mocks.board.mockResolvedValue({
		queues: [
			queue({
				advice: 'Work from the top.',
				aside: { said: '6 IDs are waiting for a username.', link: 'Open Sites', href: '/sites' }
			})
		],
		decisions: [],
		decided: 0
	});

	await render();

	const aside = host.querySelector('.lede .aside');
	expect(aside?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
		'6 IDs are waiting for a username. Open Sites'
	);
	expect(aside?.querySelector('a')?.getAttribute('href')).toBe('/sites');
	expect(host.querySelector('.lede')?.textContent).toContain(
		'Say who a folder is. Work from the top.'
	);
});

it("titles the page with its group's name, and puts the tabs and the sentence under it", async () => {
	/* Every Organize screen is titled like every other page, not only a trail and tabs. A queue
	   that is one tab of a page takes the page's name, the word the board's row and the trail
	   already use. */
	mocks.at.params = { queue: 'faces' };
	mocks.board.mockResolvedValue({
		queues: [
			queue({
				name: 'faces',
				title: 'Faces to confirm',
				group: 'faces',
				group_title: 'Faces',
				icon: 'person'
			}),
			queue({ name: 'faces-to-name', title: 'Unnamed faces', group: 'faces' })
		],
		decisions: [],
		decided: 0
	});

	await render();

	const heading = host.querySelector('.page-header h1');
	expect(words(heading)).toBe('Faces');
	expect(heading?.classList.contains('clipped')).toBe(false);
	// The tabs follow the header, and the lit tab's sentence follows the tabs.
	const line = host.querySelector('.tab-line');
	expect(line?.previousElementSibling?.classList.contains('page-header')).toBe(true);
	expect(line?.nextElementSibling?.textContent).toContain('Say who a folder is.');
});

it('titles a queue standing alone with its own name', async () => {
	await render();

	expect(words(host.querySelector('.page-header h1'))).toBe('Folders');
	expect(host.querySelector('.tab-line')).toBeNull();
});

it('draws nothing of the work above the heading', async () => {
	/* No record above the work: its thread is in Settings > History. Pinned so that a screen
	   quietly growing a second record above the work is a failure rather than a silence. */
	await render();

	expect(host.textContent).not.toContain('What happened here');
	expect(host.textContent).not.toContain('All activity');
});

/* ONE SLOT FOR TAB-LINE CONTROLS. A panel reports its controls for the whole tab through
   `ontools`, exactly as it reports its pager through `onpaging`, and the route draws them at the
   far end of the tab line (`OrganizeHeader.controls`). */
it("draws a panel's reported controls at the far end of the tab line", async () => {
	mocks.panel = (await import('./ToolsProbe.test.svelte')).default;

	await render();

	const controls = host.querySelector('.page-header .controls');
	expect(controls?.querySelector('.probe-tool')?.textContent?.trim()).toBe('Probe tool');
	// And in the header's slot, not in the body with the work.
	expect(host.querySelector('.probe-body')?.closest('.page-header')).toBeNull();
});

/* A pager steps through rows. Under a page with none it is a control for nothing. */
it('draws no pager under a page with nothing on it, and one under a page with rows', async () => {
	mocks.panel = (await import('./PagerProbe.test.svelte')).default;

	document.body.dataset.probeTotal = '0';
	await render();
	expect(host.querySelector('.frame-footer')).toBeNull();
	void unmount(screen!);
	screen = undefined;
	host.remove();

	document.body.dataset.probeTotal = '40';
	await render();
	expect(host.querySelector('.frame-footer')).not.toBeNull();
	delete document.body.dataset.probeTotal;
});

it('draws nothing there for a panel that never reports', async () => {
	/* Most panels have no control for the whole tab, and they are not made to say so. */
	await render();

	expect(host.querySelector('.page-header .controls')?.children.length).toBe(0);
});

it('asks for the board ONCE on arrival, however many parts of the screen want it', async () => {
	/* The header asks for the board to name the queue and this screen asks for its counts. A
	   first read from an effect runs after the child header's, and `refresh` never joins, so
	   every arrival would make two surveys of every queue, served one after the other. */
	await render();

	expect(mocks.board).toHaveBeenCalledTimes(1);
});

it('draws the panel before the board has answered, so its own list is not kept waiting', async () => {
	/* The panel fetches its own rows. Gated on the board, every thumbnail on the screen would wait
	   on a survey of every queue before its own list was even asked for. */
	mocks.board.mockReturnValue(new Promise(() => {}));

	await render();

	expect(host.querySelector('.separator')).not.toBeNull();
});

it('asks again when something is decided, and only then', async () => {
	const { answered } = await import('$lib/organize/organize.svelte');
	await render();
	expect(mocks.board).toHaveBeenCalledTimes(1);

	answered.changed();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();

	expect(mocks.board).toHaveBeenCalledTimes(2);
});

it('asks only for its own queue and the tabs beside it on arrival when a board is held', async () => {
	/* Arriving from Organize, the board was just drawn: asking for every queue again on the way in
	   would survey the whole board for a screen that draws two of them. */
	mocks.at.params = { queue: 'duplicates' };
	const board = {
		queues: [
			queue(),
			queue({ name: 'duplicates', title: 'Near duplicates', group: 'alike' }),
			queue({ name: 'copies', title: 'Copies', group: 'alike' })
		],
		decisions: [],
		decided: 0
	};
	mocks.held.found = board;
	mocks.board.mockResolvedValue(board);

	await render();

	expect(mocks.only).toEqual([['duplicates', 'copies']]);
	expect(mocks.board).toHaveBeenCalledTimes(1);
});

it('asks again for its own queue and the tabs beside it when the library moves, not every queue', async () => {
	/* A survey of every queue costs what the slowest one costs, too much to ask on every change of
	   the library for a screen that draws two queues. */
	mocks.at.params = { queue: 'duplicates' };
	mocks.board.mockResolvedValue({
		queues: [
			queue(),
			queue({ name: 'duplicates', title: 'Near duplicates', group: 'alike' }),
			queue({ name: 'copies', title: 'Copies', group: 'alike' })
		],
		decisions: [],
		decided: 0
	});
	await render();
	expect(mocks.board).toHaveBeenCalledTimes(1);

	mocks.onLibrary?.();
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();

	expect(mocks.only).toEqual([['duplicates', 'copies']]);
});
