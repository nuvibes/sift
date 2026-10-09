/*
 * Usernames nobody has said who they belong to.
 *
 * A wall of cards over one narrow question, and the question is in the request: this panel asks for
 * usernames with nobody behind them. Asking for all of them would draw a queue that can never be
 * emptied.
 *
 * The question is answered on the card through the same people picker Unnamed faces's "Add as
 * person" opens; the card never links to `/accounts/<id>`.
 *
 * The rest worth holding is the card's own reading: a username is a name on a site, so the picture
 * is fetched under the handle rather than the site, whose mark would draw every username on one
 * site identically.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	list: vi.fn(),
	attach: vi.fn(),
	create: vi.fn(),
	goto: vi.fn(),
	replaceState: vi.fn(),
	toast: vi.fn(),
	at: { url: new URL('http://localhost/organize/usernames') }
}));

/* Invented for this file. Nobody real, which is the rule for a fixture in this repo. */
const EVERYBODY = [
	{ id: 'p-1', name: 'Neve' },
	{ id: 'p-2', name: 'Neve Arbor' }
];

/* The server says where a page began (`offset`); a page asked for by offset begins there, so the
   stand-in answers that unless a test's own answer says otherwise. The anchored arrival is
   `UsernamePanel.anchored.test.ts`. */
vi.mock('$lib/people/usernames.svelte', () => ({
	usernames: {
		list: async (query: { offset?: number }) => ({
			offset: query.offset ?? 0,
			...(await mocks.list(query))
		}),
		attach: mocks.attach
	}
}));
/* One PAGE of people for the picker, filtered by what is typed: the server's own answer, the same
   shape the Unnamed faces picker's test hands its `PickMenu`. */
vi.mock('$lib/people/people.svelte', () => ({
	people: {
		choices: async (typed: string) => {
			const needle = (typed ?? '').trim().toLowerCase();
			const items = EVERYBODY.filter((one) => one.name.toLowerCase().includes(needle));
			return { items, total: items.length };
		},
		create: (name: string) => mocks.create(name)
	}
}));
vi.mock('$app/navigation', () => ({ goto: mocks.goto, replaceState: mocks.replaceState }));
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
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.toast } }));
vi.mock('$lib/search/search.svelte', () => ({ suggestionsFor: vi.fn(async () => []) }));
/* The picker asks for the live names of its remembered rows; none here. */
vi.mock('$lib/entity/names-now.svelte', () => ({
	askNames: vi.fn(async () => undefined),
	nameNow: () => undefined
}));

import type { PagerProps } from '$lib/components/common/Pager.svelte';
import UsernamePanel from './UsernamePanel.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the card art and the interface settings, read and kept on the way past. */
noServerAt('/api/creator-art', '/api/settings/interface');

