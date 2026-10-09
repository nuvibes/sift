import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';

/* Sharing, from the client's side. Nothing here decides anything. */

/** Everything a grant can be attached to: the server's list, taken from the server. */
export type ShareableType = components['schemas']['ObjectType'];

/** Share, or restrict. Likewise the server's, and likewise closed. */
export type Effect = components['schemas']['Effect'];

export type Grant = components['schemas']['GrantResponse'];

export type ShareableUser = components['schemas']['ShareableUserResponse'];

/** What the panel is open on: one thing, named the way the server names it. */
export interface ShareTarget {
	type: ShareableType;
	id: string | null;
	/** What to call it on screen. The panel says what it is acting on; ids are not names. */
	label: string;
}

/** The three words, and the fourth answer that only exists when the panel holds several things. */
export type Standing = 'shared' | 'restricted' | 'private';
export type Reading = Standing | 'mixed';

/** What each kind of thing is called, so a panel open on several can name them. */
/* Exported so the hidden panel can say "this file" and "this tag" in its own sentences, the same
   words the sharing panel uses. */
export const NOUNS: Record<ShareableType, readonly [string, string]> = {
	global: ['everything', 'everything'],
	root: ['folder', 'folders'],
	folder: ['folder', 'folders'],
	item: ['file', 'files'],
	tag: ['tag', 'tags'],
	person: ['person', 'people'],
	collection: ['collection', 'collections'],
	site: ['Site', 'Sites'],
	photo_set: ['Photo Set', 'Photo Sets'],
	song: ['song', 'songs']
};

/** What the panel says it is acting on. One thing is called by its name, because that is the only
 * way to be sure it is the thing you meant. */
export function subjectOf(targets: ShareTarget[]): string {
	if (targets.length === 1) return targets[0].label;
	const [one, many] = NOUNS[targets[0]?.type ?? 'item'];
	return `${counted(targets.length)} ${targets.length === 1 ? one : many}`;
}

export async function fetchUsers(): Promise<ShareableUser[]> {
	return api.get<ShareableUser[]>('/sharing/users');
}

/** One grant that reaches a thing, and what it was actually made on. */
export type GrantSource = components['schemas']['GrantSourceResponse'];

/** What each kind of source is called in a sentence, so a line reads as English. */
const SOURCE_NOUNS: Record<ShareableType, string> = {
	global: 'everything in Sift',
	root: 'the library',
	folder: 'the folder',
	item: 'file level',
	tag: 'the tag',
	person: 'the person',
	collection: 'the collection',
	site: 'the Site',
	photo_set: 'the Photo Set',
	song: 'the song'
};

/** The rest of the line after the word Shared or Restricted, in two pieces. */
interface SourceWords {
	lead: string;
	name: string | null;
	/** What follows the name, where a sentence carries on past it. Only `reasonWords` fills it. */
	tail?: string;
}

export function sourceLabel(source: GrantSource, subject: ShareableType = 'item'): SourceWords {
	if (source.here) return { lead: `on this ${NOUNS[subject][0]} itself`, name: null };
	return { lead: `by ${SOURCE_NOUNS[source.source_type]}`, name: source.source_name };
}

/** Why a grant is not the answer, for the ones that lost. */
/** How narrow a decision is, smallest first. */
const BREADTH: Record<ShareableType, number> = {
	item: 0,
	folder: 1,
	root: 2,
	tag: 3,
	person: 3,
	collection: 3,
	site: 3,
	/* A logical membership like the four above it, and at the same breadth: a set reaches its files
	   through one join, exactly as a collection does, so neither is narrower than the other. */
	photo_set: 3,
	/* And a song: one join from its files (`song_files`), the same reach as a Photo Set. */
	song: 3,
	global: 4
};

