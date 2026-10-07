/* Settings > History: the pane over the whole-install record.
 *
 * The WORDS of each line are `ledger.test.ts`'s and are not re-checked here. What this file owns is
 * the three things the pane itself decides and nothing else can:
 *
 *  - the days, because a list read down is a list somebody is looking for a day in;
 *  - paging, by numbered pages of fifty: a turn asks for that page's offset, never the first
 *    page again;
 *  - the live re-read, because it must read the page somebody is ON again, never send them back to
 *    the first.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { PAGE, type LedgerEvent, type LedgerPage } from '$lib/library/ledger';
import { onRecord } from '$lib/shell/when';

const mocks = vi.hoisted(() => ({ readLedger: vi.fn(), undoDecision: vi.fn(), undoAll: vi.fn() }));

vi.mock('$lib/library/ledger', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/library/ledger')>()),
	readLedger: mocks.readLedger,
	undoAll: mocks.undoAll
}));

import { toasts } from '$lib/shell/toasts.svelte';

vi.mock('$lib/api/history', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/history')>()),
	undoDecision: mocks.undoDecision
}));

import { beforeNavigate } from '$app/navigation';

import { libraryChanges } from '$lib/library/changes.svelte';
import Ledger from './Ledger.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';

const DAY = 86_400;
/* A fixed moment so "Today" and "Yesterday" mean the same thing every run. The pane reads the clock
   through `byDay`, so the clock is what is faked rather than the module. */
const NOW = new Date(2026, 8, 17, 14, 0, 0);

function at(daysAgo: number, hour: number): number {
	const when = new Date(NOW);
	when.setHours(hour, 0, 0, 0);
	return Math.floor(when.getTime() / 1000) - daysAgo * DAY;
}

function event(id: string, over: Partial<LedgerEvent> = {}): LedgerEvent {
	return {
		id,
		at: at(0, 9),
		actor: { kind: 'sift', id: null, name: null },
		verb: 'added',
		subjects: [
			{ kind: 'asset', id: `a-${id}`, name: `${id}.mp4`, href: `/asset/a-${id}`, gone: false }
		],
		object: null,
		count: null,
		receipt: null,
		// The line is the server's, as pieces; the pane draws them and builds nothing.
		pieces: [words(`Sift added ${id}.mp4 to the library`)],
		detail: [],
		more: '',
		folded: 1,
		first: id,
		standing: 0,
		report: null,
		still: null,
		...over
	};
}

/** One run of plain words, as the server sends it. */
function words(text: string) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

/** A receipt on a line, which is what says the act was a decision and can be taken back. */
function receipt(over: Partial<NonNullable<LedgerEvent['receipt']>> = {}) {
	return {
		queue: 'folder-claims',
		title: 'Named 12 files from a folder',
		detail: '',
		reversed_at: null,
		final: false,
		taken_back: null,
		...over
	};
}

/** Answer the question Undo all asks first, by its own button inside the dialog. */
async function agree(): Promise<string> {
	await tick();
	flushSync();
	const dialog = document.querySelector('[role="alertdialog"]') as HTMLElement;
	expect(dialog, 'Undo all asked nothing').not.toBeNull();
	const asked = (dialog.textContent ?? '').replace(/\s+/g, ' ');
	dialog.querySelector<HTMLButtonElement>('.confirm')?.click();
	await tick();
	await tick();
	flushSync();
	return asked;
}

function undoButton(): HTMLButtonElement | null {
	const found = [...host.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === 'Undo'
	);
	return (found as HTMLButtonElement | undefined) ?? null;
}

function page(items: LedgerEvent[], total = items.length, offset = 0): LedgerPage {
	return { items, total, offset };
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
	vi.setSystemTime(NOW);
	mocks.readLedger.mockResolvedValue(page([]));
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	vi.useRealTimers();
});

