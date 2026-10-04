/* Files a stash-box recognized, and the one press that settles a page of them.
 *
 * A bulk confirm nobody can see the consequences of is a leap of faith with a progress bar on it,
 * so what these hold is the arithmetic ABOVE the button: how many fields would really be written,
 * which names would have to be invented, and what taking a row out does to both. A count that
 * included the conflicts would promise writes that are not going to happen.
 *
 * The rest is the way in. Nothing arrives in this pile on its own (it is filled by a sweep) so
 * a panel with no way to start one is an empty screen telling somebody to wait for something that
 * will never begin.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { Match } from '$lib/entity/tagger.svelte';

const mocks = vi.hoisted(() => ({
	waiting: vi.fn(),
	apply: vi.fn(),
	refuse: vi.fn(),
	labels: [] as { subject: string; key: string; label: string }[]
}));

vi.mock('$lib/entity/tagger.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/tagger.svelte')>()),
	waiting: mocks.waiting,
	apply: mocks.apply,
	refuse: mocks.refuse
}));

/* The registry, stood in for by a real one whose single request is replaced. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(subject: string): never[] {
			return mocks.labels.filter((one) => one.subject === subject) as never[];
		}
	}
	return { ...real, fields: new Standing() };
});

import TaggerPanel from './TaggerPanel.svelte';
import { page } from '$app/state';
import type { PagerProps } from '$lib/components/common/Pager.svelte';

/* Two rows a match would have to invent. A person and a tag, because the kind travels with the
   name and a list that lost it would offer one tick for two different rows. */
const jane = { name: 'Jane', kind: 'person' };
const neve = { name: 'Neve', kind: 'tag' };

