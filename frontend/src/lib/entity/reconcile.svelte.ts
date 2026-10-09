/* Where a stash-box disagrees with what this library already says. */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** One field two answers differ about, with both of them on it. */
export type Disagreement = components['schemas']['DisagreementView'];

/** What settling one actually did. */
export type Settled = components['schemas']['Settled'];

function said(error: unknown): string {
	return error instanceof ApiError && error.detail ? error.detail : "That didn't work.";
}

/** Every field a linked stash-box disagrees with, in the whole library. */
export async function disagreements(signal?: AbortSignal): Promise<Disagreement[]> {
	const answer = await api.get<components['schemas']['DisagreementList']>(
		'/stash-boxes/disagreements',
		{ signal }
	);
	return answer.disagreements;
}

/** The same question about ONE record, which is what a record's own page asks. */
export async function disagreementsOf(subject: string, localId: string): Promise<Disagreement[]> {
	const answer = await api.get<components['schemas']['DisagreementList']>(
		`/stash-boxes/disagreements/${encodeURIComponent(subject)}/${encodeURIComponent(localId)}`
	);
	return answer.disagreements;
}

/** How many fields are waiting, as a sentence. The one place those words are written. */
export function waitingText(waiting: number, boxes: readonly string[] = []): string {
	// The boxes by name, as the rows and the tab's mark carry them; "a stash-box" only where
	// nothing names one.
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

/** Take one of the two answers for one field. `takeTheirs` false means keeping what is already
 * there, which is a real answer and the commonest one, and it writes nothing. */
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