async function draw(props: { decisions?: boolean } = {}): Promise<void> {
	drawn = mount(Ledger, { target: host, props }) as Record<string, unknown>;
	flushSync();
	// One tick for the read in `onMount` and one for the render that follows it.
	await tick();
	await tick();
	flushSync();
}

function lines(): string[] {
	/* Collapsed as a browser draws it: the shared Button in the middle of a sentence carries a
	   space either side of its label in the markup, which the screen never shows. */
	return [...host.querySelectorAll('.what')].map((one) =>
		(one.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
}

/* Each line's own moment, in the one form every record takes. */
function times(): string[] {
	return [...host.querySelectorAll('.at')].map((one) => one.textContent?.trim() ?? '');
}

/* A press of the pager under the list, by its name for a screen reader. */
function pagerPress(label: string): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>(`nav[aria-label="Pages"] [aria-label="${label}"]`);
}

/** Lines `from` to `from + count`, named by a letter so two pages never share an id. */
function lineRun(letter: string, count: number): LedgerEvent[] {
	return Array.from({ length: count }, (_unused, index) => event(`${letter}${index}`));
}

async function settle(): Promise<void> {
	await tick();
	await tick();
	flushSync();
}

/* The page a pane is left on outlives it (that is the point of it), so a test that turns pages
   turns back before it ends, or the next pane drawn in this file would open on its page. */
async function backToTheFirstPage(): Promise<void> {
	mocks.readLedger.mockResolvedValue(page(lineRun('a', PAGE), 120));
	pagerPress('First page')?.click();
	await settle();
}

/* Filters alone over nothing, for the seconds a read takes, say nothing about a list coming. Until
   the first read answers it draws the loading state every list has. */
it('draws the loading state until its first read answers, then the list', async () => {
	let answer: (value: LedgerPage) => void = () => {};
	mocks.readLedger.mockReturnValue(new Promise<LedgerPage>((resolve) => (answer = resolve)));
	drawn = mount(Ledger, { target: host }) as Record<string, unknown>;
	flushSync();
	await tick();
	flushSync();

	expect(host.querySelectorAll('.bone').length).toBeGreaterThan(0);
	expect(host.textContent).not.toContain('What you and Sift do appears here.');

	answer(page([event('one')]));
	await tick();
	await tick();
	flushSync();

	expect(host.querySelectorAll('.bone')).toHaveLength(0);
	expect(lines()).toHaveLength(1);
});

it('says nothing has happened yet in the words somebody can act on', async () => {
	await draw();

	expect(host.textContent).toContain('What you and Sift do appears here.');
});

it("says each line in the server's words and its moment in the one form every record takes", async () => {
	mocks.readLedger.mockResolvedValue(
		page([event('one'), event('two', { at: at(1, 11) }), event('three', { at: at(4, 11) })])
	);

	await draw();

	// No day headings: the time beside each line is the day and the time (one form, everywhere).
	expect(host.querySelectorAll('.section-heading h3')).toHaveLength(0);
	expect(times()).toEqual([at(0, 9), at(1, 11), at(4, 11)].map((one) => onRecord(one)));
	expect(times()[0]).toMatch(/^Today /);
	expect(times()[1]).toMatch(/^Yesterday /);
	expect(lines()).toEqual([
		'Sift added one.mp4 to the library',
		'Sift added two.mp4 to the library',
		'Sift added three.mp4 to the library'
	]);
});

it('puts every time in one column at the end, whether or not its line has a press', async () => {
	/* Declared tracks, so a line with a Report beside it and one without end their times at the
	   same edge, and a line that wraps keeps its time in its own cell rather than under it. */
	mocks.readLedger.mockResolvedValue(page([event('one'), event('two', { at: at(1, 11) })]));
	await draw();

	const list = host.querySelector<HTMLElement>('ul.rows');
	expect(list?.classList.contains('columned'), 'the list declares no columns').toBe(true);
	for (const time of host.querySelectorAll('.at')) {
		const cell = time.closest('.cell');
		expect(cell?.nextElementSibling, 'a cell follows the time').toBeNull();
		expect(cell?.classList.contains('end')).toBe(true);
	}
});

it('stands the presses and the time under the line at a phone width, so the line keeps the row', async () => {
	/* The two fixed tracks are 20rem and a phone line is about 24rem, so beside them the sentence
	   would be left a word's width and read one word to a line. On a phone the row is the line
	   alone, with the Report press and the moment on a line under it. */
	phoneWidth.yes = true;
	try {
		mocks.readLedger.mockResolvedValue(page([event('one')]));
		await draw();

		const row = host.querySelector<HTMLElement>('.row.columned');
		const cells = [...(row?.querySelectorAll<HTMLElement>(':scope > .cell') ?? [])];
		expect(cells, 'one cell: the line').toHaveLength(1);
		expect(cells[0].querySelector('.under .at')?.textContent).toBe(onRecord(event('one').at));
		expect(host.querySelectorAll('.at')).toHaveLength(1);
	} finally {
		phoneWidth.yes = false;
	}
});

it('asks for the next page when Next is pressed, and draws that page in place of this one', async () => {
	mocks.readLedger.mockResolvedValue(page(lineRun('a', PAGE), 120));
	await draw();
	mocks.readLedger.mockResolvedValue(page(lineRun('b', PAGE), 120, PAGE));

	pagerPress('Next page')?.click();
	await settle();

	expect(mocks.readLedger).toHaveBeenLastCalledWith(expect.objectContaining({ offset: PAGE }));
	expect(lines()).toHaveLength(PAGE);
	expect(lines()[0]).toContain('b0.mp4');
	await backToTheFirstPage();
});

it('numbers the pages, and a page pressed by number is read at its own offset', async () => {
	mocks.readLedger.mockResolvedValue(page(lineRun('a', PAGE), 120));
	await draw();
	const numbers = [...host.querySelectorAll('nav[aria-label="Pages"] .numbers li')].map((one) =>
		one.textContent?.trim()
	);
	expect(numbers).toEqual(['1', '2', '3']);
	expect(pagerPress('Show page 1')?.getAttribute('aria-current')).toBe('page');

	mocks.readLedger.mockResolvedValue(page(lineRun('c', 20), 120, PAGE * 2));
	pagerPress('Show page 3')?.click();
	await settle();

	expect(mocks.readLedger).toHaveBeenLastCalledWith(expect.objectContaining({ offset: PAGE * 2 }));
	expect(pagerPress('Show page 3')?.getAttribute('aria-current')).toBe('page');
	await backToTheFirstPage();
});

it('draws no pager when one page holds the whole of it', async () => {
	mocks.readLedger.mockResolvedValue(page([event('one')], 1));

	await draw();

	expect(host.querySelector('nav[aria-label="Pages"]')).toBeNull();
});

it('draws all three narrowings, asking for everything until somebody says otherwise', async () => {
	mocks.readLedger.mockResolvedValue(page([event('one')], 1));
	await draw();

	/* The two triggers, by the words on them. WHICH choices each offers is `ledger.test.ts`'s
	   (they are built from the vocabulary itself), and the list they open is a portal this renderer
	   will not open, so what is proved here is that the row is drawn and starts unnarrowed. */
	const triggers = [...host.querySelectorAll('button[aria-haspopup="listbox"]')].map(
		(one) => one.textContent?.trim() ?? ''
	);

	expect(triggers).toHaveLength(3);
	expect(triggers[0]).toContain('Everything');
	expect(triggers[1]).toContain('Everything');
	expect(triggers[2]).toContain('Everything');
	expect(mocks.readLedger).toHaveBeenLastCalledWith(
		expect.objectContaining({ kind: undefined, verb: undefined, decisions: undefined, offset: 0 })
	);
});

/*
 * DECISIONS: what was decided on Organize and what Sift filed by itself, as a choice of this one
 * list and not a screen of its own. Opened on it (Organize's Decisions lands here), it asks for the
 * decisions alone, and each line's Undo is the very door a decision card pressed, so a decision is
 * taken back from History exactly as from the card.
 */
it('opens on Decisions where it was sent there, and takes one back through the same door', async () => {
	mocks.readLedger.mockResolvedValue(page([event('one', { receipt: receipt() })], 1));
	mocks.undoDecision.mockResolvedValue(undefined);
	await draw({ decisions: true });

	const triggers = [...host.querySelectorAll('button[aria-haspopup="listbox"]')].map(
		(one) => one.textContent?.trim() ?? ''
	);
	expect(triggers[2]).toContain('Decisions');
	expect(mocks.readLedger).toHaveBeenLastCalledWith(
		expect.objectContaining({ decisions: true, offset: 0 })
	);

	undoButton()?.click();
	await settle();

	expect(mocks.undoDecision).toHaveBeenCalledWith('one');
	// And the page is read again under the same choice, so the line redraws as taken back there.
	expect(mocks.readLedger).toHaveBeenLastCalledWith(expect.objectContaining({ decisions: true }));
});

it("leads a decision's line with the picture the server handed for it, and only that line", async () => {
	/* A decision has to be CHECKED, not only read: each row carries a still of what it was
	   about. The server hands it on the line (`still`); a line with
	   none draws none, and the still sits at the row's height so the list keeps one rhythm. */
	mocks.readLedger.mockResolvedValue(
		page(
			[
				event('one', {
					receipt: receipt(),
					still: { kind: 'asset', id: 'file-otter', href: '/asset/file-otter', art: 'tok' }
				}),
				event('two', { receipt: receipt() })
			],
			2
		)
	);
	await draw({ decisions: true });

	const rows = [...host.querySelectorAll('.what')];
	expect(rows[0].querySelector('.still img')?.getAttribute('src')).toContain('file-otter');
	expect(rows[1].querySelector('.still')).toBeNull();
});

it("says what is true once a decision has been taken back, in its area's words", async () => {
	mocks.readLedger.mockResolvedValue(
		page(
			[
				event('one', {
					receipt: receipt({
						reversed_at: 1,
						taken_back: 'Taken back, so these can come up again.'
					})
				})
			],
			1
		)
	);
	await draw({ decisions: true });

	expect(lines()[0]).toContain('Taken back, so these can come up again.');
});

it('says under a decision what else it wrote, and not once it has been taken back', async () => {
	const more = 'A new person added, 1 other folder with that name answered.';
	mocks.readLedger.mockResolvedValue(
		page(
			[
				event('one', { receipt: receipt(), more } as Partial<LedgerEvent>),
				event('two', { receipt: receipt({ reversed_at: 1 }), more } as Partial<LedgerEvent>)
			],
			2
		)
	);
	await draw();

	expect(lines()[0]).toContain(more);
	expect(lines()[1]).not.toContain(more);
});

it('sends Decisions back with Undo all, so the press is read the way its line was drawn', async () => {
	mocks.readLedger.mockResolvedValue(
		page([event('press', { folded: 3, first: 'first', standing: 3, receipt: receipt() })], 1)
	);
	mocks.undoAll.mockResolvedValue({ undone: 3, of: 3 });
	await draw({ decisions: true });

	[...host.querySelectorAll('button')]
		.find((one) => one.textContent?.trim() === 'Undo all')
		?.click();
	await settle();
	await agree();

	expect(mocks.undoAll).toHaveBeenCalledWith('press', expect.objectContaining({ decisions: true }));
});

it('reads the page it is on again when something happens, never the first page', async () => {
	mocks.readLedger.mockResolvedValue(page(lineRun('a', PAGE), 120));
	await draw();
	mocks.readLedger.mockResolvedValue(page(lineRun('b', PAGE), 120, PAGE));
	pagerPress('Next page')?.click();
	await settle();
	mocks.readLedger.mockResolvedValue(page(lineRun('n', PAGE), 121, PAGE));

	libraryChanges.changed();
	await settle();

	expect(mocks.readLedger).toHaveBeenLastCalledWith(expect.objectContaining({ offset: PAGE }));
	expect(lines()[0]).toContain('n0.mp4');
	await backToTheFirstPage();
});

/*
 * ONE LINE PER PRESS: a task still running hands its line back with a newer id and a larger count.
 * The line it replaces is the one with the same oldest act (`first`), so the list never carries the
 * press twice.
 */
it('replaces a folded line that grew, rather than holding the press twice', async () => {
	mocks.readLedger.mockResolvedValue(
		page([event('press-3', { folded: 3, first: 'press-1' }), event('older')], 2)
	);
	await draw();
	mocks.readLedger.mockResolvedValue(
		page([event('press-5', { folded: 5, first: 'press-1' }), event('older')], 2)
	);

	libraryChanges.changed();
	await tick();
	await tick();
	flushSync();

	expect(lines()).toHaveLength(2);
	expect(lines()[0]).toContain('press-5.mp4');
});

/*
 * UNDO ALL on a folded line: every decision of the press, through the server's own reading of it
 * under the same narrowing. Never a single Undo on its newest act, which would take back one of
 * four thousand and read as the whole line.
 */
it('offers Undo all on a folded line of decisions, and asks for that press', async () => {
	mocks.readLedger.mockResolvedValue(
		page([event('press', { folded: 12, first: 'first', standing: 12, receipt: receipt() })], 1)
	);
	mocks.undoAll.mockResolvedValue({ undone: 12, of: 12 });
	await draw();

	expect(undoButton()).toBeNull();
	const all = [...host.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === 'Undo all'
	);
	expect(all).toBeTruthy();

	all?.click();
	await tick();
	flushSync();
	// It asks first, with the count, and nothing is taken back until it is answered.
	expect(mocks.undoAll).not.toHaveBeenCalled();
	expect(await agree()).toContain('Undo 12 decisions?');

	expect(mocks.undoAll).toHaveBeenCalledWith('press', { kind: undefined, verb: undefined });
});

/*
 * TAKING ONE BACK, from this list rather than from a band above the work on a queue screen.
 *
 * An event with a receipt IS a workbench decision (one row, one id), so the press goes through
 * the same door a queue screen uses. What is checked is the three conditions and the door, not the
 * server's answer: a line with no receipt was never a decision, one already reversed reads as taken
 * back, and a queue that can reverse nothing would be offering a button that can only say no.
 */
it('offers an Undo on a decision that has not been taken back, and presses the same door', async () => {
	mocks.readLedger.mockResolvedValue(page([event('one', { receipt: receipt() })], 1));
	mocks.undoDecision.mockResolvedValue(undefined);
	await draw();

	expect(undoButton()).not.toBeNull();

	undoButton()?.click();
	await tick();
	await tick();
	flushSync();

	expect(mocks.undoDecision).toHaveBeenCalledWith('one');
});

it('says how many went back when a batch was taken back only in part', async () => {
	const said = 'Put back 1 of 2 names. The others keep the names they have now.';
	mocks.readLedger.mockResolvedValue(page([event('one', { receipt: receipt() })], 1));
	mocks.undoDecision.mockResolvedValue({ undone: true, put_back: 1, of: 2, said });
	await draw();

	undoButton()?.click();
	await tick();
	await tick();
	flushSync();

	expect(toasts.items.map((one) => one.message)).toContain(said);
});

it('offers no Undo on a line that was never a decision', async () => {
	mocks.readLedger.mockResolvedValue(page([event('one')], 1));

	await draw();

	expect(undoButton()).toBeNull();
});

it('offers no Undo on one already taken back, nor on a queue that can reverse nothing', async () => {
	mocks.readLedger.mockResolvedValue(
		page(
			[
				event('back', { receipt: receipt({ reversed_at: 1_760_000_100 }) }),
				event('gone', { receipt: receipt({ final: true }) })
			],
			2
		)
	);

	await draw();

	expect(undoButton()).toBeNull();
});

/*
 * LAST IN THIS FILE ON PURPOSE. What the pane remembers about where it was left outlives one
 * component, which is the whole point of it, so a test that leaves it on page two would hand that
 * page to whatever mounts next. Anything added after this one restores that page instead of
 * reading the first.
 */
it('opens again on the page it was left on, rather than at the first page', async () => {
	mocks.readLedger.mockResolvedValue(page(lineRun('a', PAGE), 300));
	await draw();
	mocks.readLedger.mockResolvedValue(page(lineRun('b', PAGE), 300, PAGE));
	pagerPress('Next page')?.click();
	await settle();

	/* Pressing a name in a line RUNS a route, so the pane is torn down and built again, which
	   is the whole of what this memory exists for. */
	unmount(drawn as Record<string, unknown>);
	drawn = null;
	mocks.readLedger.mockClear();

	await draw();

	expect(mocks.readLedger).toHaveBeenLastCalledWith(
		expect.objectContaining({ limit: PAGE, offset: PAGE })
	);
	expect(lines()[0]).toContain('b0.mp4');
});

/*
 * WHERE IN THE LIST, on the box that is actually doing the scrolling.
 *
 * The pane is drawn inside a scrolling area it does not own, and that area declares itself
 * scrollable only once it has set itself up, so asking which box scrolls while this pane is
 * mounting answers `<html>`. A listener there records nothing, and the position put back would be
 * nought every time.
 */
it('puts back where the list was being read, on the box that really scrolls', async () => {
	const all = Array.from({ length: 600 }, (_unused, index) => event(`d${index}`));
	mocks.readLedger.mockImplementation(({ limit, offset }: { limit: number; offset: number }) =>
		Promise.resolve(page(all.slice(offset, offset + limit), all.length, offset))
	);

	/* A stand-in for that area: jsdom has no layout, so the box is given a position that can be
	   written and read back. What is being tested is which element is asked, not how far it goes. */
	const area = document.createElement('div');
	let top = 0;
	Object.defineProperty(area, 'scrollTop', {
		configurable: true,
		get: () => top,
		set: (next: number) => {
			top = next;
		}
	});
	document.body.append(area);
	area.append(host);

	await draw();
	/* AND IT BECOMES A SCROLLING BOX ONLY NOW: the area around this pane sets its own overflow
	   once it has measured itself, so anything that asked which box scrolls while the pane was
	   mounting would be told `<html>`. */
	area.style.overflowY = 'auto';
	area.scrollTop = 1200;

	// Somebody presses a name in a line. The position is taken here, before anything is taken apart.
	const leaving = vi.mocked(beforeNavigate).mock.calls.at(-1)?.[0];
	expect(leaving).toBeTypeOf('function');
	leaving?.({} as never);

	/* AND THEN THE SCREEN IS TIDIED ON ITS WAY OUT, which is the other half: a reset arrives as an
	   ordinary scroll, and following it put people back at the top. Nothing must read this. */
	area.scrollTop = 320;
	area.dispatchEvent(new Event('scroll'));

	unmount(drawn as Record<string, unknown>);
	drawn = null;
	area.scrollTop = 0;

	await draw();
	await tick();
	flushSync();

	expect(area.scrollTop).toBe(1200);
	area.remove();
});

/*
 * AND THE LEAVING THAT ANNOUNCES NOTHING, which is the other way out of this pane.
 *
 * Pressing a FILE's name opens the popout through `pushState`, and `pushState` in `@sveltejs/kit`
 * assigns `page.state` wholesale, so `page.state.settings` is gone, the Settings panel is
 * unmounted, and no navigation is announced: standing at 347 in a 450-line feed, pressing a file's
 * name and closing the popout with Escape must not come back at the TOP.
 *
 * The press is the door, because it is the one moment that is both early enough to be honest and
 * late enough to mean something. Recording it at the TEARDOWN puts nothing back: by then the screen
 * is being tidied on its way out, which is the reset the case above exists to ignore.
 */
it('takes the position at the press, for the leaving that announces nothing', async () => {
	const all = Array.from({ length: 600 }, (_unused, index) => event(`e${index}`));
	mocks.readLedger.mockImplementation(({ limit, offset }: { limit: number; offset: number }) =>
		Promise.resolve(page(all.slice(offset, offset + limit), all.length, offset))
	);

	const area = document.createElement('div');
	let top = 0;
	Object.defineProperty(area, 'scrollTop', {
		configurable: true,
		get: () => top,
		set: (next: number) => {
			top = next;
		}
	});
	document.body.append(area);
	area.append(host);

	/* Scrolling before the pane is drawn, unlike the case above: the rule that the area declares
	   itself late is that case's, and what this one is about is the door, not the finding. */
	area.style.overflowY = 'auto';
	await draw();
	area.scrollTop = 980;

	/* Somebody puts a finger on a file's name. Nothing has moved yet, and this is the reading. */
	area.dispatchEvent(new Event('pointerdown', { bubbles: true }));

	/* And THEN the panel is taken away with no navigation announced at all, and the screen on its
	   way out is tidied. Neither of those may be read. */
	area.scrollTop = 320;
	area.dispatchEvent(new Event('scroll'));
	unmount(drawn as Record<string, unknown>);
	drawn = null;
	await tick();
	flushSync();
	area.scrollTop = 0;

	await draw();
	await tick();
	flushSync();

	expect(area.scrollTop).toBe(980);
	area.remove();
});

it('folds a line naming many things and opens the rest in place, the line wrapping to hold it', async () => {
	/* Nineteen file names would run off the end of the row. The server names five and folds the
	   rest into ONE piece, which opens where the count was. */
	const file = (at: number) => ({
		text: `${String(at + 2).padStart(2, '0')}.jpg`,
		kind: 'asset',
		id: `f${at}`,
		href: `/asset/f${at}`,
		gone: false,
		rest: [],
		lead: ''
	});
	/* A list's own separators: ", " between names, " and " before the last where `last` says so. */
	const listed = (from: number, to: number, last: string) =>
		Array.from({ length: to - from }, (_, at) => [
			words(at === to - from - 1 ? last : ', '),
			file(from + at)
		]).flat();
	const pieces = [
		words('You agreed with 19 matches: '),
		...listed(0, 5, ', ').slice(1),
		{ ...words('14 more'), lead: ' and ', rest: listed(5, 19, ' and ') },
		words(' confirmed')
	];
	mocks.readLedger.mockResolvedValue(page([event('many', { verb: 'decided', pieces })]));

	await draw();

	expect(lines()[0]).toBe(
		'You agreed with 19 matches: 02.jpg, 03.jpg, 04.jpg, 05.jpg, 06.jpg and 14 more confirmed'
	);
	const more = host.querySelector<HTMLButtonElement>('.what button');
	expect(more?.getAttribute('aria-expanded')).toBe('false');

	more?.click();
	flushSync();

	expect(host.querySelector('.what button')).toBeNull();
	expect(host.querySelectorAll('.what a')).toHaveLength(19);
});

it("offers a run's Report only where the server says the line has one", async () => {
	/* A task's own run line (a backup) names the task under the same subject kind as a pass,
	   and a Report on it would answer "Couldn't load that report". The line's `report` is the
	   answer. */
	const run = (id: string, subject: string, report: string | null) =>
		event(id, {
			verb: 'ran',
			subjects: [{ kind: 'run', id: subject, name: 'Scan', href: null, gone: false }],
			pieces: [words(`Sift ran ${subject}`)],
			report
		});
	mocks.readLedger.mockResolvedValue(
		page([run('e1', '01RUNOFAPASS', '01RUNOFAPASS'), run('e2', 'backup', null)])
	);

	await draw();

	const reports = [...host.querySelectorAll('button')].filter(
		(one) => one.textContent?.trim() === 'Report'
	);
	expect(reports).toHaveLength(1);
});
