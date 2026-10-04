/*
 * Runs of one creator's loose pictures that look like one sitting, as the queue draws them.
 *
 * Every card must be the same size regardless of how many pictures it holds. The height is a
 * stylesheet rule a document applying no styles cannot read; what can be asserted, and makes the
 * rule hold, is that a big sheet arrives cut with the rest behind a press.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { PagerProps } from '$lib/components/common/Pager.svelte';
import { forgetMeasurements } from '$lib/grid/cards.svelte';

const mocks = vi.hoisted(() => ({
	shoots: vi.fn(),
	openAsset: vi.fn(),
	makeTheSet: vi.fn(),
	replaceState: vi.fn(),
	at: { url: new URL('http://localhost/organize/shoots') }
}));

vi.mock('$lib/player/asset-view', () => ({ openAsset: mocks.openAsset }));

/* The address this wall is on, which the paging reads its place from and writes its place to. */
vi.mock('$app/state', () => ({
	page: {
		get url() {
			return mocks.at.url;
		},
		state: {},
		params: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: mocks.replaceState }));

vi.mock('$lib/entity/shoots.svelte', () => ({
	SHOOTS_PER_PAGE: 20,
	/* The server always says where the page it answered begins; a stand-in answer that does not
	   is given the top, which is what every answer below is about. */
	shoots: async (query: unknown) => ({ offset: 0, ...(await mocks.shoots(query)) }),
	makeTheSet: mocks.makeTheSet,
	nameTheRest: vi.fn(),
	notASet: vi.fn()
}));

import ShootsPanel from './ShootsPanel.svelte';

function shoot(pictures: number, over: Record<string, unknown> = {}) {
	return {
		id: 's-1',
		name: 'Neve Arbogast',
		pictures,
		unnamed: 0,
		/* The server's two sentences, as `queue.asking` writes them for the board's card too. */
		question: `Do these ${pictures} pictures of Neve Arbogast belong together?`,
		detail: `${pictures} pictures of Neve Arbogast that are in no Photo Set`,
		items: Array.from({ length: pictures }, (_one, at) => ({
			id: `asset-${at}`,
			art: null,
			named: true,
			media_type: 'image'
		})),
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	forgetMeasurements();
	mocks.at.url = new URL('http://localhost/organize/shoots');
	mocks.shoots.mockResolvedValue({ shoots: [], total: 0, auto_file: false });
	mocks.makeTheSet.mockResolvedValue({ photo_set_id: 'set-1', pictures: 10, name: 'x' });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn, { outro: false });
	drawn = null;
	host?.remove();
});

async function draw(props: Record<string, unknown> = {}): Promise<void> {
	drawn = mount(ShootsPanel, { target: host, props }) as Record<string, unknown>;
	await settle();
}

async function settle(): Promise<void> {
	for (let round = 0; round < 6; round += 1) {
		flushSync();
		await tick();
	}
	flushSync();
}

/** The stills actually on screen. */
function stills(): number {
	return host.querySelectorAll('.strip img').length;
}

/** A button of the card, by the words on it. */
function press(words: string): void {
	[...host.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.includes(words))
		?.click();
	flushSync();
}

it('cuts a big sheet, and every card opens its shoot on a page of its own', async () => {
	/* The rest of a big sheet does not open in place, growing one card on the wall: every card
	   opens its shoot's own page, the way a Photo Set opens, where every picture is drawn. */
	const { goto } = await import('$app/navigation');
	mocks.shoots.mockResolvedValue({
		shoots: [shoot(30), shoot(3, { id: 's-2' })],
		total: 2,
		auto_file: false
	});

	await draw();

	expect(stills()).toBe(15);
	press('Open all 30 photos');
	expect(vi.mocked(goto)).toHaveBeenCalledWith('/organize/shoots/s-1');

	press('Open this shoot');
	expect(vi.mocked(goto)).toHaveBeenLastCalledWith('/organize/shoots/s-2');
});