/** Where a named thing lives, so a line about it can be a way to it. */
export function sourceHref(type: ShareableType, id: string | null): string | null {
	if (id === null) return null;
	if (type === 'person') return `/people/${id}`;

	if (type === 'collection') return `/collections/${id}`;
	if (type === 'site') return `/sites/${id}`;
	if (type === 'photo_set') return `/photo-sets/${id}`;
	if (type === 'song') return `/songs/${id}`;
	if (type === 'tag') return `/browse?tag=${id}`;
	return null;
}

export function breadthOf(source: GrantSource): number {
	return BREADTH[source.source_type];
}

/** How much MORE than the named thing a decision about a site covers, or nothing at all. */
export function includesSitesWithin(count: number): string | null {
	if (count < 1) return null;
	return `Includes the ${counted(count)} ${count === 1 ? 'Site' : 'Sites'} within it`;
}

/* A restrict is absolute: it beats every share, made nearer or broader, so a share that is not
 * in force lost to a restrict. */
export function beatenBy(source: GrantSource): string {
	return source.effect === 'share'
		? 'Not in force: a restrict beats any share'
		: "Not in force: they reach it through files this doesn't cover";
}

/** One thing YOU have hidden that is concealing this, and where you did it. */
export type VaultSource = components['schemas']['VaultSourceResponse'];

/** The rest of the line after the word Hidden, split the same way the sharing lines are. */
export function hiddenLabel(source: VaultSource, subject: ShareableType = 'item'): SourceWords {
	if (source.here) return { lead: `on this ${NOUNS[subject][0]} itself`, name: null };
	return { lead: `by ${SOURCE_NOUNS[source.source_type]}`, name: source.source_name };
}

/** What YOU have hidden that is concealing this, if anything. */
/** Bring one of the things named above back onto your own screens. */
export function canUnhide(source: VaultSource): boolean {
	return source.source_id != null;
}

export async function unhide(source: VaultSource): Promise<void> {
	const id = source.source_id;
	if (id == null) throw new Error("there's no such thing to unhide");
	const body = { vault: false };
	switch (source.source_type) {
		case 'item':
			return api.put<void>(`/assets/${id}/vault`, { body });
		case 'folder':
			return api.put<void>(`/folders/${id}/vault`, { body });
		case 'root':
			return api.patch<void>(`/library/roots/${id}`, { body });
		case 'collection':
			return api.put<void>(`/collections/${id}/vault`, { body });
		case 'tag':
			return api.put<void>(`/tags/${id}/vault`, { body });
		case 'site':
			return api.put<void>(`/sites/${id}/vault`, { body });
		case 'photo_set':
			return api.put<void>(`/photo-sets/${id}/vault`, { body });
		case 'song':
			return api.put<void>(`/songs/${id}/vault`, { body });
		case 'person':
			return api.put<void>(`/people/${id}`, {
				body: { name: source.source_name ?? '', vault: false }
			});
		default:
			throw new Error("there's no such thing to unhide");
	}
}

export async function fetchVaultSources(target: ShareTarget): Promise<VaultSource[]> {
	return api.get<VaultSource[]>('/sharing/hidden-by', {
		query: { object_type: target.type, object_id: target.id ?? undefined }
	});
}

export async function fetchSources(target: ShareTarget): Promise<GrantSource[]> {
	return api.get<GrantSource[]>('/sharing/sources', {
		query: { object_type: target.type, object_id: target.id ?? undefined }
	});
}

/** Who, other than you, can see one thing, and through what. */
export type ReachReport = components['schemas']['ReachReport'];
export type ReachUser = components['schemas']['ReachUser'];
export type ReachThrough = components['schemas']['ReachThrough'];

/** The line under a name, in the two pieces the sharing panel splits its own lines into. */
export function reachLabel(line: ReachThrough, subject: ShareableType = 'item'): SourceWords {
	if (line.how === 'shared') return { lead: `on this ${NOUNS[subject][0]} itself`, name: null };
	const noun = SOURCE_NOUNS[line.kind];
	if (line.name === null) return { lead: `through ${noun}`, name: null };
	return { lead: `through ${noun}`, name: line.name };
}

