/* Two of something that turn out to be one. Two calls, and the first one is why this is a module
 * rather than a button. */

import { api, ApiError, type ApiPath } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import type { PickChoice } from '$lib/components/common/verbs';

/** What a merge would move, counted before anything moves. */
export type Weighed = components['schemas']['MergeWeighed'];

/** What a merge of songs moves: the files, by the songs going and the one kept. */
type SongsWeighed = components['schemas']['SongsMerged'];

/** One of the things a merge is about, as the sheet draws it. */
export interface MergeSubject extends PickChoice {
	/** How many files this one holds, for the default answer. Absent counts as none. */
	files?: number;
}

/** What is being folded. The word decides the address and what the list of ids is called. */
export type MergeableKind = 'person' | 'site' | 'song';

interface Address {
	/** Where the count goes, and where the write goes. Two addresses, deliberately. */
	weigh: ApiPath;
	merge: ApiPath;
	/** What the server calls the list of ids in the body. */
	field: 'people' | 'sites' | 'songs';
}

/** Every address written out, rather than built from a stem and a suffix. */
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

/** A song's answer, in the shape the sheet reads. */
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

/** What folding SEVERAL into one would move, added up. */
export async function weighSeveral(
	kind: MergeableKind,
	ids: string[],
	into: string
): Promise<Weighed> {
	const { weigh, field } = ADDRESS[kind];
	return asWeighed(kind, await api.post<Weighed>(weigh, { body: { [field]: ids, into } }));
}

/** Fold several into one, in a single act. */
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
