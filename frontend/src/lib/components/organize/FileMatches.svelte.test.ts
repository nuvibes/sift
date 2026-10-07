/* The one-file door into the stash-box pile, and the four ways an empty sheet can be empty.
 *
 * What is held here is the part somebody acts on. Two of them matter more than the rest.
 *
 * The first is `blocked`. An empty sheet would report a fact about the FILE (nobody recognised
 * it) when the real state may be that nothing could be asked at all. Those look identical on screen
 * and are opposite situations: one is finished, the other is a thing to go and fix. The four causes
 * are ordered from the most specific to the least because each makes the ones after it meaningless,
 * so the order is part of the answer rather than a tidiness.
 *
 * The second is the prune at the press. A name is ticked while its row is on the sheet and the row
 * is then taken out; sending the name anyway invents a row off the back of a match nobody agreed
 * to. The same guard exists in the pile and is asserted there; this is the copy on the file sheet,
 * held here so it is watched too.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { Match, Missing } from '$lib/entity/tagger.svelte';
import type { StashBox } from '$lib/settings-ui/stash-boxes.svelte';

const mocks = vi.hoisted(() => ({
	waitingFor: vi.fn(),
	answeredFor: vi.fn(),
	apply: vi.fn(),
	refuse: vi.fn(),
	enrichFiles: vi.fn(),
	shown: vi.fn(),
	undoDecision: vi.fn(),
	boxes: [] as unknown[],
	holder: null as { items: unknown[] } | null,
	labels: [] as { subject: string; key: string; label: string }[]
}));

vi.mock('$lib/entity/tagger.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/tagger.svelte')>()),
	waitingFor: mocks.waitingFor,
	answeredFor: mocks.answeredFor,
	apply: mocks.apply,
	refuse: mocks.refuse
}));

vi.mock('$lib/entity/enrich-many.svelte', () => ({ enrichFiles: mocks.enrichFiles }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.shown } }));
vi.mock('$lib/api/history', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/history')>()),
	undoDecision: mocks.undoDecision
}));

/* The store, stood in for by one holding reactive state a test can assign to. Reactive and not a
   plain array on purpose: `blocked` is a derivation over `items`, and a derivation whose source
   never changes computes once and caches, which would make every one of the four causes below
   report whatever the first read happened to see. */
vi.mock('$lib/settings-ui/stash-boxes.svelte', async () => {
	const { reactiveProps } = await import('$lib/design/testing.svelte');
	const holder = reactiveProps({ items: [] as unknown[] });
	mocks.holder = holder;
	return {
		StashBoxes: class {
			get items(): unknown[] {
				return holder.items;
			}
			async load(): Promise<void> {
				holder.items = mocks.boxes;
			}
			follow(): void {}
		}
	};
});

/* The registry, so a field draws under its label rather than its column name. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(subject: string): never[] {
			return mocks.labels.filter((one) => one.subject === subject) as never[];
		}
	}
	return { ...real, fields: new Standing() };
});

import FileMatches from './FileMatches.svelte';

const jane: Missing = { name: 'Jane', kind: 'person' };
const neve: Missing = { name: 'Neve', kind: 'tag' };

function box(over: Partial<StashBox> = {}): StashBox {
	return {
		id: 'box-1',
		name: 'StashDB',
		endpoint: 'https://example.invalid/graphql',
		enabled: true,
		has_key: true,
		key_ready: true,
		route: null,
		sites_are: 'site',
		slug: 'stashdb',
		requests_per_minute: 60,
		...over
	};
}

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
let applied = 0;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.labels = [];
	mocks.boxes = [box()];
	if (mocks.holder) mocks.holder.items = [];
	mocks.waitingFor.mockResolvedValue({ matches: [], total: 0 });
	mocks.answeredFor.mockResolvedValue({ matches: [], total: 0 });
	mocks.apply.mockResolvedValue({ files: 1, fields: 1, created: 0, decision_id: 'd-1' });
	mocks.refuse.mockResolvedValue({ files: 1, fields: 0, created: 0, decision_id: 'd-2' });
	mocks.enrichFiles.mockResolvedValue(null);
	applied = 0;
	/* Fake, for every test rather than for the three about the watch.
	 *
	 * Opening the sheet with nothing already held runs the watch (six looks a second apart) and
	 * a test that does not wind that forward measures the spinner. Five of these read an empty
	 * sheet's SENTENCE, which is only drawn once the looking has finished, so under real timers they
	 * would all assert against "Asking each switched-on stash-box". */
	vi.useFakeTimers();
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	// The sheet is portalled to the end of the document, so it outlives the host it was written in.
	document.body.innerHTML = '';
	vi.useRealTimers();
});

