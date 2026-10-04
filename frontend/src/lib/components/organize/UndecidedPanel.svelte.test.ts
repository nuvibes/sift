/*
 * The names the unattended enrichment could not choose for.
 *
 * A stash-box holding two certain entries for a name is a judgement about which one is this person,
 * and the enrichment declines it. This panel is the list of those declines, which would otherwise
 * exist only as a number in a job's note ("24 had more than one match").
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({ get: vi.fn(), search: vi.fn(), linksOf: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get }
}));

/* The chooser is the REAL sheet; only what it would ask the network is stood in for. */
vi.mock('$lib/entity/enrich.svelte', async (importActual) => ({
	...(await importActual<typeof import('$lib/entity/enrich.svelte')>()),
	search: mocks.search,
	linksOf: mocks.linksOf
}));

import UndecidedPanel from './UndecidedPanel.svelte';

function undecided(over: Record<string, unknown> = {}) {
	return {
		subject: 'person',
		id: 'p-1',
		name: 'Neve Arbogast',
		candidates: 2,
		seen_at: 0,
		...over
	};
}

function page(over: Record<string, unknown> = {}) {
	return { items: [undecided()], total: 1, ...over };
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

/* One answer per address, so the list, the record read and the sheet's box list each get their own:
   a mock that answered every GET with the list would hand the sheet a list as a record. */
function answering(list: unknown = page()): void {
	mocks.get.mockImplementation(async (path: string) => {
		if (path === '/stash-boxes/undecided') return list;
		if (path.startsWith('/stash-boxes/record/')) return { values: { birth_date: '1991-07-09' } };
		if (path === '/stash-boxes') return { boxes: [] };
		throw new Error(`unexpected ${path}`);
	});
}

beforeEach(() => {
	vi.clearAllMocks();
	answering();
	mocks.search.mockResolvedValue([]);
	mocks.linksOf.mockResolvedValue([]);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function draw(props: Record<string, unknown> = {}): Promise<void> {
	drawn = mount(UndecidedPanel, { target: host, props }) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

it('lists the name and how many entries it could not choose between', async () => {
	await draw();

	expect(words(host)).toContain('Neve Arbogast');
	expect(words(host)).toContain('2 entries, open to choose');
});

it('says where the choosing is done', async () => {
	await draw();

	expect(words(host)).toContain('open to choose');
});

it('opens the chooser ON THIS PAGE, with what Sift holds read first, and never leaves', async () => {
	/*
	 * The row opens the chooser over the queue rather than leaving the page: it is a button, not an
	 * address, so nothing here has an href to follow.
	 */
	await draw();

	expect(host.querySelector('a[href*="enrich=1"]')).toBeNull();
	const row = [...host.querySelectorAll('button')].find(
		(one) => one.getAttribute('aria-label') === "Choose Neve Arbogast's entry"
	);
	expect(row).toBeTruthy();

	row?.click();
	for (let turn = 0; turn < 6; turn += 1) {
		await Promise.resolve();
		flushSync();
	}
	await tick();

	expect(mocks.get).toHaveBeenCalledWith('/stash-boxes/record/person/p-1');
	expect(document.body.textContent).toContain('Look up in a stash-box');
	// And it asked about the row's own name, as the record page's Enrich does.
	expect(mocks.search).toHaveBeenCalledWith('person', 'Neve Arbogast', 'p-1');
});

it('names the kind of thing each row is, for anybody who cannot see the glyph', async () => {
	answering(page({ items: [undecided({ subject: 'tag', id: 't-1' })] }));

	await draw();

	expect(host.querySelector('.kind')?.getAttribute('aria-label')).toBe('Tag');
});

it('says nothing is waiting rather than drawing an empty list', async () => {
	answering(page({ items: [], total: 0 }));

	await draw();

	expect(words(host)).toContain('Nothing to choose');
	expect(words(host)).toContain('appears here until you choose the right one');
});

it('publishes a pager to the frame and takes it back down', async () => {
	const onpaging = vi.fn();
	answering(page({ total: 90 }));

	await draw({ onpaging });

	expect(onpaging.mock.calls.at(-1)?.[0]?.total).toBe(90);

	if (drawn) unmount(drawn);
	drawn = null;
	expect(onpaging).toHaveBeenLastCalledWith(null);
});
