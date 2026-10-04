/* Files a stash-box recognised, and the one press that settles a page of them.
 *
 * ## Why the rows carry what they WOULD do
 *
 * A bulk confirm nobody can see the consequences of is a leap of faith with a progress bar on it.
 * So every row arrives with the fields it would change (what is there beside what is offered)
 * and with the names it would have to invent. The button counts those before it is pressed rather
 * than reporting them afterwards.
 *
 * ## Creating is per press, and per NAME
 *
 * `create` is sent with the press and is never remembered. A stored "always make the people" is
 * the automatic creation this feature deliberately refuses; a tick beside each name is a decision
 * somebody takes while looking at what it would do.
 *
 * NAMES rather than a yes-or-no. One tick over thirty-one entries offers two answers (all of
 * them, or none and lose the four that were wanted), so reading the list carefully would be
 * punished. An empty list means invent nothing.
 */

import { api, ApiError } from '$lib/api/client';
import type { FoundRecord } from '$lib/settings-ui/stash-boxes.svelte';
import type { components } from '$lib/api/schema';
import { asked, type PageAsk } from '$lib/grid/anchor';

/** One row an answer would have to invent, and what kind of row it is. */
export type Missing = components['schemas']['MissingRef'];

/** How sure Sift is, which is never how sure the stash-box is. */
export type Grade = 'certain' | 'likely' | 'unsure';

/** What somebody chose about one disagreeing field. `mine` is the default and sends nothing. */
export type Answer = 'mine' | 'theirs' | 'both';

/** One field a match would change, before anything changes. */
export type FieldChange = components['schemas']['FieldChange'];

/** One disagreement, answered, addressed to the match it was shown on. */
export type Settled = components['schemas']['SettleField'];

/** One stash-box's answer about one file. */
export type Match = components['schemas']['MatchView'];

type MatchPage = components['schemas']['MatchList'];

/** What a confirmation actually did. Every number is a write that landed. */
export type Applied = components['schemas']['Applied'];

/** How many answers one page carries. The same number the server will settle in one press. */
export const PAGE = 24;

function said(error: unknown): string {
	return error instanceof ApiError && error.detail ? error.detail : "That didn't work.";
}

/** One page of the pile, surest first. */
/**
 * What is waiting for ONE file. The chooser on the grid's own menu reads this.
 *
 * The same route and the same order as the pile, filtered by the file. Not a second function that
 * fetches everything and filters here: the pile is paged, so a file whose match sits on page four
 * would come back empty from a client-side filter and read as "nothing found".
 */
export async function waitingFor(assetId: string, limit = PAGE): Promise<MatchPage> {
	return await api.get<MatchPage>('/stash-boxes/matches', { query: { limit, asset: assetId } });
}

/**
 * What was already answered about ONE file, newest first: the settled half of `waitingFor`. The
 * chooser reads it so a file a box matched and somebody applied says so.
 */
export async function answeredFor(assetId: string, limit = PAGE): Promise<MatchPage> {
	return await api.get<MatchPage>('/stash-boxes/matches', {
		query: { limit, asset: assetId, state: 'answered' }
	});
}

/** Which state of the pile: what still waits, or what was already answered. */
export type MatchState = 'waiting' | 'answered';

/**
 * One page of the pile in one state, asked for by where it starts or by the row it starts AT:
 * the pile's paging (`CardPaging.query`) says which. A row is named `<file>:<box>`, `matchKey`.
 */
export async function waiting(query: PageAsk, state: MatchState = 'waiting'): Promise<MatchPage> {
	return await api.get<MatchPage>('/stash-boxes/matches', {
		query: { ...asked(query), state }
	});
}

/** Say yes to these, to exactly these rows being invented, and to these disagreements answered. */
export async function apply(
	matches: Match[],
	create: Missing[],
	settle: Settled[] = []
): Promise<Applied> {
	return await api.post<Applied>('/stash-boxes/matches/apply', {
		body: { matches: matches.map(one), create, settle }
	});
}

/**
 * The disagreements somebody answered, addressed to their matches, as the press sends them.
 *
 * Only the matches still in the press, and only the answers that are not "keep mine": keeping
 * your own is the default and writing it would be a change in every log that watches for one, for
 * a decision that changed nothing.
 */
export function toSettle(
	matches: Match[],
	answers: Record<string, Record<string, Answer>>
): Settled[] {
	const out: Settled[] = [];
	for (const match of matches) {
		const mine = answers[matchKey(match)] ?? {};
		for (const [key, take] of Object.entries(mine)) {
			if (take === 'mine') continue;
			out.push({ asset_id: match.asset_id, box_id: match.box_id, key, take });
		}
	}
	return out;
}

/** What names one match: theirs and ours together, the same pair the server settles by. */
export function matchKey(match: Match): string {
	return `${match.asset_id}:${match.box_id}`;
}

/** Say no to these. Nothing is written and they stop being asked about. */
export async function refuse(matches: Match[]): Promise<Applied> {
	return await api.post<Applied>('/stash-boxes/matches/refuse', {
		body: { matches: matches.map(one), create: [] }
	});
}

/** Which answer, as the server names one: theirs and ours together. */
function one(match: Match): { asset_id: string; box_id: string } {
	return { asset_id: match.asset_id, box_id: match.box_id };
}

/** The sentence to show when one of these did not work. */
export function problemFrom(error: unknown): string {
	return said(error);
}

/** What names one row rather than one word. A person and a tag can be spelled the same. */
export function rowKey(one: Missing): string {
	return `${one.kind}:${one.name}`;
}

/**
 * Every row a set of matches would have to invent, counted once.
 *
 * Deduplicated across the whole page rather than summed per row, because the same person turns up
 * on twenty files from one release, and "this would create 20 people" beside a list of one name
 * is a number that makes somebody refuse a press they should have made.
 *
 * By KIND and name. Folding "the person Jane" into "the tag Jane" would offer one tick for two
 * different rows, and whichever the writer reached first would be the one that got made.
 */
export function wouldCreate(matches: Match[]): Missing[] {
	const seen = new Map<string, Missing>();
	for (const match of matches) {
		for (const one of match.creates) if (!seen.has(rowKey(one))) seen.set(rowKey(one), one);
	}
	return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * How many fields a set of matches would actually write, given the new rows ticked for making.
 * Conflicts are not among them.
 *
 * A field that names only rows nobody has made yet (four new tags) writes nothing unless one of
 * them is ticked: the press drops a name it may not make. So the count is of what the press will
 * land with these ticks, which is the number its receipt says afterwards.
 */
export function wouldWrite(matches: Match[], making: readonly Missing[]): number {
	const ticked = new Set(making.map(rowKey));
	return matches.reduce(
		(total, match) =>
			total +
			match.changes.filter(
				(one) =>
					one.outcome === 'write' &&
					(one.stands !== false || (one.needs ?? []).some((need) => ticked.has(rowKey(need))))
			).length,
		0
	);
}
