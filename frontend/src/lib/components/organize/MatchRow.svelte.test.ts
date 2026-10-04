/* One stash-box's claim about one file, and everything applying it would change.
 *
 * This row is the safety story of the whole feature: it is what lets somebody agree to a page of
 * matches without it being a leap of faith, because every field that would be written is named with
 * what is already there beside it. Two surfaces draw it (the pile under Organize and the chooser
 * on one file's menu) so a claim it states wrongly is stated wrongly twice.
 *
 * What is held here is the part that decides an outcome: which side of a disagreement survives a
 * press, that keeping neither is not reachable, and that a field which cannot hold two values never
 * offers to keep both. The drawing is checked only where it says something a person acts on.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import type { Answer, FieldChange, Match } from '$lib/entity/tagger.svelte';

const mocks = vi.hoisted(() => ({
	labels: [] as { subject: string; key: string; label: string }[]
}));

/* The registry, stood in for by a real one whose single request is replaced. `one()` reads through
   `of()`, so replacing the one method covers the label lookup this row makes. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(subject: string): never[] {
			return mocks.labels.filter((one) => one.subject === subject) as never[];
		}
	}
	return { ...real, fields: new Standing() };
});

import MatchRow from './MatchRow.svelte';

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
let toggled = 0;
let answered: { key: string; answer: Answer }[] = [];

beforeEach(() => {
	vi.clearAllMocks();
	mocks.labels = [];
	toggled = 0;
	answered = [];
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

function draw(props: Record<string, unknown>): void {
	drawn = mount(MatchRow, {
		target: host,
		props: {
			taken: true,
			ontoggle: () => (toggled += 1),
			answers: {},
			onanswer: (key: string, answer: Answer) => answered.push({ key, answer }),
			...props
		} as never
	}) as Record<string, unknown>;
	flushSync();
}

/** A control by the start of its accessible name, which is where these carry their meaning. */
function named(startsWith: string): HTMLButtonElement {
	const found = [...host.querySelectorAll('button')].find((one) =>
		(one.getAttribute('aria-label') ?? '').startsWith(startsWith)
	);
	if (!found) throw new Error(`no control labelled "${startsWith}"`);
	return found;
}

function maybe(startsWith: string): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((one) =>
		(one.getAttribute('aria-label') ?? '').startsWith(startsWith)
	);
}

const conflict = (over: Partial<FieldChange> = {}): FieldChange => ({
	key: 'released_on',
	outcome: 'conflict',
	mine: '1990',
	theirs: '1991',
	needs: [],
	stands: true,
	...over
});

it('names the field by its label in the registry rather than by its key', () => {
	// The key is a column name. A row stating consequences in column names is a row nobody reads.
	mocks.labels = [{ subject: 'asset', key: 'title', label: 'Title' }];

	draw({ match: match() });

	expect(host.textContent).toContain('Title');
});

it('falls back to the key when the registry has no label for it', () => {
	// An older client meeting a newer server. A blank field name is worse than an ugly one.
	draw({ match: match() });

	expect(host.textContent).toContain('title');
});

it('says the row would change nothing when it carries no changes', () => {
	// An empty list of changes and a row that failed to load look identical without this.
	draw({ match: match({ changes: [] }) });

	expect(host.textContent).toContain("This would change nothing you don't already have");
});

it('offers to leave a row out while it is in, and to include it while it is out', () => {
	draw({ match: match() });
	expect(maybe('Leave out A Clip')).toBeTruthy();

	named('Leave out A Clip').click();
	flushSync();

	expect(toggled).toBe(1);
});

it('labels the tick to include a row that is already out', () => {
	draw({ match: match(), taken: false });

	expect(maybe('Include A Clip')).toBeTruthy();
});

it('says how sure Sift is, in words that separate an exact hash from a lookalike', () => {
	// An exact fingerprint means somebody uploaded this same file. A perceptual match means it
	// looks the same, which is a different claim and must not read as the first one.
	draw({ match: match({ grade: 'certain' }) });
	expect(host.textContent).toContain('Exact fingerprint');

	unmount(drawn as Record<string, unknown>);
	drawn = null;
	host.remove();
	host = document.createElement('div');
	document.body.append(host);

	draw({ match: match({ grade: 'likely' }) });
	expect(host.textContent).toContain('Looks the same, length agrees');
});

it('separates a lookalike whose length was never checked from one that agrees', () => {
	draw({ match: match({ grade: 'unsure' }) });

	expect(host.textContent).toContain('Looks the same, length unchecked');
});

