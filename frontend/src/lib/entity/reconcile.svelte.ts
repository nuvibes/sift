/* Where a stash-box disagrees with what this library already says.
 *
 * A list of FIELDS rather than of subjects, and that is the whole screen. "StashDB has something to
 * say about Jane" is not a question anybody can answer; "StashDB says 1991 and you have 1990" is.
 *
 * Nothing is stored on the server: a disagreement is the state of two values, and either can change
 * under it. So the list is worked out on every read and is true when it is drawn, rather than being
 * a table describing a disagreement that ended a week ago.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** One field two answers differ about, with both of them on it. */
export type Disagreement = components['schemas']['DisagreementView'];

/** What settling one actually did. */
export type Settled = components['schemas']['Settled'];

function said(error: unknown): string {
	return error instanceof ApiError && error.detail ? error.detail : "That didn't work.";
}

/**
 * Every field a linked stash-box disagrees with, in the whole library.
 *
 * `signal` is how a caller puts a ceiling on the wait.
 *
 * This does NOT ask remote boxes, and the cost is not a fact about somebody else's server. The
 * answer is worked out from what was KEPT at the moment somebody agreed to a link, so no network is
 * touched and the whole cost is local: one enrichment plan per linked record, with one visibility
 * read for the lot.
 *
 * The Stash-boxes pane wants exactly this. A RECORD's page does not, and does not ask it: see
 * `disagreementsOf`.
 */
export async function disagreements(signal?: AbortSignal): Promise<Disagreement[]> {
	const answer = await api.get<components['schemas']['DisagreementList']>(
		'/stash-boxes/disagreements',
		{ signal }
	);
	return answer.disagreements;
}

/**
 * The same question about ONE record, which is what a record's own page asks.
 *
 * Reading the whole library's list and throwing away every row but this one's would be one question
 * asked one way with the wrong READ: a page view working out every linked record in the library to
 * draw at most three rows. The server answers this one directly.
 *
 * It is still one question asked one way: the server builds both lists out of the same rule, so a
 * field that is waiting here is waiting there. No `signal`: there is nothing to give up on. This is
 * three seeking reads about one subject rather than a survey of everything anybody has ever linked.
 */
export async function disagreementsOf(subject: string, localId: string): Promise<Disagreement[]> {
	const answer = await api.get<components['schemas']['DisagreementList']>(
		`/stash-boxes/disagreements/${encodeURIComponent(subject)}/${encodeURIComponent(localId)}`
	);
	return answer.disagreements;
}

/**
 * How many fields are waiting, as a sentence. The one place those words are written.
 *
 * Two surfaces say them: the line above the panel on a record's History tab, and the tooltip on the
 * mark beside that tab's word. They are the same fact, and two copies of one sentence is how a
 * number and the words around it come to disagree about what they are counting.
 */
export function waitingText(waiting: number, boxes: readonly string[] = []): string {
	// The boxes by name, as the rows and the tab's mark carry them; "a stash-box" only where
	// nothing names one. One box agrees with "disagrees", two or more with "disagree".
	const who = boxes.length > 0 ? andList(boxes) : 'a stash-box';
	const verb = boxes.length > 1 ? 'disagree' : 'disagrees';
	return waiting === 1 ? `One field ${who} ${verb} with` : `${waiting} fields ${who} ${verb} with`;
}

/** The distinct box names on these rows, in the order they first appear. */
export function boxesOf(rows: readonly Pick<Disagreement, 'box_name'>[]): string[] {
	return [...new Set(rows.map((row) => row.box_name).filter((name): name is string => !!name))];
}

/** "A", "A and B", "A, B and C". */
function andList(names: readonly string[]): string {
	if (names.length <= 1) return names.join('');
	return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/**
 * Take one of the two answers for one field.
 *
 * `takeTheirs` false means keeping what is already there, which is a real answer and the commonest
 * one, and it writes nothing. A field re-written with the value it already holds is a change in
 * every log that watches for one, for a decision that changed nothing.
 */
export async function settle(one: Disagreement, takeTheirs: boolean): Promise<Settled> {
	return await api.post<Settled>('/stash-boxes/disagreements/settle', {
		body: {
			subject: one.subject,
			local_id: one.local_id,
			// The box is part of what names a row: two boxes disagreeing about one field are two
			// rows, and a press that did not say which one would settle whichever the server met first.
			box_id: one.box_id,
			key: one.key,
			take_theirs: takeTheirs
		}
	});
}

/** Where the subject of one row lives, so a name can be opened. */
export function pageOf(one: Disagreement): string {
	// A file's own page: a file's disagreements are rows too.
	if (one.subject === 'asset') return `/asset/${one.local_id}`;
	if (one.subject === 'person') return `/people/${one.local_id}`;
	if (one.subject === 'site') return `/sites/${one.local_id}`;
	return `/tags/${one.local_id}`;
}

/** The sentence to show when one of these did not work. */
export function problemFrom(error: unknown): string {
	return said(error);
}