function username(over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		id: 'a-1',
		username: 'esmewrenfield',
		display_name: null,
		asset_count: 3,
		site_name: 'SomeSite',
		person_id: null,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.list.mockResolvedValue({ items: [], total: 0 });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function press(words: string): void {
	[...host.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.includes(words))
		?.click();
	flushSync();
}

async function settle(): Promise<void> {
	for (let at = 0; at < 6; at++) await tick();
	flushSync();
}

async function draw(): Promise<void> {
	drawn = mount(UsernamePanel, { target: host }) as Record<string, unknown>;
	// Settled rather than two ticks: the page is read through the wall's paging, which is a step
	// more between the request and the rows than a bare call.
	await settle();
}

it('asks only for the usernames nobody has answered for', async () => {
	// The whole panel is this filter. Without it the queue holds every username in the library,
	// including the ones already decided, and it can never be worked to the bottom.
	await draw();

	expect(mocks.list).toHaveBeenCalledWith({ unattached: true, limit: 60, offset: 0 });
});

it("draws its wall inside the queue's own frame: no second title, no second scrolling box", async () => {
	// The route draws the trail, the title, the tabs and the pager. A wall that drew its own frame
	// inside that one would put a second title under the first and a scrolling box that never
	// scrolled, grown to the height of every card.
	mocks.list.mockResolvedValue({ items: [username()], total: 1 });

	await draw();

	expect(host.querySelector('h1')).toBeNull();
	expect(host.querySelector('[data-scroll-area-viewport]')).toBeNull();
	expect(host.querySelector('.wall')).not.toBeNull();
});

it("draws one card per username, opening the username's files and never a page of its own", async () => {
	// A username is stored and is not a place: the picture and the name open the Files wall
	// filtered to it, and no link on the card reaches `/accounts/<id>`.
	mocks.list.mockResolvedValue({ items: [username()], total: 1 });

	await draw();

	expect(host.querySelector('a[href="/browse?username=a-1"]')).not.toBeNull();
	expect(host.querySelector('a[href^="/accounts/"]')).toBeNull();
	expect(host.textContent).toContain('esmewrenfield');
});

it('opens the files from the card, under their own name', async () => {
	mocks.list.mockResolvedValue({ items: [username()], total: 1 });

	await draw();
	press('Open the files');

	expect(mocks.goto).toHaveBeenCalledWith('/browse?username=a-1');
});

it('says the ID on the card when it is known', async () => {
	mocks.list.mockResolvedValue({ items: [username({ number: '31620508417' })], total: 1 });

	await draw();

	// "ID", the one word for a Site's number for a username.
	expect(host.textContent).toContain('ID 31620508417');
});

/*
 * "Who is this?" opens the same picker Unnamed faces's "Add as person" opens.
 *
 * What is asserted is what this card is responsible for: the picker is behind its own button,
 * picking somebody joins the username to them, and creating somebody makes them and then joins. The
 * list's own behaviour is `PickMenu`'s suite. The list is portalled to the end of the document, so
 * its rows are found in `document`, not inside the card.
 */
describe('who is this, answered in the people picker', () => {
	function saying(row: Element): string {
		const name = row.querySelector('.name');
		return ((name ?? row).textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();
	}

	function rows(): string[] {
		return [...document.querySelectorAll('.pick [role="menuitem"]')].map(saying);
	}

	function choose(words: string): void {
		const row = [...document.querySelectorAll('.pick [role="menuitem"]')].find(
			(one) => saying(one) === words
		);
		if (!(row instanceof HTMLElement)) throw new Error(`there is no row saying ${words}`);
		row.click();
		flushSync();
	}

	async function openThePicker(): Promise<void> {
		press('Choose person');
		await settle();
	}

	async function narrow(text: string): Promise<void> {
		const input = document.querySelector('.pick input');
		if (!(input instanceof HTMLInputElement)) throw new Error('there is no box to narrow with');
		input.focus();
		input.value = text;
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await new Promise((resolve) => setTimeout(resolve, 200));
		await settle();
	}

	it('opens the people picker rather than a field on the card or another screen', async () => {
		mocks.list.mockResolvedValue({ items: [username()], total: 1 });

		await draw();
		await openThePicker();

		expect(rows()).toEqual(expect.arrayContaining(['Neve', 'Neve Arbor']));
		// No second way in: the card has no field of its own and no "This is them".
		expect(host.querySelector('input')).toBeNull();
		expect(document.body.textContent).not.toContain('This is them');
		expect(mocks.goto).not.toHaveBeenCalled();
	});

	it('joins the username to whoever is picked, in one press, and reads the queue again', async () => {
		mocks.list.mockResolvedValue({ items: [username()], total: 1 });
		mocks.attach.mockResolvedValue({ person_name: 'Neve Arbor' });

		await draw();
		await openThePicker();
		choose('Neve Arbor');
		await settle();

		expect(mocks.attach).toHaveBeenCalledWith('a-1', { personId: 'p-2' });
		expect(mocks.attach).toHaveBeenCalledTimes(1);
		// The username is no longer waiting, so the queue is read again.
		expect(mocks.list).toHaveBeenCalledTimes(2);
	});

	it('pages with the pager every wall draws, and page two is still page two after a join', async () => {
		/* Without page buttons a pile of more than one page would show the first page under a count
		   that said more. The pager is the frame's own (`asPager`), and a join
		   reads the queue again at the SAME place rather than from the top. */
		mocks.list.mockImplementation(async (query: { offset?: number }) => ({
			items: [username({ id: `a-${query.offset ?? 0}` })],
			total: 130
		}));
		mocks.attach.mockResolvedValue({ person_name: 'Neve Arbor' });
		let pager: PagerProps | null = null;
		drawn = mount(UsernamePanel, {
			target: host,
			props: { onpaging: (reported: PagerProps | null) => (pager = reported) }
		}) as Record<string, unknown>;
		await settle();

		const first = pager as unknown as PagerProps;
		expect([first.offset, first.total]).toEqual([0, 130]);
		first.onnext();
		await settle();
		expect(mocks.list).toHaveBeenLastCalledWith({ unattached: true, limit: 60, offset: 60 });
		expect((pager as unknown as PagerProps).offset).toBe(60);

		await openThePicker();
		choose('Neve Arbor');
		await settle();

		expect(mocks.attach).toHaveBeenCalledWith('a-60', { personId: 'p-2' });
		expect(mocks.list).toHaveBeenLastCalledWith({ unattached: true, limit: 60, offset: 60 });
		expect((pager as unknown as PagerProps).offset).toBe(60);
	});

	it('makes somebody new from the typed name at the picker, then joins them', async () => {
		mocks.list.mockResolvedValue({ items: [username()], total: 1 });
		mocks.create.mockResolvedValue({ id: 'p-new', name: 'Elina Sorrel' });
		mocks.attach.mockResolvedValue({ person_name: 'Elina Sorrel' });

		await draw();
		await openThePicker();
		await narrow('Elina Sorrel');

		// Offered only because nobody on the page is called that.
		expect(rows()).toContain('Create Elina Sorrel');
		choose('Create Elina Sorrel');
		await settle();

		expect(mocks.create).toHaveBeenCalledWith('Elina Sorrel');
		expect(mocks.attach).toHaveBeenCalledWith('a-1', { personId: 'p-new' });
	});
});

it('puts the act on the right: "Open the files" first, "Choose person" last', async () => {
	// Every card in Sift ends on its affirmative. The two sit in one row laid out left to right, so
	// the order in the markup IS the order on screen.
	mocks.list.mockResolvedValue({ items: [username()], total: 1 });

	await draw();

	const words = [...host.querySelectorAll('.answers button')].map((one) =>
		(one.textContent ?? '').trim()
	);
	expect(words).toEqual(['Open the files', 'Choose person']);
});

it('shows the name the site displays when there is one, and the username when there is not', async () => {
	mocks.list.mockResolvedValue({ items: [username({ display_name: 'Neve Arb' })], total: 1 });

	await draw();

	expect(host.textContent).toContain('Neve Arb');
});

it('counts FILES under a username, with the site beside it', async () => {
	// "files", the word every wall counts in, never "items".
	mocks.list.mockResolvedValue({ items: [username({ asset_count: 1 })], total: 1 });

	await draw();

	expect(host.textContent).toContain('1 file on SomeSite');
	expect(host.textContent).not.toContain('1 item');
	expect(host.textContent).toContain('SomeSite');
});

it('groups a count in the thousands, as every other count on Organize is', async () => {
	mocks.list.mockResolvedValue({ items: [username({ asset_count: 1867 })], total: 1 });

	await draw();

	expect(host.textContent).toContain('1,867 files on SomeSite');
});

it('says what it counts without a site, rather than trailing a separator', async () => {
	// A username whose site was never recorded is ordinary. The detail line has to read as a sentence
	// either way rather than ending in a dash.
	mocks.list.mockResolvedValue({
		items: [username({ site_name: null, asset_count: 4 })],
		total: 1
	});

	await draw();

	expect(host.textContent).toContain('4 files');
	expect(host.textContent).not.toContain('4 files -');
});

it('says the queue is empty rather than drawing an empty wall', async () => {
	await draw();

	expect(host.textContent).toContain('Nothing to assign.');
});

it('says so when they could not be read, instead of reading as an empty queue', async () => {
	// The failure that matters: nothing to show and nothing wrong look identical, and one of them
	// means a person stops checking a queue that is actually full.
	mocks.list.mockRejectedValue(new Error('offline'));

	await draw();

	expect(host.textContent).toContain("Those couldn't be read.");
});

it('draws a username an undo put back, without the page being reloaded', async () => {
	// An Undo in History takes a join back on the server, which lists the username here again and
	// announces a library change. The card has to come back on this screen from that alone, not
	// stay gone until a reload while the server is already listing it.
	await draw();
	expect(host.textContent).toContain('Nothing to assign.');

	mocks.list.mockResolvedValue({ items: [username()], total: 1 });
	libraryChanges.changed();
	await settle();

	expect(mocks.list, 'the pile was not read again').toHaveBeenCalledTimes(2);
	expect(host.textContent).toContain('esmewrenfield');
});
