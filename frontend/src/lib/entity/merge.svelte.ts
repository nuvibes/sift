/* Two of something that turn out to be one.
 *
 * Two calls, and the first one is why this is a module rather than a button. Weighing counts what
 * would move and writes nothing; merging does it. They are separate because a merge cannot be taken
 * back, so the numbers have to be on the screen before the press rather than reported after it.
 *
 * One pair of functions for people and for sites, because the difference between them is an address
 * and the name of one field. The SERVER holds two implementations and should (a site's tables are
 * not a person's, and one of them can hold saved cookies), but a client that asked the question twice
 * would be two places to keep the two-call rule, and that rule is the whole guard.
 */

import { api, ApiError, type ApiPath } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import type { PickChoice } from '$lib/components/common/verbs';

/** What a merge would move, counted before anything moves. */
export type Weighed = components['schemas']['MergeWeighed'];

/** What a merge of songs moves: the files, by the songs going and the one kept. */
type SongsWeighed = components['schemas']['SongsMerged'];

/**
 * One of the things a merge is about, as the sheet draws it.
 *
 * A row rather than a name, and both extras are there because the sheet asks a question about
 * IDENTITY: the picture is how a wall of people is read in the first place, and the count is what
 * decides the default: keeping whoever holds the most files is what moves the least. Both are
 * optional because a caller holding nothing but a name may still open the sheet, and the rows then
 * fall back to a letter and to the order they came in.
 */
export interface MergeSubject extends PickChoice {
	/** How many files this one holds, for the default answer. Absent counts as none. */
	files?: number;
}

/**
 * What is being folded. The word decides the address and what the list of ids is called.
 *
 * Written out rather than derived from a route, because these are the only three things in Sift
 * that can turn out to be each other: a tag or a collection is somebody's own word for a group, and
 * two of those being "the same" is not a discovery about the world. A song is a piece of music,
 * and a name typed on one file and the name AcoustID gave another can be the same recording.
 */
export type MergeableKind = 'person' | 'site' | 'song';

interface Address {
	/** Where the count goes, and where the write goes. Two addresses, deliberately. */
	weigh: ApiPath;
	merge: ApiPath;
	/** What the server calls the list of ids in the body. */
	field: 'people' | 'sites' | 'songs';
}

/**
 * Every address written out, rather than built from a stem and a suffix.
 *
 * `ApiPath` is the union of the routes the server actually declares, generated from its own schema,
 * so a literal here is CHECKED and a template string is not. Writing `${stem}/merge` would
 * typecheck against nothing at all, which is how a client comes to call a route that was renamed or
 * removed.
 *
 * There is no address for the CANDIDATES here. A page of `/people` filtered inside the sheet is a
 * chooser that cannot reach anybody past the page: typing their name finds nothing, with nothing on
 * screen to say why. The sheet asks the stores instead (`people.choices`, `sites.choices`), the
 * same filter-on-the-server question every other picker in the app asks, so anybody in the library
 * is reachable by typing their name.
 */
const ADDRESS: Record<MergeableKind, Address> = {
	person: {
		weigh: '/people/weigh-merge',
		merge: '/people/merge',
		field: 'people'
	},
	site: {
		weigh: '/sites/weigh-merge',
		merge: '/sites/merge',
		field: 'sites'
	},
	song: {
		weigh: '/songs/weigh-merge',
		merge: '/songs/merge',
		field: 'songs'
	}
};

/**
 * A song's answer, in the shape the sheet reads.
 *
 * A song moves files and nothing else: no names to keep, no usernames, no faces, no fields to
 * fill. So the sheet's lines for those are all nought and empty, which it already draws as
 * nothing, and the files line says what a song merge does. The songs going are named together,
 * as the sheet names the others when there is more than one.
 */
function asWeighed(kind: MergeableKind, answer: Weighed | SongsWeighed): Weighed {
	if (kind !== 'song') return answer as Weighed;
	const song = answer as SongsWeighed;
	return {
		into_name: song.into_name,
		from_name: song.from_names.join(', '),
		files: song.files,
		aliases: 0,
		aliases_named: [],
		children: 0,
		children_named: [],
		faces: 0,
		faces_from: [],
		facts: 0,
		filled: [],
		links: 0,
		links_named: [],
		usernames: 0,
		usernames_named: []
	};
}

function said(error: unknown): string {
	return error instanceof ApiError && error.detail ? error.detail : "That didn't work.";
}

/**
 * What folding SEVERAL into one would move, added up. Nothing is written.
 *
 * A POST for a question that writes nothing, which is right here for one reason: the ids are a
 * list, and a list of them in a query string is a request something in the middle truncates.
 */
export async function weighSeveral(
	kind: MergeableKind,
	ids: string[],
	into: string
): Promise<Weighed> {
	const { weigh, field } = ADDRESS[kind];
	return asWeighed(kind, await api.post<Weighed>(weigh, { body: { [field]: ids, into } }));
}

/**
 * Fold several into one, in a single act.
 *
 * ONE request rather than one per pair, and that is the whole reason this exists: a merge cannot
 * be taken back, so four of them done as four calls is four chances to stop halfway. And halfway
 * through folding four into one is a library where two are gone, two are still there, and nothing
 * says which. The server does the set in one transaction.
 *
 * Sending the survivor among `ids` is fine; it is dropped rather than refused, because the obvious
 * thing to send is the selection somebody made.
 */
export async function mergeSeveral(
	kind: MergeableKind,
	ids: string[],
	into: string
): Promise<Weighed> {
	const { merge, field } = ADDRESS[kind];
	return asWeighed(kind, await api.post<Weighed>(merge, { body: { [field]: ids, into } }));
}

/** The sentence to show when one of these did not work. */
export function problemFrom(error: unknown): string {
	return said(error);
}