function match(over: Partial<Match> = {}): Match {
	return {
		asset_id: 'a-1',
		box_id: 'box-1',
		box_name: 'StashDB',
		remote_id: 'r-1',
		grade: 'certain',
		state: 'waiting',
		found_at: 1,
		decided_at: null,
		art: null,
		record: {
			source_id: 'box-1',
			source_name: 'StashDB',
			remote_id: 'r-1',
			subject: 'asset',
			name: 'A Clip',
			disambiguation: null,
			image_url: null,
			file_count: 1,
			fields: {},
			confidence: 1
		} as Match['record'],
		changes: [
			{ key: 'title', outcome: 'write', mine: null, theirs: 'A Clip', needs: [], stands: true }
		],
		creates: [],
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.labels = [];
	mocks.waiting.mockResolvedValue({ matches: [], total: 0 });
	mocks.apply.mockResolvedValue({});
	mocks.refuse.mockResolvedValue({});
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

async function draw(): Promise<void> {
	drawn = mount(TaggerPanel, { target: host }) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

function button(startsWith: string): HTMLButtonElement {
	const found = [...host.querySelectorAll('button')].find((one) =>
		(one.textContent ?? '').trim().startsWith(startsWith)
	);
	if (!found) throw new Error(`no button opening "${startsWith}"`);
	return found;
}

/*
 * What a control SAYS, with the icon taken off.
 *
 * An icon is a ligature, so it is part of the element's own text, and it is a private-use
 * codepoint rather than whitespace, which is why trimming alone leaves it behind.
 */
function said(one: Element): string {
	return (one.textContent ?? '')
		.replace(/[^\x20-\x7e]/g, '')
		.replace(/\s+/g, ' ')
		.trim();
}

/** A bits-ui select opens on the keyboard. A bare `click()` does not reach it in jsdom. */
function open(trigger: HTMLElement): void {
	trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
}

/** The row ticks carry their meaning in the label rather than in any text. */
function named(startsWith: string): HTMLButtonElement {
	const found = [...host.querySelectorAll('button')].find((one) =>
		(one.getAttribute('aria-label') ?? '').startsWith(startsWith)
	);
	if (!found) throw new Error(`no control labelled "${startsWith}"`);
	return found;
}

it('says why an empty list is empty when every recognised file has been answered', async () => {
	/* The count of answered files tells an empty waiting tab from a feature nobody switched on. */
	mocks.waiting.mockResolvedValue({ matches: [], total: 0, answered: 283 });
	await draw();
	expect((host.textContent ?? '').replace(/\s+/g, ' ')).toContain(
		'All 283 files a stash-box recognized have been answered.'
	);
	expect(host.querySelector('a[href="/browse?enriched=stash"]')).not.toBeNull();
});

it('keeps the switch-it-on sentence when nothing was ever recognised', async () => {
	mocks.waiting.mockResolvedValue({ matches: [], total: 0, answered: 0 });
	await draw();
	// Whitespace folded: the sentence wraps wherever the formatter puts its line breaks.
	expect(host.textContent?.replace(/\s+/g, ' ')).toContain('Turn on matching first');
});

it('carries no heading of its own under the page title that already names the pile', async () => {
	await draw();

	const headings = [...host.querySelectorAll('h1, h2, h3')].map((one) => one.textContent ?? '');
	expect(headings.some((one) => one.includes('Files a stash-box recognized'))).toBe(false);
});

it('starts with every row in the press, because the screen exists to settle a page', async () => {
	// Starting with nothing ticked would make the ordinary case forty presses before the one that
	// counts.
	mocks.waiting.mockResolvedValue({ matches: [match(), match({ asset_id: 'a-2' })], total: 2 });

	await draw();

	expect(host.textContent).toContain('2 of 2 selected');
});

it('counts the fields it would write, and leaves the conflicts out of that number', async () => {
	// A conflict is a question, not a write. Counting one would promise a change that is not going
	// to happen, above a button whose whole job is to say what it does.
	mocks.waiting.mockResolvedValue({
		matches: [
			match({
				changes: [
					{ key: 'title', outcome: 'write', mine: null, theirs: 'A Clip', needs: [], stands: true },
					{
						key: 'released_on',
						outcome: 'conflict',
						mine: '1990',
						theirs: '1991',
						needs: [],
						stands: true
					}
				]
			})
		],
		total: 1
	});

	await draw();

	expect(host.textContent).toContain('1');
	expect(host.textContent).toContain('field would be written');
});

it('takes a row out of the press without reading it off the server again', async () => {
	mocks.waiting.mockResolvedValue({ matches: [match(), match({ asset_id: 'a-2' })], total: 2 });
	await draw();
	mocks.waiting.mockClear();

	named('Leave out').click();
	flushSync();

	expect(host.textContent).toContain('1 of 2 selected');
	expect(mocks.waiting).not.toHaveBeenCalled();
});

it('lists the names it would invent, once each, rather than once per file', async () => {
	// The same person turns up on twenty files from one release. "This would create 20 people"
	// beside a list of one name is a sentence nobody believes twice.
	mocks.waiting.mockResolvedValue({
		matches: [match({ creates: [jane] }), match({ asset_id: 'a-2', creates: [jane, neve] })],
		total: 2
	});

	await draw();

	expect(host.textContent).toContain("2 entries this library doesn't have");
	expect(named('Create Jane')).toBeTruthy();
	expect(named('Create Neve')).toBeTruthy();
});

it('does not offer to create anything when there is nothing to create', async () => {
	mocks.waiting.mockResolvedValue({ matches: [match()], total: 1 });

	await draw();

	expect(host.textContent).not.toContain("this library doesn't have");
});

it('sends only the rows still in the press, and invents nothing nobody ticked', async () => {
	// Nothing ticked is a real answer and it is the default one: creating rows out of somebody
	// else's vocabulary is what this feature refuses to do on its own.
	mocks.waiting.mockResolvedValue({ matches: [match(), match({ asset_id: 'a-2' })], total: 2 });
	await draw();
	named('Leave out').click();
	flushSync();

	button('Apply to').click();
	flushSync();

	expect(mocks.apply).toHaveBeenCalledWith([expect.objectContaining({ asset_id: 'a-2' })], [], []);
});

it('sends the names that were ticked, and only those', async () => {
	// Why there is a tick per name rather than one for all: half of what a stash-box names is worth
	// having and half is somebody else's filing, and one answer for both loses whichever half you
	// wanted.
	mocks.waiting.mockResolvedValue({ matches: [match({ creates: [jane, neve] })], total: 1 });
	await draw();

	named('Create Neve').click();
	flushSync();
	button('Apply to').click();
	flushSync();

	expect(mocks.apply).toHaveBeenCalledWith(
		[expect.objectContaining({ asset_id: 'a-1' })],
		[neve],
		[]
	);
});

it('does not create a name whose only row was taken out of the press', async () => {
	// A name is ticked while it is on offer and the row it came from is then left out. Sending it
	// anyway would invent a person off the back of a match nobody agreed to, and it is exactly
	// the drift a list held beside the rows, rather than derived from them, produces.
	mocks.waiting.mockResolvedValue({
		matches: [match({ creates: [jane] }), match({ asset_id: 'a-2', creates: [neve] })],
		total: 2
	});
	await draw();

	// Jane is named by a-1 and by nothing else; `named` takes the first match, which is a-1's own
	// tick. So ticking her and then removing her row leaves a name on offer nowhere.
	named('Create Jane').click();
	flushSync();
	named('Leave out').click();
	flushSync();
	button('Apply to').click();
	flushSync();

	expect(mocks.apply).toHaveBeenCalledWith([expect.objectContaining({ asset_id: 'a-2' })], [], []);
});

it('refuses the same rows the apply would have taken', async () => {
	mocks.waiting.mockResolvedValue({ matches: [match()], total: 1 });
	await draw();

	button('Discard').click();
	flushSync();

	expect(mocks.refuse).toHaveBeenCalledWith([expect.objectContaining({ asset_id: 'a-1' })]);
});

/*
 * No "Scan the library" press on the pile: the stash-box sweep fills it once new files have their
 * fingerprints, so the empty state says that rather than pointing at a button.
 */
it('offers no scan press, and says what fills the pile without one', async () => {
	await draw();

	expect(host.textContent).not.toContain('Scan the library');
	expect(host.textContent?.replace(/\s+/g, ' ')).toContain(
		'Sift looks up new files on the stash-boxes once their fingerprints are ready'
	);
});

it('calls a field what the record calls it, and falls back to the key', async () => {
	mocks.labels = [{ subject: 'asset', key: 'title', label: 'Title' }];
	mocks.waiting.mockResolvedValue({
		matches: [
			match({
				changes: [
					{ key: 'title', outcome: 'write', mine: null, theirs: 'A Clip', needs: [], stands: true },
					{
						key: 'invented_later',
						outcome: 'write',
						mine: null,
						theirs: 'x',
						needs: [],
						stands: true
					}
				]
			})
		],
		total: 1
	});

	await draw();

	expect(host.textContent).toContain('Title');
	expect(host.textContent).toContain('invented_later');
});

it('says how many are waiting in all when the page is not the whole pile', async () => {
	mocks.waiting.mockResolvedValue({ matches: [match()], total: 400 });

	await draw();

	expect(host.textContent).toContain('400 waiting in all.');
});

it('says so when the pile could not be read', async () => {
	mocks.waiting.mockRejectedValue(new Error('offline'));

	await draw();

	expect(host.textContent).toContain("That didn't work.");
});

it('leaves a disagreement alone until somebody answers it', async () => {
	/*
	 * A conflict on a file can be settled here by taking the stash-box's answer; the screen that
	 * settles disagreements on records reads linked people, sites and tags, never files.
	 *
	 * Keeping your own is still the default and sends nothing: a field rewritten with the value it
	 * already holds is a change in every log that watches for one, for a decision that changed
	 * nothing.
	 */
	mocks.waiting.mockResolvedValue({
		matches: [
			match({
				changes: [
					{
						key: 'site',
						outcome: 'conflict',
						mine: 'Pmvhaven',
						theirs: 'hollowgrain',
						needs: [],
						stands: true
					}
				]
			})
		],
		total: 1
	});
	await draw();

	expect(host.textContent).toContain('keeping what is here');
	expect(named('Keep Pmvhaven').getAttribute('aria-pressed')).toBe('true');
	expect(named('Use hollowgrain').getAttribute('aria-pressed')).toBe('false');

	button('Apply to').click();
	flushSync();
	expect(mocks.apply.mock.calls[0][2]).toEqual([]);
});

it('sends the answer somebody gave, and nothing for the ones they left', async () => {
	mocks.waiting.mockResolvedValue({
		matches: [
			match({
				changes: [
					{
						key: 'site',
						outcome: 'conflict',
						mine: 'Pmvhaven',
						theirs: 'hollowgrain',
						needs: [],
						stands: true
					},
					{ key: 'title', outcome: 'conflict', mine: 'Old', theirs: 'New', needs: [], stands: true }
				]
			})
		],
		total: 1
	});
	await draw();

	named('Use hollowgrain').click();
	flushSync();
	expect(host.textContent).toContain('taking theirs');

	button('Apply to').click();
	flushSync();
	expect(mocks.apply.mock.calls[0][2]).toEqual([
		{ asset_id: 'a-1', box_id: 'box-1', key: 'site', take: 'theirs' }
	]);
});

it('swaps rather than adds on a field that holds one value', async () => {
	// A conflict only ever happens on a field that holds ONE value: a field holding many merges
	// the two answers without asking. So pressing one answer here drops the other, and there is no
	// state in which both are kept: promising one would promise a write that puts "a, b" where a
	// single name goes.
	mocks.waiting.mockResolvedValue({
		matches: [
			match({
				changes: [
					{
						key: 'site',
						outcome: 'conflict',
						mine: 'Pmvhaven',
						theirs: 'hollowgrain',
						needs: [],
						stands: true
					}
				]
			})
		],
		total: 1
	});
	await draw();

	named('Use hollowgrain').click();
	flushSync();
	expect(named('Keep Pmvhaven').getAttribute('aria-pressed')).toBe('false');
	expect(host.textContent).not.toContain('keeping both');
});

it('never lets a disagreement end with neither answer kept', async () => {
	// Pressing the only one that is on would leave the field with nothing chosen, which is not an
	// answer: one of the two always survives.
	//
	// The fixture is a single-valued field on purpose: a list field is merged, both answers kept
	// without asking, so it is never among the conflicts, and a test built on one would describe a
	// screen nobody can reach.
	mocks.waiting.mockResolvedValue({
		matches: [
			match({
				changes: [
					{
						key: 'site',
						outcome: 'conflict',
						mine: 'Pmvhaven',
						theirs: 'hollowgrain',
						needs: [],
						stands: true
					}
				]
			})
		],
		total: 1
	});
	await draw();

	named('Keep Pmvhaven').click();
	flushSync();

	expect(named('Keep Pmvhaven').getAttribute('aria-pressed')).toBe('true');
	expect(host.textContent).toContain('keeping what is here');
});

it('sends nothing for a disagreement answered back to keeping your own', async () => {
	// Pressed away and pressed back. The field ends where it started, so there is nothing to write,
	// and writing it anyway would be a change in every log that watches for one, for a decision
	// that changed nothing.
	mocks.waiting.mockResolvedValue({
		matches: [
			match({
				changes: [
					{
						key: 'site',
						outcome: 'conflict',
						mine: 'Pmvhaven',
						theirs: 'hollowgrain',
						needs: [],
						stands: true
					}
				]
			})
		],
		total: 1
	});
	await draw();

	named('Use hollowgrain').click();
	flushSync();
	named('Keep Pmvhaven').click();
	flushSync();
	expect(host.textContent).toContain('keeping what is here');

	button('Apply to').click();
	flushSync();
	expect(mocks.apply.mock.calls[0][2]).toEqual([]);
});

it('keeps the list on screen while it reloads after a decision', async () => {
	/* The no-blink guard: the same `loading && items.length === 0` condition the other panels
	 * carry, held here by a test because the pile is empty on a fresh library and cannot be
	 * reached by looking. Applying a page
	 * re-reads the list, and drawing the skeleton again while it does makes the screen blink out and
	 * back for every single answer, which is the one thing somebody working through a queue does
	 * over and over.
	 *
	 * The reload after the press never resolves here, so what is asserted is what a person is
	 * looking at WHILE they wait, which is the whole of the behaviour.
	 */
	mocks.waiting.mockResolvedValue({ matches: [match()], total: 1 });
	await draw();
	expect(host.textContent).toContain('A Clip');

	mocks.waiting.mockReturnValue(new Promise(() => {}));
	button('Apply to').click();
	await tick();
	flushSync();
	await tick();
	flushSync();

	expect(host.textContent, 'the row went away while the list was re-read').toContain('A Clip');
	expect(
		host.querySelector('.bone'),
		'the skeleton came back over a list already drawn'
	).toBeNull();
});

it('names what an empty page holds, on both tabs', async () => {
	/* The pager says "No <noun>", so it is handed a noun. */
	const pagers: (PagerProps | null)[] = [];
	const drawOn = async (search: string) => {
		(page as { url: URL }).url = new URL(`http://localhost/organize/tagger${search}`);
		drawn = mount(TaggerPanel, {
			target: host,
			props: { onpaging: (pager: PagerProps | null) => void pagers.push(pager) }
		}) as Record<string, unknown>;
		flushSync();
		await tick();
		await tick();
		flushSync();
	};
	try {
		await drawOn('');
		expect(pagers.at(-1)?.noun).toBe('matches to review');
		unmount(drawn!);
		await drawOn('?show=answered');
		expect(pagers.at(-1)?.noun).toBe('answered matches');
	} finally {
		(page as { url: URL }).url = new URL('http://localhost/');
	}
});