async function draw(assetId = 'a-1'): Promise<void> {
	drawn = mount(FileMatches, {
		target: host,
		props: { open: true, assetId, onapplied: () => (applied += 1) } as never
	}) as Record<string, unknown>;
	flushSync();
	// Past the whole watch. A sheet that has something waiting never reaches a timer at all; one
	// that has not is six seconds of looking, and both have to be finished before anything is read.
	await vi.advanceTimersByTimeAsync(20_000);
	flushSync();
}

/** Everything on the sheet, which is drawn at the end of the document rather than in the host. */
function said(): string {
	return document.body.textContent ?? '';
}

function all(): HTMLButtonElement[] {
	return [...document.body.querySelectorAll('button')];
}

function button(startsWith: string): HTMLButtonElement {
	const found = all().find((one) => wordsOn(one).startsWith(startsWith));
	if (!found) throw new Error(`no button opening "${startsWith}"`);
	return found;
}

function named(startsWith: string): HTMLButtonElement {
	const found = all().find((one) => (one.getAttribute('aria-label') ?? '').startsWith(startsWith));
	if (!found) throw new Error(`no control labelled "${startsWith}"`);
	return found;
}

it('shows what is already waiting without asking a stash-box again', async () => {
	// A sweep may have found this days ago. Asking again to learn what is already known is a
	// request to somebody else's service that nobody needed.
	mocks.waitingFor.mockResolvedValue({ matches: [match()], total: 1 });

	await draw();

	expect(said()).toContain('A Clip');
	expect(mocks.enrichFiles).not.toHaveBeenCalled();
});

it('queues the per-file job when nothing is waiting yet', async () => {
	mocks.waitingFor.mockResolvedValue({ matches: [], total: 0 });

	await draw();

	expect(mocks.enrichFiles).toHaveBeenCalledWith(['a-1'], { quiet: true });
});

it('puts a refusal on the sheet rather than in a toast that slides over it', async () => {
	// "Switched off in Settings" is the one answer somebody opening this needs, and a toast puts it
	// on top of the sheet for three seconds and then takes it away.
	mocks.enrichFiles.mockResolvedValue('Matching is switched off.');

	await draw();

	expect(said()).toContain('Matching is switched off.');
	expect(mocks.shown).not.toHaveBeenCalled();
});

it('gives up after six looks rather than spinning on a box that is down', async () => {
	// A box that is slow or down produces nothing to wait for, so this stops and says plainly that
	// nothing came back rather than turning the sheet into a spinner nobody can leave.
	mocks.waitingFor.mockResolvedValue({ matches: [], total: 0 });

	await draw();

	// The first read is the one that checks what is already held; the six after it are the watch.
	expect(mocks.waitingFor).toHaveBeenCalledTimes(7);
});

it('stops looking the moment something arrives', async () => {
	mocks.waitingFor
		.mockResolvedValueOnce({ matches: [], total: 0 })
		.mockResolvedValueOnce({ matches: [], total: 0 })
		.mockResolvedValue({ matches: [match()], total: 1 });

	await draw();

	expect(mocks.waitingFor).toHaveBeenCalledTimes(3);
	expect(said()).toContain('A Clip');
});

it('tells a finished question with no answer from one still in flight', async () => {
	await draw();

	expect(said()).toContain('No stash-box recognized this file');
});

it('says no stash-box is set up at all, which is not a fact about the file', async () => {
	mocks.boxes = [];

	await draw();

	expect(said()).toContain('No stash-box is set up yet');
});

it('says every box is turned off before it blames the file', async () => {
	mocks.boxes = [box({ enabled: false }), box({ id: 'box-2', enabled: false })];

	await draw();

	expect(said()).toContain('Every stash-box is turned off');
});

it('names the locked key first, because a key that will not open is not a box switched off', async () => {
	// The most specific cause wins. A restart leaves every saved key sealed and nothing else wrong,
	// and "add a key in Settings" sends somebody to type in one they already have.
	mocks.boxes = [box({ has_key: true, key_ready: false })];

	await draw();

	expect(said()).toContain("a key that's locked because Sift restarted");
	expect(said()).toContain('Nothing was lost');
});

it('counts the locked boxes rather than saying "the one" when there are several', async () => {
	mocks.boxes = [
		box({ has_key: true, key_ready: false }),
		box({ id: 'box-2', has_key: true, key_ready: false })
	];

	await draw();

	expect(said()).toContain('All 2 stash-boxes that are turned on have a key');
});

/* The last two causes are DISJOINT, and that is worth saying rather than re-deriving.
 *
 * "every key is locked" needs every switched-on box to HAVE a key; "no box has a key" needs none of
 * them to. They cannot both be true, so swapping those two lines changes no answer and a mutation
 * that swaps them survives, correctly. The order that IS load-bearing is the pair above them:
 * with no box switched on, `locked.length === on.length` is `0 === 0`, so the switched-off cause
 * has to come first or an install with everything turned off is told its keys are sealed. Both of
 * those are mutation-checked; these two are checked for what they SAY, which is all there is. */
