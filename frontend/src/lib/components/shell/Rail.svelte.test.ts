/*
 * The rail, and the one thing on it that reports as well as navigates.
 *
 * Sift works in the background, which leaves nothing on screen saying so: a file dropped into a
 * watched folder is probed, thumbnailed and previewed with no sign of it except the jobs queue. The
 * jobs queue is a section of Settings, and an ambient "something is happening" must not live only
 * on a screen nobody has open, so the Settings gear turns, and it is also the way to the queue that
 * is turning it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import contextMenuItem from '$lib/components/common/ContextMenuItem.svelte?raw';
import Rail from './Rail.svelte';
import source from './Rail.svelte?raw';
import { imports } from '$lib/library/imports.svelte';
import codepoints from '$lib/generated/icon-codepoints.json';
import { rail } from './rail-state.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the note that the Downloads outcomes were seen, which this file does not follow. */
noServerAt('/api/downloads/seen');

/* The press behind the leaf and the bolt, caught here: what it sends is `full-amount.test.ts`'s. */
const presses = vi.hoisted(() => ({ asked: [] as boolean[] }));
vi.mock('./full-amount', async (original) => ({
	...(await original<typeof import('./full-amount')>()),
	pressFullAmount: vi.fn(async (on: boolean) => {
		presses.asked.push(on);
	})
}));

/* Mutable, so one file can render the rail as both an admin and a guest. `vi.hoisted` because a
 * `vi.mock` factory is lifted above every ordinary declaration in the file and would otherwise
 * close over a variable that does not exist yet. */
const mocks = vi.hoisted(() => ({
	session: { isAdmin: true, isSignedIn: true },
	saved: { items: [] as { id: string; name: string; query: string }[], ensure: () => {} }
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: mocks.session }));
vi.mock('$lib/search/saved-searches.svelte', () => ({ savedSearches: mocks.saved }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: { url: new URL('http://localhost/browse') }
}));

let host: HTMLElement;

afterEach(() => {
	imports.busy = 0;
	imports.downloading = 0;
	imports.waitingForCookies = 0;
	imports.expiredCookies = 0;
	imports.downloadFailed = false;
	imports.downloadSucceeded = false;
	imports.page = null;
	presses.asked = [];
	mocks.session.isAdmin = true;
	mocks.saved.items = [];
	// The rail's arrangement is one object for the whole application, so a test that rearranges it
	// hands the next one a rail somebody else moved.
	rail.reset();
	rail.editing = false;
	host?.remove();
});

function render() {
	host = document.createElement('div');
	document.body.append(host);
	mount(Rail, { target: host });
	flushSync();
	return host;
}

/** A nav link's icon wrapper (the thing that turns) by its destination. */
function glyphOf(href: string): HTMLElement {
	const link = host.querySelector(`a[href="${href}"]`) as HTMLElement;
	return link.querySelector('.glyph') as HTMLElement;
}

/** The glyph character rendered inside a link, so a swap from one icon to another is observable. */
function glyphChar(href: string): string {
	return (glyphOf(href).querySelector('.icon') as HTMLElement).textContent ?? '';
}

describe('the busy gear on Settings', () => {
	it('is still when there is nothing to do', () => {
		imports.working = 0;
		render();

		expect(glyphOf('/settings').className).not.toContain('working');
	});

	it('turns while there is background work', () => {
		render();

		imports.working = 3;
		flushSync();

		expect(glyphOf('/settings').className).toContain('working');
	});

	it('turns for work that brings nothing in, which is most of it', () => {
		/*
		 * The gear turns for any work in flight except a download (`working`), not only arriving
		 * work (`busy`: imports, probes, thumbnails), so a library-wide face pass turns it too, on
		 * the item that leads to the queue running it, whatever kind of work it is.
		 */
		render();
		imports.busy = 0;
		imports.downloading = 0;
		imports.working = 200;
		flushSync();

		expect(glyphOf('/settings').className).toContain('working');
	});

	it('stops when the queue empties', () => {
		render();
		imports.working = 1;
		flushSync();

		imports.working = 0;
		flushSync();

		expect(glyphOf('/settings').className).not.toContain('working');
	});

	it('does not turn for a download alone: that is the Downloads item now', () => {
		// A download does not turn the gear: it is excluded from `working` outright, so a lone
		// download leaves the gear still.
		render();
		imports.busy = 1;
		imports.downloading = 1;
		imports.working = 0;
		flushSync();

		expect(glyphOf('/settings').className).not.toContain('working');
	});
});

