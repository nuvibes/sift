/*
 * The report that says who, other than you, can see one thing, and through what.
 *
 * What is checked is the wording, because the wording is the product. The panel writes and decides
 * nothing: the server answers who can see the thing and which line is in force, and the tests are
 * about whether that answer survives being drawn. The three mistakes it could make are sentences: a
 * yes with no reason beside it, a losing decision drawn as in force, and a switched-off user
 * reported as a plain no.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick } from 'svelte';
import VisibilityDialog from './VisibilityDialog.svelte';
import type { ReachUser, ReachReport, ShareTarget } from '$lib/library/sharing';

const mocks = vi.hoisted(() => ({ fetchReach: vi.fn(), fetchReachThrough: vi.fn() }));

vi.mock('$lib/library/sharing', async () => {
	const actual =
		await vi.importActual<typeof import('$lib/library/sharing')>('$lib/library/sharing');
	return {
		...actual,
		fetchReach: mocks.fetchReach,
		fetchReachThrough: mocks.fetchReachThrough
	};
});

const TARGET: ShareTarget = { type: 'item', id: 'a1', label: 'holiday.mp4' };

function user(over: Partial<ReachUser> = {}): ReachUser {
	return {
		id: 'u2',
		name: 'sam',
		role: 'guest',
		disabled: false,
		sees: false,
		through: [],
		...over
	};
}

function report(over: Partial<ReachReport> = {}): ReachReport {
	return {
		subject_type: 'item',
		subject_id: 'a1',
		hidden: false,
		concealed: false,
		outside: null,
		users: [],
		...over
	};
}

let host: HTMLElement;

async function open(answer: ReachReport, target: ShareTarget = TARGET) {
	mocks.fetchReach.mockResolvedValue(answer);
	host = document.createElement('div');
	document.body.append(host);
	mount(VisibilityDialog, { target: host, props: { open: true, target } });
	flushSync();
	await tick();
	await tick();
	flushSync();
}

/* Portalled to the end of the document, so the sheet is not inside the host it was mounted into. */
function panel(): HTMLElement | null {
	return document.querySelector('.reach-sheet');
}