it('groups how many are waiting, the way every other count on screen is grouped', async () => {
	mocks.shoots.mockResolvedValue({ shoots: [shoot(3)], total: 12345, auto_file: false });

	await draw();

	expect(host.querySelector('.count')?.textContent).toBe(
		`${(12345).toLocaleString()} shoots waiting`
	);
});

it('shows a small shoot whole, with nothing to press', async () => {
	// A control over a sheet that is already all of it is a press that saves nothing.
	mocks.shoots.mockResolvedValue({ shoots: [shoot(10)], total: 1, auto_file: false });

	await draw();

	expect(stills()).toBe(10);
	expect(host.textContent).not.toContain('more');
});

it('counts the whole shoot rather than what is showing', async () => {
	/* The line under the name is what somebody reads when the sheet is cut, so it has to be about
	   the proposal and not about the strip. */
	mocks.shoots.mockResolvedValue({ shoots: [shoot(30)], total: 1, auto_file: false });

	await draw();

	expect(host.querySelector('.detail')?.textContent).toContain('30 pictures');
});

/*
 * A picture on a card opens the viewer over the queue, on that picture, through `openAsset`, the
 * door every Organize card uses, so closing it returns here. The list is the whole shoot, so Next
 * and Previous walk the sitting past what the card draws.
 */
it('opens the viewer on the picture pressed, with the whole shoot to step through', async () => {
	const one = shoot(14);
	one.items[13] = { id: 'asset-13', art: null, named: true, media_type: 'gif' };
	mocks.shoots.mockResolvedValue({ shoots: [one], total: 1, auto_file: false });

	await draw();

	const presses = [...host.querySelectorAll<HTMLElement>('.strip [aria-label^="Open photo"]')];
	expect(presses).toHaveLength(12);
	presses[2].click();

	expect(mocks.openAsset).toHaveBeenCalledTimes(1);
	const [id, among] = mocks.openAsset.mock.calls[0] as [string, { id: string; runs: boolean }[]];
	expect(id).toBe('asset-2');
	expect(among).toHaveLength(14);
	expect(among[0]).toEqual({ id: 'asset-0', runs: false });
	// A GIF runs in the viewer and a still does not; the server says which.
	expect(among[13]).toEqual({ id: 'asset-13', runs: true });
});

/*
 * The card asks the board's question in the board's words ("Do these 13 pictures of X belong
 * together?" over "13 pictures of X that are in no Photo Set"). Both are the server's sentences,
 * drawn as sent, so the two cards cannot say different things about one shoot.
 */
it('asks the question the board asks, over the line the board draws', async () => {
	mocks.shoots.mockResolvedValue({ shoots: [shoot(13)], total: 1, auto_file: false });

	await draw();

	expect(host.querySelector('h3')?.textContent?.trim()).toBe(
		'Do these 13 pictures of Neve Arbogast belong together?'
	);
	expect(host.querySelector('.detail')?.textContent?.trim()).toBe(
		'13 pictures of Neve Arbogast that are in no Photo Set'
	);
});

/*
 * The person's name in the question is a way to them, and only there: the line under it says the
 * same name and is plain. The server sends who the sentences name; the name is found where it sits
 * and drawn as a link to the person's page, and the rest of the words stay as sent.
 */
it('links the person named in the question, and not in the line under it', async () => {
	const link = { kind: 'person', id: 'person-7', name: 'Neve Arbogast', href: null, gone: false };
	mocks.shoots.mockResolvedValue({
		shoots: [shoot(13, { links: [link] })],
		total: 1,
		auto_file: false
	});

	await draw();

	const named = host.querySelector('h3')?.querySelectorAll<HTMLAnchorElement>('a.named') ?? [];
	expect(named).toHaveLength(1);
	expect(named[0].textContent).toBe('Neve Arbogast');
	expect(named[0].getAttribute('href')).toBe('/people/person-7');
	expect(host.querySelector('.detail')?.querySelectorAll('a')).toHaveLength(0);
	// Every character of both sentences is still there, in order.
	expect(host.querySelector('h3')?.textContent?.trim()).toBe(
		'Do these 13 pictures of Neve Arbogast belong together?'
	);
	expect(host.querySelector('.detail')?.textContent?.trim()).toBe(
		'13 pictures of Neve Arbogast that are in no Photo Set'
	);
});