describe('the Downloads row while a download fetches', () => {
	/* Nothing turns for a download: the Downloads screen shows each download's progress, and a
	 * second thing spinning in the corner would be a distraction. */
	it('keeps its own glyph, still, and nothing in the rail turns for it', () => {
		render();
		const idle = glyphChar('/downloads');

		imports.busy = 1;
		imports.downloading = 1;
		flushSync();

		expect(glyphOf('/downloads').className).not.toContain('working');
		expect(glyphChar('/downloads')).toBe(idle);
		expect(host.querySelectorAll('.glyph.working')).toHaveLength(0);
	});

	it('still says so to a screen reader, and keeps its count of Sites waiting for cookies', () => {
		render();
		imports.downloading = 1;
		imports.waitingForCookies = 2;
		flushSync();

		const link = host.querySelector('a[href="/downloads"]') as HTMLElement;
		expect(link.getAttribute('aria-label')).toContain('downloading');
		expect(link.querySelector('.wanted')?.textContent).toBe('2');
	});
});

/** A queue page carrying only what the leaf reads. */
function queuePage(facts: { running: number; stepping_back: boolean; full_amount: boolean }) {
	return {
		jobs: [],
		counts: { running: facts.running },
		stepping_back: facts.stepping_back,
		full_amount: facts.full_amount
	} as unknown as NonNullable<typeof imports.page>;
}

/** The leaf or bolt's button, or null. */
function fullAmountButton(): HTMLButtonElement | null {
	return host.querySelector('.full-amount button');
}