/** Why a line is not the one that decided, said the way the sharing panel says it. */
export function reachBeatenBy(line: ReachThrough): string {
	return line.how === 'restricted'
		? "Not in force: they reach it through files this doesn't cover"
		: 'Not in force: a restrict beats any share';
}

/** Narrowest first, so the lines under a name read downwards as the resolver reads them. */
export function reachBreadth(line: ReachThrough): number {
	return BREADTH[line.kind];
}

export async function fetchReach(target: ShareTarget): Promise<ReachReport> {
	return api.get<ReachReport>('/sharing/reach', {
		query: { object_type: target.type, object_id: target.id ?? undefined }
	});
}

/** WHY one user can see an entity, when nothing was ever said about the entity. */
export type ReachThroughFiles = components['schemas']['ReachThroughReport'];
type ReachReason = components['schemas']['ReachReasonResponse'];

export async function fetchReachThrough(
	target: ShareTarget,
	user: string
): Promise<ReachThroughFiles> {
	return api.get<ReachThroughFiles>('/sharing/reach/through', {
		query: { object_type: target.type, object_id: target.id ?? undefined, user }
	});
}

/** One reason as it reads: "through the folder Holiday, which holds 3 of them". */
export function reasonWords(reason: ReachReason, files: number): SourceWords {
	const noun = SOURCE_NOUNS[reason.kind];
	const held = files > 1 && reason.files < files ? `, which holds ${reason.files} of ${files}` : '';
	if (reason.name === null) return { lead: `through ${noun}${held}`, name: null };
	return { lead: `through ${noun}`, name: reason.name, tail: held };
}

export async function fetchGrants(target: ShareTarget): Promise<Grant[]> {
	return api.get<Grant[]>('/sharing', {
		query: { object_type: target.type, object_id: target.id ?? undefined }
	});
}

/** Make a grant. Returns the whole panel afterwards, not the row just written. */
export async function grant(
	target: ShareTarget,
	subjectUserId: string,
	effect: Effect
): Promise<Grant[]> {
	return api.put<Grant[]>('/sharing', {
		body: {
			object_type: target.type,
			object_id: target.id,
			subject_user_id: subjectUserId,
			effect
		}
	});
}

export async function revoke(
	target: ShareTarget,
	subjectUserId: string,
	effect: Effect
): Promise<Grant[]> {
	return api.post<Grant[]>('/sharing/revoke', {
		body: {
			object_type: target.type,
			object_id: target.id,
			subject_user_id: subjectUserId,
			effect
		}
	});
}

/** What one user currently holds on this thing, as one of the three words the interface uses. */
export function standingOf(grants: Grant[], subjectUserId: string): Standing {
	if (holds(grants, subjectUserId, 'restrict')) return 'restricted';
	if (holds(grants, subjectUserId, 'share')) return 'shared';
	return 'private';
}

/** One answer about one user across everything the panel is open on. */
export function readingOf(perTarget: Grant[][], subjectUserId: string): Reading {
	const words = perTarget.map((grants) => standingOf(grants, subjectUserId));
	const first = words[0] ?? 'private';
	return words.every((word) => word === first) ? first : 'mixed';
}

/** Make one user's standing on one thing what somebody asked for, and nothing else. */
export async function put(
	target: ShareTarget,
	subjectUserId: string,
	desired: Standing,
	current: Grant[]
): Promise<void> {
	if (desired !== 'private') {
		const effect: Effect = desired === 'shared' ? 'share' : 'restrict';
		await grant(target, subjectUserId, effect);
		return;
	}
	for (const effect of ['share', 'restrict'] as const) {
		if (holds(current, subjectUserId, effect)) await revoke(target, subjectUserId, effect);
	}
}

/** Whether one specific row exists, which is a different question from the one above. */
export function holds(grants: Grant[], subjectUserId: string, effect: Effect): boolean {
	return grants.some((entry) => entry.subject_user_id === subjectUserId && entry.effect === effect);
}