/*
 * The count is over the wall, not under it, and is the server's total rather than the cards drawn.
 * No "Look again" press is drawn: every scan asks for the pass as it settles.
 */
it('says how many are waiting above the first card, and offers no Look again', async () => {
	mocks.shoots.mockResolvedValue({ shoots: [shoot(10)], total: 5, auto_file: false });

	await draw();

	const count = host.querySelector('.count');
	expect(count?.textContent?.trim()).toBe('5 shoots waiting');
	const wall = host.querySelector('.wall');
	expect(wall && count?.compareDocumentPosition(wall)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
	expect(host.textContent).not.toContain('Look again');
});

/*
 * "Create Photo Set" is a split button. The lead half is the ordinary press and sends no name, so
 * the set takes the proposal's; the chevron's "Create with a name..." opens the rename box holding
 * that name, and what is typed is what the set is called.
 */
it('creates the Photo Set under the proposal name from the lead half', async () => {
	mocks.shoots.mockResolvedValue({ shoots: [shoot(10)], total: 1, auto_file: false });
	await draw();

	const lead = host.querySelector<HTMLButtonElement>('.split .lead button');
	expect(lead?.textContent).toContain('Create Photo Set');
	lead?.click();
	flushSync();

	expect(mocks.makeTheSet).toHaveBeenCalledTimes(1);
	expect(mocks.makeTheSet.mock.calls[0]).toEqual(['s-1']);
});

it('takes a refusal for pictures already in a Photo Set by reading the queue again', async () => {
	/* The card stood while its pictures were filed elsewhere. The server refuses the press and
	   answers the card by that set, so the queue is read again (the card is gone from it) and the
	   server's sentence is said: not the whole-screen problem a broken queue gets. */
	const { ApiError } = await import('$lib/api/client');
	const { toasts } = await import('$lib/shell/toasts.svelte');
	mocks.shoots.mockResolvedValueOnce({ shoots: [shoot(10)], total: 1, auto_file: false });
	mocks.makeTheSet.mockRejectedValueOnce(
		new ApiError(
			409,
			'That conflicts',
			'Those pictures are already in a Photo Set, so nothing new was made.'
		)
	);
	await draw();

	host.querySelector<HTMLButtonElement>('.split .lead button')?.click();
	await settle();

	expect(mocks.shoots).toHaveBeenCalledTimes(2);
	expect(host.querySelector('.strip')).toBeNull();
	expect(host.textContent).not.toContain("couldn't");
	expect(toasts.items.at(-1)?.message).toContain('already in a Photo Set');
});

it('creates it under a typed name from "Create with a name\u2026"', async () => {
	mocks.shoots.mockResolvedValue({ shoots: [shoot(10)], total: 1, auto_file: false });
	await draw();

	/* The library opens on POINTERDOWN, not on click. See `IdentifiedPanel`'s test. */
	const door = host.querySelector<HTMLButtonElement>('.split .trail button');
	door?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
	const row = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((one) =>
		one.textContent?.includes('Create with a name\u2026')
	);
	expect(row).toBeDefined();
	row?.click();
	flushSync();
	await tick();

	const box = document.querySelector<HTMLInputElement>('[role="alertdialog"] input');
	expect(box?.value).toBe('Neve Arbogast');
	if (box) {
		box.value = 'Rooftop sitting';
		box.dispatchEvent(new Event('input', { bubbles: true }));
	}
	flushSync();
	document.querySelector<HTMLButtonElement>('[role="alertdialog"] .confirm')?.click();
	flushSync();
	await tick();

	expect(mocks.makeTheSet).toHaveBeenCalledTimes(1);
	expect(mocks.makeTheSet.mock.calls[0]).toEqual(['s-1', 'Rooftop sitting']);
});

/*
 * The Shoots page pages as every other Organize wall does: the pager goes to the frame's foot
 * through `onpaging`, and the proposal the page starts at goes in the address as `from`, so the way
 * back lands on the page it left, and a queue longer than a page is reachable in full.
 */
const QUEUE = 45;

/** The server, over a queue of `size` proposals named by their place: an offset or a `from`. */
function queueOf(size: number) {
	return async (query: { limit: number; offset?: number; from?: string }) => {
		const offset = query.from === undefined ? Number(query.offset) : Number(query.from.slice(2));
		const count = Math.max(0, Math.min(Number(query.limit), size - offset));
		return {
			shoots: Array.from({ length: count }, (_x, i) =>
				shoot(3, {
					id: `s-${offset + i}`,
					question: `Do these 3 pictures of shoot ${offset + i} belong together?`
				})
			),
			total: size,
			offset,
			auto_file: false
		};
	};
}

it('hands a pager up to the frame when the queue is longer than a page, and turns it', async () => {
	mocks.shoots.mockImplementation(queueOf(QUEUE));
	let pager: PagerProps | null = null;
	await draw({ onpaging: (reported: PagerProps | null) => (pager = reported) });

	expect(pager).not.toBeNull();
	const first = pager as unknown as PagerProps;
	expect(first.total).toBe(QUEUE);
	expect(first.shown).toBe(20);
	expect(first.offset).toBe(0);
	// The count over the wall is still the whole queue's, not the page's.
	expect(host.querySelector('.count')?.textContent?.trim()).toBe(`${QUEUE} shoots waiting`);

	first.onnext();
	await settle();

	expect(mocks.shoots).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
	expect((pager as unknown as PagerProps).offset).toBe(20);
	expect(host.querySelector('h3')?.textContent).toContain('shoot 20 belong');
});

it('keeps its page in the address, and opens there again', async () => {
	mocks.shoots.mockImplementation(queueOf(QUEUE));
	let pager: PagerProps | null = null;
	await draw({ onpaging: (reported: PagerProps | null) => (pager = reported) });
	(pager as unknown as PagerProps).onnext();
	await settle();

	// The first proposal of the page on screen, written into the address it is standing on.
	const written = mocks.replaceState.mock.calls.at(-1)?.[0] as string | undefined;
	expect(written).toBe('/organize/shoots?from=s-20&near=20');

	// Back to it: the page the address names is the page asked for, and drawn.
	unmount(drawn as Record<string, unknown>, { outro: false });
	drawn = null;
	mocks.shoots.mockClear();
	mocks.at.url = new URL('http://localhost/organize/shoots?from=s-20');
	await draw();

	expect(mocks.shoots).toHaveBeenCalledWith({ limit: 20, from: 's-20' });
	expect(host.querySelector('h3')?.textContent).toContain('shoot 20 belong');
});

it('steps back a page when answering the last card empties the one on screen', async () => {
	/* Twenty-one proposals, on the second page, and its one card answered: the page is empty and
	   the queue is not. It says so by showing the page the queue now ends on. */
	let size = 21;
	mocks.shoots.mockImplementation((query) => queueOf(size)(query));
	let pager: PagerProps | null = null;
	await draw({ onpaging: (reported: PagerProps | null) => (pager = reported) });
	(pager as unknown as PagerProps).onnext();
	await settle();
	expect(host.querySelector('h3')?.textContent).toContain('shoot 20 belong');

	size = 20;
	host.querySelector<HTMLButtonElement>('.split .lead button')?.click();
	await settle();

	expect(mocks.shoots).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
	expect(host.textContent).not.toContain('No shoots waiting');
	expect(host.querySelector('h3')?.textContent).toContain('shoot 0 belong');
	// The address follows: it names the first card of the page now on screen.
	expect(mocks.replaceState.mock.calls.at(-1)?.[0]).toBe('/organize/shoots?from=s-0&near=0');
});