it('says a key is missing only when no box that is on has one', async () => {
	mocks.boxes = [box({ has_key: false, key_ready: false })];

	await draw();

	expect(said()).toContain("No stash-box that's turned on has a key");
});

it('blames nothing when a box could have been asked and simply did not know the file', async () => {
	// One box on, keyed and open. The empty answer is then genuinely about the file, and a sentence
	// about Settings would send somebody to fix something that is not broken.
	mocks.boxes = [box()];

	await draw();

	expect(said()).toContain('No stash-box recognized this file');
	expect(said()).not.toContain('in Settings');
});

it('counts the fields it would write and leaves the disagreements out of that number', async () => {
	mocks.waitingFor.mockResolvedValue({
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

	expect(said()).toContain('1');
	expect(said()).toContain('field would be written');
});

it('counts a field that names only new rows once one of them is ticked', async () => {
	// Four new tags start unticked, and a tags field with nothing ticked writes nothing: the count
	// above the button is the count the receipt will say.
	const beach: Missing = { name: 'Beach', kind: 'tag' };
	mocks.waitingFor.mockResolvedValue({
		matches: [
			match({
				changes: [
					{ key: 'title', outcome: 'write', mine: null, theirs: 'A Clip', needs: [], stands: true },
					{
						key: 'tags',
						outcome: 'write',
						mine: [],
						theirs: ['Beach'],
						needs: [beach],
						stands: false
					}
				],
				creates: [beach]
			})
		],
		total: 1
	});
	await draw();

	expect(said()).toContain('1 field would be written');

	named('Create Beach').click();
	flushSync();

	expect(said()).toContain('2 fields would be written');
});

it('takes a row out of the press without reading the server again', async () => {
	mocks.waitingFor.mockResolvedValue({
		matches: [match(), match({ box_id: 'box-2', box_name: 'PMVStash' })],
		total: 2
	});
	await draw();
	mocks.waitingFor.mockClear();

	named('Leave out').click();
	flushSync();

	button('Apply').click();
	flushSync();
	await tick();

	expect(mocks.apply).toHaveBeenCalledWith([expect.objectContaining({ box_id: 'box-2' })], [], []);
	expect(mocks.waitingFor).not.toHaveBeenCalled();
});

it('will not apply when every row has been taken out', async () => {
	mocks.waitingFor.mockResolvedValue({ matches: [match()], total: 1 });
	await draw();

	named('Leave out').click();
	flushSync();

	expect(button('Apply').disabled).toBe(true);
});

it('invents nothing nobody ticked', async () => {
	// Creating rows out of somebody else's vocabulary is the thing this feature refuses to do on
	// its own, and the default answer to every name is no.
	mocks.waitingFor.mockResolvedValue({ matches: [match({ creates: [jane, neve] })], total: 1 });
	await draw();

	button('Apply').click();
	flushSync();
	await tick();

	expect(mocks.apply).toHaveBeenCalledWith([expect.objectContaining({ asset_id: 'a-1' })], [], []);
});

it('sends the names that were ticked, and only those', async () => {
	mocks.waitingFor.mockResolvedValue({ matches: [match({ creates: [jane, neve] })], total: 1 });
	await draw();

	named('Create Neve').click();
	flushSync();
	button('Apply').click();
	flushSync();
	await tick();

	expect(mocks.apply).toHaveBeenCalledWith(
		[expect.objectContaining({ asset_id: 'a-1' })],
		[neve],
		[]
	);
});

it('does not create a name whose only row was taken out of the press', async () => {
	// The prune at the press. A name ticked while it was on offer, whose row then left, would
	// otherwise invent a person off the back of a match nobody agreed to.
	mocks.waitingFor.mockResolvedValue({
		matches: [
			match({ creates: [jane] }),
			match({ box_id: 'box-2', box_name: 'PMVStash', creates: [neve] })
		],
		total: 2
	});
	await draw();

	named('Create Jane').click();
	flushSync();
	named('Leave out').click();
	flushSync();
	button('Apply').click();
	flushSync();
	await tick();

	expect(mocks.apply).toHaveBeenCalledWith([expect.objectContaining({ box_id: 'box-2' })], [], []);
});

it('sends the answers to the disagreements, addressed to the rows they were shown on', async () => {
	mocks.waitingFor.mockResolvedValue({
		matches: [
			match({
				changes: [
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

	named('Use 1991').click();
	flushSync();
	button('Apply').click();
	flushSync();
	await tick();

	expect(mocks.apply).toHaveBeenCalledWith(
		[expect.objectContaining({ asset_id: 'a-1' })],
		[],
		[{ asset_id: 'a-1', box_id: 'box-1', key: 'released_on', take: 'theirs' }]
	);
});

it('tells the screen behind it that something was written', async () => {
	mocks.waitingFor.mockResolvedValue({ matches: [match()], total: 1 });
	await draw();

	button('Apply').click();
	flushSync();
	await tick();
	await tick();

	expect(applied).toBe(1);
	expect(mocks.shown).toHaveBeenCalledWith('One field written', { tone: 'success' });
});

it('says what went wrong on the sheet when the write is refused', async () => {
	mocks.waitingFor.mockResolvedValue({ matches: [match()], total: 1 });
	mocks.apply.mockRejectedValue(new Error('no'));
	await draw();

	button('Apply').click();
	flushSync();
	await tick();
	await tick();

	expect(said()).toContain("That couldn't be applied.");
});

it('refuses every row it was shown, not only the ones still ticked', async () => {
	// "Discard" is an answer about the file, and a row taken out of the press is still a row this
	// file was offered. Refusing only the ticked ones would leave the rest to be asked about again.
	mocks.waitingFor.mockResolvedValue({
		matches: [match(), match({ box_id: 'box-2', box_name: 'PMVStash' })],
		total: 2
	});
	await draw();

	named('Leave out').click();
	flushSync();
	button('Discard').click();
	flushSync();
	await tick();

	expect(mocks.refuse).toHaveBeenCalledWith([
		expect.objectContaining({ box_id: 'box-1' }),
		expect.objectContaining({ box_id: 'box-2' })
	]);
});

it('says what went wrong on the sheet when the refusal is refused', async () => {
	mocks.waitingFor.mockResolvedValue({ matches: [match()], total: 1 });
	mocks.refuse.mockRejectedValue(new Error('no'));
	await draw();

	button('Discard').click();
	flushSync();
	await tick();
	await tick();

	expect(said()).toContain("That couldn't be discarded.");
});

it('offers no answers at all while there is nothing to answer about', async () => {
	// The foot is drawn only once there are rows. Two buttons over an empty sheet would offer to
	// apply nothing and to refuse nothing.
	await draw();

	expect(all().some((one) => (one.textContent ?? '').includes('Apply'))).toBe(false);
});

it('says what a box already matched and applied, never that nothing recognized the file', async () => {
	/* Nothing waits for a file whose match was applied, so reading the waiting rows alone would say
	   "No stash-box recognized this file" about a file a box had matched. */
	mocks.answeredFor.mockResolvedValue({
		matches: [match({ box_name: 'FansDB', state: 'applied', decided_at: 1_700_000_000 })],
		total: 1
	});

	await draw();

	expect(said()).toContain('FansDB matched this file to A Clip. Applied');
	expect(said()).not.toContain('No stash-box recognized this file');
	expect(mocks.enrichFiles, 'asked a box to learn what was already known').not.toHaveBeenCalled();

	button('Identify again').click();
	await vi.advanceTimersByTimeAsync(20_000);
	flushSync();

	expect(mocks.enrichFiles).toHaveBeenCalledWith(['a-1'], { quiet: true, again: true });
	// Nothing new waiting afterwards: said, rather than the same sheet drawn again as if nothing
	// had been pressed.
	expect(said()).toContain('Asked again. Nothing new came back, so this answer still stands.');
});

it("discards an answer already applied with the waiting answer's own press, and offers its Undo", async () => {
	/* An applied answer can be taken back from this sheet, not only by undoing the apply from the
	   stash-box page. */
	const applied_ = match({ box_name: 'FansDB', state: 'applied', decided_at: 1_700_000_000 });
	mocks.answeredFor.mockResolvedValue({ matches: [applied_], total: 1 });
	mocks.undoDecision.mockResolvedValue({});

	await draw();
	button('Discard').click();
	await vi.advanceTimersByTimeAsync(0);
	flushSync();

	expect(mocks.refuse).toHaveBeenCalledWith([applied_]);
	expect(applied, 'the screen behind was not told').toBeGreaterThan(0);
	const [message, options] = mocks.shown.mock.calls.at(-1) as [
		string,
		{ action?: { run: () => void } }
	];
	expect(message).toBe('Discarded. Sift took off what FansDB said about this file.');
	options.action?.run();
	await vi.advanceTimersByTimeAsync(0);
	expect(mocks.undoDecision).toHaveBeenCalledWith('d-2');
});

it('offers no Discard beside an answer already discarded', async () => {
	mocks.answeredFor.mockResolvedValue({
		matches: [match({ box_name: 'FansDB', state: 'refused', decided_at: 1_700_000_000 })],
		total: 1
	});

	await draw();

	expect(all().some((one) => wordsOn(one).startsWith('Discard'))).toBe(false);
});