it('draws an absent value as a word rather than as an empty space', () => {
	// `nothing` is a statement. A blank is indistinguishable from a value that failed to arrive.
	draw({
		match: match({
			changes: [
				{ key: 'title', outcome: 'write', mine: null, theirs: null, needs: [], stands: true }
			]
		})
	});

	expect(host.textContent).toContain('nothing');
});

it('draws a list of values on one line, and an empty list as nothing', () => {
	draw({
		match: match({
			changes: [
				{
					key: 'tags',
					outcome: 'write',
					mine: null,
					theirs: ['one', 'two'],
					needs: [],
					stands: true
				},
				{ key: 'people', outcome: 'write', mine: null, theirs: [], needs: [], stands: true }
			]
		})
	});

	expect(host.textContent).toContain('one, two');
	expect(host.textContent).toContain('nothing');
});

it('says a username as its handle on its Site, never as a printed object', () => {
	draw({
		match: match({
			changes: [
				{
					key: 'accounts',
					outcome: 'write',
					mine: [],
					theirs: [
						{ site: 'OnlyFans', handle: 'quillmoss', url: 'https://example.com/quillmoss' },
						{ site: '', handle: 'hollowgrain', url: 'https://example.com/hollowgrain' }
					],
					needs: [],
					stands: true
				}
			]
		})
	});

	expect(host.textContent).toContain('quillmoss on OnlyFans, hollowgrain');
	expect(host.textContent).not.toContain('[object Object]');
});

it('leaves a disagreement alone, and says so, where nothing can answer it', () => {
	// The pile passes no `onanswer` on a surface that cannot settle one. Drawing the presses there
	// would offer a decision that goes nowhere.
	draw({ match: match({ changes: [conflict()] }), onanswer: undefined });

	expect(host.textContent).toContain('left alone');
	expect(maybe('Use 1991')).toBeUndefined();
});

it('offers both values as presses where a disagreement can be answered', () => {
	draw({ match: match({ changes: [conflict()] }) });

	expect(maybe('Keep 1990')).toBeTruthy();
	expect(maybe('Use 1991')).toBeTruthy();
});

it('starts on what is already here, because keeping your own is the default', () => {
	draw({ match: match({ changes: [conflict()] }) });

	expect(named('Keep 1990').getAttribute('aria-pressed')).toBe('true');
	expect(named('Use 1991').getAttribute('aria-pressed')).toBe('false');
	expect(host.textContent).toContain('keeping what is here');
});

it('swaps outright, because a conflict is always a field that holds ONE value', () => {
	// There is no "keep both" and there never could be: a field holding many is merged without
	// anybody being asked, so it is never among the conflicts. Offering it here would promise a
	// write the server refuses, and would put "1990, 1991" where one date goes.
	draw({ match: match({ changes: [conflict()] }) });

	named('Use 1991').click();
	flushSync();

	expect(answered).toEqual([{ key: 'released_on', answer: 'theirs' }]);
});

it('says nothing when the side already kept is pressed', () => {
	// Keeping neither is not an answer, and re-choosing what is already chosen is not a change:
	// keeping your own is the default and sends nothing at all.
	draw({
		match: match({ changes: [conflict()] }),
		answers: { released_on: 'mine' as Answer }
	});

	named('Keep 1990').click();
	flushSync();

	expect(answered).toEqual([]);
});

it('says in words what the presses would do, because two struck-through values are not a sentence', () => {
	draw({
		match: match({ changes: [conflict()] }),
		answers: { released_on: 'theirs' as Answer }
	});
	expect(host.textContent).toContain('taking theirs');
});

it('answers each field on its own, so one disagreement does not settle another', () => {
	draw({
		match: match({
			changes: [
				conflict({ key: 'released_on', mine: '1990', theirs: '1991' }),
				conflict({ key: 'title', mine: 'Mine', theirs: 'Theirs' })
			]
		}),
		answers: { released_on: 'theirs' as Answer }
	});

	expect(named('Use 1991').getAttribute('aria-pressed')).toBe('true');
	expect(named('Use Theirs').getAttribute('aria-pressed')).toBe('false');
});

it('addresses the still with the token the row carries, so it may be kept', () => {
	/*
	 * Without it every picture on this pile is asked about again on every visit: a bare address
	 * names no particular picture, so the server refuses it the week-long promise and the browser
	 * has to check. The token comes down with the row (one read for the page, in the route) and
	 * all this file has to do is put it on the end. `MatchView.art`.
	 */
	draw({ match: match({ art: 'abc123' }) });

	expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/a-1/thumb?v=abc123');
});

it('leaves it bare where nothing is known about the pictures yet', () => {
	// Which is a slower row and never a wrong one: a token invented before there is a picture to
	// name would pin an empty frame in somebody's browser for a week.
	draw({ match: match({ art: null }) });

	expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/a-1/thumb');
});