describe('the leaf and the bolt above the rule', () => {
	it('draws the leaf, not pressed, while tasks run on fewer workers because the device is in use', () => {
		render();
		imports.page = queuePage({ running: 4, stepping_back: true, full_amount: false });
		flushSync();

		const button = fullAmountButton();
		expect(button).not.toBeNull();
		expect(button?.getAttribute('aria-pressed')).toBe('false');
		expect(button?.getAttribute('aria-label')).toBe('Use the full amount of this device');
		// Above the rule, in the bottom half.
		const footer = host.querySelector('.group.footer') as HTMLElement;
		const rule = footer.querySelector('.rail-rule') as HTMLElement;
		const holder = footer.querySelector('.full-amount') as HTMLElement;
		expect(holder.compareDocumentPosition(rule) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('draws the bolt, pressed, while the full amount runs although the device is in use', () => {
		render();
		imports.page = queuePage({ running: 8, stepping_back: false, full_amount: true });
		flushSync();

		expect(fullAmountButton()?.getAttribute('aria-pressed')).toBe('true');
		const bolt = fullAmountButton()?.textContent;
		expect(bolt?.trim()).toBe(String.fromCodePoint(parseInt(codepoints.bolt_boost, 16)));
		imports.page = queuePage({ running: 8, stepping_back: true, full_amount: false });
		flushSync();
		expect(fullAmountButton()?.textContent).not.toBe(bolt);
	});

	/* Read from the stylesheet: a pressed ghost would otherwise sit in a filled circle at rest. */
	it('draws the bolt yellow on the bare ground, its circle only under the pointer', () => {
		const at = source.indexOf(
			".full-amount :global(.btn.ghost.rail-full-amount[aria-pressed='true']:hover:not(:disabled)) {"
		);
		expect(at, 'the pressed rule moved').toBeGreaterThan(-1);
		const body = source.slice(at, source.indexOf('}', at));
		expect(body).toContain('--btn-ground: transparent;');
		expect(body).toContain('color: var(--sift-warn);');
		expect(source).not.toMatch(
			/rail-full-amount\[aria-pressed='true'\]\)\s*\{[^}]*background-color/
		);
	});

	it('presses for the full amount from the leaf and steps back from the bolt', async () => {
		render();
		imports.page = queuePage({ running: 4, stepping_back: true, full_amount: false });
		flushSync();
		fullAmountButton()?.click();
		flushSync();
		expect(presses.asked).toEqual([true]);
		// One press at a time: the second waits for the first to be answered.
		await vi.waitFor(() => expect(fullAmountButton()?.disabled).toBe(false));

		imports.page = queuePage({ running: 8, stepping_back: false, full_amount: true });
		flushSync();
		fullAmountButton()?.click();
		flushSync();
		expect(presses.asked).toEqual([true, false]);
	});

	it('draws nothing with no tasks running, or with nobody at the device', () => {
		render();
		imports.page = queuePage({ running: 0, stepping_back: true, full_amount: false });
		flushSync();
		expect(fullAmountButton()).toBeNull();

		// Nobody here: the full count by itself, neither flag set.
		imports.page = queuePage({ running: 12, stepping_back: false, full_amount: false });
		flushSync();
		expect(fullAmountButton()).toBeNull();
	});

	it('is not offered to a guest, whose window never reads the queue', () => {
		mocks.session.isAdmin = false;
		render();
		imports.page = queuePage({ running: 4, stepping_back: true, full_amount: false });
		flushSync();
		expect(fullAmountButton()).toBeNull();
	});

	it('keeps its place on the icons-only rail', () => {
		rail.collapsed = true;
		try {
			render();
			imports.page = queuePage({ running: 4, stepping_back: true, full_amount: false });
			flushSync();
			expect(host.querySelector('nav.rail')?.className).toContain('collapsed');
			expect(fullAmountButton()).not.toBeNull();
		} finally {
			rail.collapsed = false;
		}
	});
});

describe('what the rail contains, and in what order', () => {
	/* The order is a decision, not an accident, so it is written down somewhere that fails when it
	 * changes. The top group is the ways of looking at a library; the bottom group is the places
	 * you go TO. */
	it('lists the ways of looking, in the order they were chosen', () => {
		render();

		const hrefs = [...host.querySelectorAll('nav a.item')].map((link) => link.getAttribute('href'));

		expect(hrefs).toEqual([
			'/browse',
			'/people',
			'/sites',
			'/collections',
			'/photo-sets',
			'/tags',
			'/songs',
			'/loops',
			'/favorites',
			'/theater',
			'/organize',
			'/downloads',
			'/hidden',
			'/insights',
			'/settings',
			'/recent'
		]);
	});

	it('draws the rule between Theater and Organize', () => {
		render();

		const hrefsIn = (selector: string) =>
			[...host.querySelectorAll(`${selector} a.item`)].map((link) => link.getAttribute('href'));
		expect(hrefsIn('.group:not(.footer)').at(-1)).toBe('/theater');
		expect(hrefsIn('.group.footer')[0]).toBe('/organize');
	});

	it('keeps Library and Jobs out of it entirely', () => {
		// Both are sections of Settings. A rail slot pointing at them would be a second way to
		// somewhere with one home, and they have no routes of their own.
		render();

		expect(host.querySelector('a[href="/library"]')).toBeNull();
		expect(host.querySelector('a[href="/jobs"]')).toBeNull();
	});

	it('puts the divider above the bottom group rather than between two admin blocks', () => {
		render();

		const divider = host.querySelector('.rail-rule');
		expect(divider).not.toBeNull();
		expect(divider?.parentElement?.className).toContain('footer');
	});
});

/*
 * A window snapped to half of a 1920 screen is about 960 wide, which the desktop window allows.
 * The rail goes icon-only there and keeps every row: a row that comes and goes as the window is
 * resized is one nobody can learn where to find.
 */
describe('the rail at the width of a snapped window', () => {
	afterEach(() => vi.unstubAllGlobals());

	/** Answer every media query the way a window `width` wide would. */
	function windowOf(width: number): void {
		vi.stubGlobal('matchMedia', (query: string) => {
			const min = /min-width:\s*(\d+)px/.exec(query);
			const max = /max-width:\s*(\d+)px/.exec(query);
			const matches =
				(min === null || width >= Number(min[1])) && (max === null || width <= Number(max[1]));
			return {
				matches,
				media: query,
				addEventListener: () => {},
				removeEventListener: () => {}
			};
		});
	}

	it('keeps every row, Theater included, and draws them icon-only', () => {
		windowOf(960);
		render();

		const hrefs = [...host.querySelectorAll('nav a.item')].map((link) => link.getAttribute('href'));
		expect(hrefs).toContain('/theater');
		expect(hrefs).toHaveLength(16);
		expect(host.querySelector('nav.rail')?.classList.contains('collapsed')).toBe(true);
	});
});

describe('the rail a guest is shown', () => {
	it('offers Settings but not the admin destinations beside it', () => {
		/* Filtered per item rather than as a block, so a guest gets a bottom group with one row in it
		 * rather than none. None of this is what protects those screens: the server refuses them.
		 * It is about not offering a door that will not open. */
		mocks.session.isAdmin = false;
		render();

		expect(host.querySelector('a[href="/settings"]')).not.toBeNull();
		expect(host.querySelector('a[href="/downloads"]')).toBeNull();
		// Hidden is theirs now: a guest has their own hidden set and needs somewhere to look at it.
		expect(host.querySelector('a[href="/hidden"]')).not.toBeNull();
	});

	it('does not offer Organize, which is the admin decides what the library says', () => {
		/* Absent rather than shown and refused. The server turns a guest away from everything behind
		 * it, and that is the control; this is about not offering a door that will not open.
		 *
		 * It sits ABOVE the rule, unlike the other admin row, so this is not covered by the group
		 * below the rule being filtered: each item is filtered on its own. */
		mocks.session.isAdmin = false;
		render();

		expect(host.querySelector('a[href="/organize"]')).toBeNull();
	});

	it("leaves a link dropped on Favorites to the window, which says it is an admin's", () => {
		const zone = () => host.querySelector('a[href="/favorites"]')?.closest('[data-drop-zone]');
		render();
		expect(zone(), 'an admin drops a link on Favorites').toBeTruthy();
		host.remove();

		mocks.session.isAdmin = false;
		render();

		expect(zone()).toBeFalsy();
	});
});

describe('what the arrangement does to the rail', () => {
	/* The rail draws the order the browser remembers, not the order the code was written in. These
	 * assert the drawn rail follows it: the stored arrangement being READ is tested where it is
	 * stored; this is it being obeyed. */

	it('draws the rows in the arrangement, not in the order they are declared', () => {
		rail.nudge('favorites', -1);
		render();

		const hrefs = [...host.querySelectorAll('nav a.item')].map((link) => link.getAttribute('href'));

		expect(hrefs.indexOf('/favorites')).toBeLessThan(hrefs.indexOf('/loops'));
	});

	it('leaves out a row that has been put away', () => {
		rail.hide('tags');
		render();

		expect(host.querySelector('a[href="/tags"]')).toBeNull();
		// And not by leaving out the rest of them with it.
		expect(host.querySelector('a[href="/browse"]')).not.toBeNull();
	});

	it('draws a row on the side of the rule it was moved to', () => {
		/* The rule is a position rather than a wall. A row dragged below it belongs to the bottom of
		 * the rail from then on, which is the group the rule sits in. */
		rail.placeInRegion('tags', 'below');
		render();

		const footer = host.querySelector('.footer') as HTMLElement;
		expect(footer.querySelector('a[href="/tags"]')).not.toBeNull();
	});
});

describe('rearranging is a mode, and it is off', () => {
	/* A rail row is a link somebody clicks dozens of times a day. Nothing here may be picked up by a
	 * click with a little drift in it, which is what a rail full of draggable anchors gives you:
	 * a browser drags an anchor by default. */

	it('so no row can be dragged until it is asked for', () => {
		render();

		const links = [...host.querySelectorAll('nav a.item')];
		expect(links.length).toBeGreaterThan(0);
		for (const link of links) expect(link.getAttribute('draggable')).toBe('false');
	});

	it('and there is nothing offering to put a row away', () => {
		render();

		expect(host.querySelector('.put-away')).toBeNull();
	});

	it('until the mode is on, and then every row can be picked up', () => {
		render();

		rail.editing = true;
		flushSync();

		for (const link of host.querySelectorAll('nav a.item')) {
			expect(link.getAttribute('draggable')).toBe('true');
		}
	});

	it('and every row but Settings offers to be put away', () => {
		// Settings is where a hidden row is put back. A control that removes the way back is not a
		// control anybody should be offered.
		render();

		rail.editing = true;
		flushSync();

		// The label is on the control rather than the glyph inside it: the shared button requires
		// one from an icon-only caller.
		const labels = [...host.querySelectorAll('.put-away')].map((button) =>
			button.getAttribute('aria-label')
		);
		expect(labels).toContain('Hide Browse');
		expect(labels).not.toContain('Hide Settings');
	});

	it('and there is a way out of it that does not need a keyboard', () => {
		render();

		rail.editing = true;
		flushSync();

		const done = host.querySelector('.done') as HTMLButtonElement;
		expect(done).not.toBeNull();

		done.click();
		flushSync();

		expect(rail.editing).toBe(false);
		expect(host.querySelector('.put-away')).toBeNull();
	});
});

describe('the searches somebody kept', () => {
	const search = (id: string, name: string, query = 'q=beach') => ({ id, name, query });

	it('are not on the rail at all', () => {
		/*
		 * Saved searches are not a group on the rail: the rail carries places Sift has, and a saved
		 * search is a question somebody wrote. They are made, renamed and deleted on the filter
		 * bar. Asserted, so putting them back is a decision.
		 */
		mocks.saved.items = [search('one', 'Beaches'), search('two', 'Long ones')];

		const host = render();

		expect(host.querySelector('.saved')).toBeNull();
		expect(host.textContent).not.toContain('Beaches');
		expect(host.textContent).not.toContain('Long ones');
	});
});

describe('the Sites asking for cookies', () => {
	/* The rail says the machine is working (a turning glyph, a pulsing dot), and without this
	   would say nothing at all about the queue having STOPPED and being sat waiting on a person.
	   Those look identical from a rail with only a spinner on it. The count is the same number the
	   Downloads screen's Edit cookies row wears under Options, read from the same store the dot
	   comes from. */
	it('counts the downloads waiting and the Sites whose cookies ran out', () => {
		imports.waitingForCookies = 2;
		imports.expiredCookies = 1;

		render();

		const row = host.querySelector('a[href="/downloads"]') as HTMLElement;
		expect(row.querySelector('.wanted')?.textContent).toBe('3');
	});

	/* The colour is not the only thing saying so: what a screen reader is told has to carry it too,
	   the way the status dot's own words do. */
	it('says so in the label, for anybody who cannot see the colour', () => {
		imports.waitingForCookies = 1;

		render();

		const row = host.querySelector('a[href="/downloads"]') as HTMLElement;
		expect(row.getAttribute('aria-label')).toBe('Downloads, 1 waiting for cookies');
	});

	it('says nothing when no Site is asking for anything', () => {
		render();

		const row = host.querySelector('a[href="/downloads"]') as HTMLElement;
		expect(row.querySelector('.wanted')).toBeNull();
		expect(row.getAttribute('aria-label')).toBe('Downloads');
	});

	/* Said while another download fetches: a download fetching and a download waiting for cookies
	   are two different downloads, and a queue commonly has both. The fetching one turns nothing
	   and lights nothing; the count stays. */
	it('stays while another download fetches, which draws nothing of its own', () => {
		imports.downloading = 1;
		imports.waitingForCookies = 1;

		render();

		const row = host.querySelector('a[href="/downloads"]') as HTMLElement;
		expect(row.querySelector('.glyph.working'), 'the glyph turned').toBeNull();
		expect(row.querySelector('.status'), 'a running download lit a dot').toBeNull();
		expect(row.querySelector('.wanted')?.textContent).toBe('1');
	});
});

/*
 * "Mark downloads as seen", on the Downloads row's own right-click menu.
 *
 * The dot on Downloads (green for a finished download, red for a failed one) goes out on opening
 * the Downloads screen, and acknowledging it should not mean leaving whatever somebody is doing,
 * so the row's menu offers the same acknowledgement where the dot is. It is declared as data beside the
 * destination (`nav.ts`) and drawn by the shared verb list, so these open the real menu rather than
 * reading the source: what is held is that the right row carries it, that it says why when it is
 * refused, and that choosing it puts the dot out.
 */
describe('the Downloads row marks its outcomes as seen', () => {
	/* jsdom has no pointer capture and the menu primitive releases it on the way down; absent, the
	   handler throws and no menu opens. A gap in the test environment, not in the app. */
	for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
		if (!(name in Element.prototype)) {
			Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
		}
	}

	let instance: ReturnType<typeof mount> | null = null;

	afterEach(() => {
		if (instance) unmount(instance);
		instance = null;
		document.body.innerHTML = '';
	});

	function mountRail() {
		host = document.createElement('div');
		document.body.append(host);
		instance = mount(Rail, { target: host });
		flushSync();
	}

	/** The words of a row, with the icon ligature's private-use codepoints taken out. */
	const words = (row: Element) => (row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();

	/** Right-click the rail row with this id, and wait for its menu. By the row's own id rather
	    than its address: the brand at the head of the rail is a link to Browse too. */
	async function openMenuOf(id: string): Promise<HTMLElement[]> {
		const trigger = host
			.querySelector(`a[data-rail-row="${id}"]`)
			?.closest('[data-context-menu-trigger]');
		expect(trigger, `no right-click target around the ${id} row`).toBeTruthy();
		trigger?.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true }));
		let rows: HTMLElement[] = [];
		await vi.waitFor(
			() => {
				rows = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')];
				expect(rows.map(words)).toContain('Move up');
			},
			{ timeout: 5000 }
		);
		return rows;
	}

	const markRow = (rows: HTMLElement[]) =>
		rows.find((row) => words(row).startsWith('Mark downloads as seen'));

	it('is on the Downloads menu, above the arranging, and on no other row', async () => {
		imports.downloadSucceeded = true;
		mountRail();

		const rows = await openMenuOf('downloads');
		const labels = rows.map(words);
		expect(labels[0]).toBe('Mark downloads as seen');
		expect(labels.indexOf('Mark downloads as seen')).toBeLessThan(labels.indexOf('Move up'));
		expect(
			document.querySelector('.ui-menu .menu-separator'),
			'nothing sets it apart'
		).not.toBeNull();

		if (instance) unmount(instance);
		instance = null;
		document.body.innerHTML = '';
		mountRail();
		expect(markRow(await openMenuOf('browse'))).toBeUndefined();
	});

	it.each([
		['the dot is out', () => {}],
		['a download is still running', () => (imports.downloading = 1)]
	])('is refused, saying why, while %s', async (_when, arrange) => {
		arrange();
		mountRail();

		const row = markRow(await openMenuOf('downloads'));
		expect(row, 'the row vanished rather than being refused').toBeTruthy();
		expect(row?.hasAttribute('data-disabled')).toBe(true);
		expect(words(row as HTMLElement)).toContain('Nothing new');
	});

	it('puts the dot out when chosen, and leaves every download where it is', async () => {
		imports.downloadFailed = true;
		mountRail();
		expect(host.querySelector('a[href="/downloads"] .status-error')).not.toBeNull();

		const row = markRow(await openMenuOf('downloads'));
		expect(row?.hasAttribute('data-disabled')).toBe(false);
		expect(words(row as HTMLElement)).not.toContain('Nothing new');
		row?.click();
		flushSync();

		expect(imports.downloadStatus).toBe('none');
		expect(host.querySelector('a[href="/downloads"] .status')).toBeNull();
	});
});

describe('a row of the rail', () => {
	afterEach(removeStyles);

	it("is dressed by the rail alone: a menu row's rule does not reach it", () => {
		const row = render().querySelector('a.item') as HTMLElement;

		/* A menu row writes `.item` too. The rail's rows are not in a menu, so its layout and inset
		   must not arrive here. */
		applyStyles(contextMenuItem);
		expect(getComputedStyle(row).display).not.toBe('flex');

		applyStyles(source, row);
		expect(getComputedStyle(row).display).toBe('flex');
		expect(getComputedStyle(row).userSelect).toBe('none');
	});
});