function text(): string {
	return (panel()?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

/* The answer beside each name, read from the row rather than from the panel's whole text: the
 * sentence at the foot of the panel is prose about reach, so a search over everything would pass
 * whatever the rows happened to say. The same trap `ShareDialog`'s own suite names. */
function standings(): string[] {
	return [...(panel()?.querySelectorAll('.standing') ?? [])].map((each) =>
		(each.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
}

function reasons(): string[] {
	return [...(panel()?.querySelectorAll('.reason') ?? [])].map((each) =>
		(each.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
}

beforeEach(() => {
	mocks.fetchReach.mockReset();
	/* Nothing to explain, by default. Every test but the two below is about a row the first read
	   already accounts for, and a stub answering reasons would put a sentence under all of them. */
	mocks.fetchReachThrough.mockReset();
	mocks.fetchReachThrough.mockResolvedValue({ reasons: [], files: 0, complete: true });
});

afterEach(() => {
	document.querySelectorAll('.reach-sheet').forEach((each) => each.remove());
	host?.remove();
});

describe('the visibility report', () => {
	it('names what it is about, so nobody reads it against the wrong file', async () => {
		await open(report());
		expect(text()).toContain('holiday.mp4');
	});

	it('says an install with one user has nobody to report on', async () => {
		/* Not an error and not an empty list somebody has to interpret. The report is about everybody
		   BUT the person reading it, so with nobody else there is genuinely nothing to say. */
		await open(report());
		expect(text()).toContain('only user here');
		expect(standings()).toEqual([]);
	});

	it('explains a yes that nothing on the thing itself accounts for', async () => {
		/* The reason the panel exists. Nothing was written on the file; it is reachable because the
		   folder it sits in was handed over, and a report saying "Can see" with no line under it is
		   the sentence with a hole in it this whole panel is about. */
		await open(
			report({
				users: [
					user({
						sees: true,
						through: [
							{ kind: 'folder', id: 'f1', name: 'Holiday', how: 'inherited', decides: true }
						]
					})
				]
			})
		);
		expect(standings()).toEqual(['Can see']);
		expect(reasons()[0]).toBe('Shared through the folder Holiday');
	});

	it('words a decision made on the thing itself differently from one reaching in', async () => {
		await open(
			report({
				users: [
					user({
						sees: true,
						through: [{ kind: 'item', id: 'a1', name: null, how: 'shared', decides: true }]
					})
				]
			})
		);
		expect(reasons()[0]).toBe('Shared on this file itself');
	});

	it('draws a decision that lost without letting it read as in force', async () => {
		/* Both are shown (the share is the answer to what happens when the restrict comes off, which
		   is the next question every time) and only one of them is the reason. The loser takes the
		   quiet class rather than being filtered out, because a report that hides the share leaves
		   somebody to rediscover it the day the restrict is lifted. */
		await open(
			report({
				users: [
					user({
						sees: false,
						through: [
							{ kind: 'folder', id: 'f1', name: 'Holiday', how: 'inherited', decides: false },
							{ kind: 'tag', id: 't1', name: 'private', how: 'restricted', decides: true }
						]
					})
				]
			})
		);
		expect(standings()).toEqual(["Can't see"]);
		// In force first, then the rest narrowest-first: the order the resolver reads them in.
		expect(reasons()).toEqual([
			'Restricted through the tag private',
			'Shared through the folder Holiday'
		]);
		const beaten = panel()?.querySelectorAll('.reason.beaten') ?? [];
		expect([...beaten].map((each) => each.textContent?.includes('folder'))).toEqual([true]);
	});

	it('says an admin sees everything because of the role, not because of a share', async () => {
		/* Drawn as a share it would be a decision nobody made and nobody could revoke, and it would
		   send whoever read it looking for a grant that is not there. */
		await open(report({ users: [user({ id: 'u3', name: 'kate', role: 'admin', sees: true })] }));
		expect(standings()).toEqual(['Can see']);
		expect(reasons()).toEqual(['Every file, as an administrator']);
	});

	it('never leaves a yes with a blank under it, which is what an entity produces', async () => {
		/*
		 * A person, a tag or a site is on a guest's wall because one of its files can be reached,
		 * and what let that file through was said about something else, so the server has a true
		 * yes and no grant row to name (every entity on a library shared by folder).
		 *
		 * This sentence says only what the first read asked, and is the fallback: the second read
		 * names the files' own reasons, and this stands where that read found nothing or could not
		 * be made. The alternative is silence under "Can see", which reads as a bug rather than an
		 * answer.
		 */
		await open(report({ users: [user({ sees: true, through: [] })] }));
		expect(standings()).toEqual(['Can see']);
		expect(reasons()).toEqual(['Through a file of this one they can already reach']);
	});

	it('names what let the files through, which is what the sentence above stands in for', async () => {
		/* The durable answer to the same state. The first read can only say that ONE file under the
		   person is reachable; the second names a page of those files and what let each of them
		   through, collapsed to the distinct reasons. */
		mocks.fetchReachThrough.mockResolvedValue({
			reasons: [{ kind: 'folder', id: 'f1', name: 'Holiday', files: 3 }],
			files: 5,
			complete: true
		});

		await open(report({ users: [user({ sees: true, through: [] })] }), {
			type: 'person',
			id: 'p1',
			label: 'Neve Arbor'
		});
		// The second read is its own request: the panel draws as soon as the report lands and the
		// reasons arrive after it, so this waits for that extra round trip.
		await tick();
		flushSync();

		expect(reasons()).toEqual(['Through a file through the folder Holiday, which holds 3 of 5']);
	});

	it('says when it read only part of the entity, because a count reads as a total', async () => {
		/* The ceiling is on the read and not on the answer, so a panel that hid it would print a
		   number out of a page as though it were a number out of everything. */
		mocks.fetchReachThrough.mockResolvedValue({
			reasons: [{ kind: 'collection', id: 'c1', name: 'Summer', files: 200 }],
			files: 200,
			complete: false
		});

		await open(report({ users: [user({ sees: true, through: [] })] }), {
			type: 'person',
			id: 'p1',
			label: 'Neve Arbor'
		});
		// The second read is its own request: the panel draws as soon as the report lands and the
		// reasons arrive after it, so this waits for that extra round trip.
		await tick();
		flushSync();

		expect(reasons()).toEqual([
			'Through a file through the collection Summer',
			'Read from the first 200 files of this one'
		]);
	});

	it('asks for a reason only where the report has none, which is the only case it answers', async () => {
		/* One read per user and only where it can say something the first read could not. Asked
		   for every row it would be a read per user on a panel somebody opened to check one
		   thing. */
		await open(
			report({
				users: [
					user({ id: 'u2', sees: false }),
					user({ id: 'u3', name: 'kate', role: 'admin', sees: true }),
					user({ id: 'u4', name: 'lee', sees: true, through: [] })
				]
			})
		);

		expect(mocks.fetchReachThrough.mock.calls.map((call) => call[1])).toEqual(['u4']);
	});

	it('says nothing extra under a no, which is what the line above must not do', async () => {
		/* The half that stops the sentence above being printed under every row. A cannot-see with no
		   chain is the ordinary state of most of a library and has nothing to explain. */
		await open(report({ users: [user({ sees: false, through: [] })] }));
		expect(standings()).toEqual(["Can't see"]);
		expect(reasons()).toEqual([]);
	});

	it('tells a switched-off user apart from one that was never given anything', async () => {
		/* It keeps everything it was ever granted and can make no request to use any of it. "Cannot
		   see" alone reads as a decision about this thing, and that reading is wrong the moment the
		   user is switched back on, with nothing here having changed. */
		await open(report({ users: [user({ disabled: true, sees: false })] }));
		expect(standings()).toEqual(['User blocked']);
	});

	it('says what YOU have hidden at the top, and only what you hid yourself is worded as yours', async () => {
		/* Hiding is the caller's own and reported once, above the rows, never per user, which
		   would be handing over somebody else's choice about their own screen. */
		await open(report({ hidden: false, concealed: true }));
		expect(text()).toContain('Something above this is hiding it from you');

		document.querySelectorAll('.reach-sheet').forEach((each) => each.remove());
		await open(report({ hidden: true, concealed: true }));
		expect(text()).toContain('You have hidden this');
	});

	it('says nothing about hiding when nothing is concealing it', async () => {
		/* The half that stops the two above passing on a line that is always drawn. It is also the
		   honest answer while the vault is shut: the server reports no concealment either way, and
		   what the panel must not do is invent a difference between those. */
		await open(report());
		expect(text()).not.toContain('hiding it from you');
		expect(text()).not.toContain('You have hidden this');
	});

	it('says so when the report could not be read, rather than looking like a clean no', async () => {
		mocks.fetchReach.mockRejectedValue(new Error('no'));
		host = document.createElement('div');
		document.body.append(host);
		mount(VisibilityDialog, { target: host, props: { open: true, target: TARGET } });
		flushSync();
		await tick();
		await tick();
		flushSync();
		expect(text()).toContain("couldn't be loaded");
		expect(standings()).toEqual([]);
	});
});

describe('outside this device', () => {
	const outside = {
		enrich_refused_here: false,
		enrich_refused: false,
		enriched_at: null,
		enriched_by: null,
		swap_refused_here: false,
		swap_refused: false
	};

	it('says where the thing stands with stash-box lookups and swaps, beside a switch for each', async () => {
		await open(report({ outside: { ...outside, swap_refused: true } }));
		const switches = [...(panel()?.querySelectorAll('[role="switch"]') ?? [])];
		expect(switches.map((one) => one.getAttribute('aria-label'))).toEqual([
			"Don't enrich",
			"Don't swap"
		]);
		expect(panel()?.querySelector('.section-heading.band h3')?.textContent?.trim()).toBe(
			'Outside this device'
		);
		expect(text()).toContain('No stash-box has filled anything in about it yet.');
		// Out by something it is filed under: the switch on the file itself is off, and it says why.
		expect(switches[1]?.getAttribute('aria-checked')).toBe('false');
		expect(text()).toContain("Kept out of swaps by something it's filed under.");
	});

	/* The settings column (15rem) would leave the words a third of this sheet: three lines a sentence. */
	it('gives the two switches their own width, not the settings column', async () => {
		await open(report({ outside }));
		const section = panel()?.querySelector('.outside') as HTMLElement;
		const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
		const { default: source } = await import('./VisibilityDialog.svelte?raw');
		applyStyles(source, section);
		expect(getComputedStyle(section).getPropertyValue('--settings-control-col').trim()).toBe(
			'auto'
		);
		removeStyles();
	});

	it("says Don't enrich keeps a thing out of swaps as well", async () => {
		await open(
			report({
				outside: {
					...outside,
					enrich_refused_here: true,
					enrich_refused: true,
					swap_refused: true
				}
			})
		);
		expect(text()).toContain(
			"Kept out of swaps too, because Don't enrich keeps everything about it on this device."
		);
	});

	it('draws nothing about outside for a thing neither is ever told about', async () => {
		await open(report({ outside: null }), { type: 'folder', id: 'f1', label: 'clips' });
		expect(panel()?.querySelector('[role="switch"]')).toBeNull();
		expect(text()).not.toContain('Outside this device');
	});
});
