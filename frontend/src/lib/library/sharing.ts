import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';

/* Sharing, from the client's side.
 *
 * Nothing here decides anything. The server resolves every permission on every request, and this
 * file writes grants and reads back what it wrote, so a screen that got the state wrong shows the
 * wrong buttons for a moment and nothing more. That is the whole safety story for this module, and
 * it is worth writing down because a file called sharing looks like a place somebody would add a
 * check.
 *
 * The two words are the product, so they are the vocabulary here too. **Share** hands something
 * over. **Restrict** is not the absence of a share. It is a promise: never this, whatever else
 * gets shared later. The absence of both is **Not shared**, which is what everything in a library
 * is until somebody says otherwise, and it is why an empty panel is an ordinary answer.
 */

/**
 * Everything a grant can be attached to: the server's list, taken from the server.
 *
 * Every noun below is looked up by it, so a copy written out here would decide what the panel can
 * put into words: a kind the server sends that the copy has never heard of would draw a sentence
 * with a hole in it. Written down twice, nothing says which of the two is behind.
 */
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

/**
 * What the panel says it is acting on.
 *
 * One thing is called by its name, because that is the only way to be sure it is the thing you
 * meant. Several are counted, because forty filenames is not a heading, and the count is the
 * fact that matters when the next press changes all of them together.
 */
export function subjectOf(targets: ShareTarget[]): string {
	if (targets.length === 1) return targets[0].label;
	const [one, many] = NOUNS[targets[0]?.type ?? 'item'];
	return `${counted(targets.length)} ${targets.length === 1 ? one : many}`;
}

export async function fetchUsers(): Promise<ShareableUser[]> {
	return api.get<ShareableUser[]>('/sharing/users');
}

/**
 * One grant that reaches a thing, and what it was actually made on.
 *
 * The answer to "why is this shared when I never shared it". A file inside three folders, carrying
 * four tags and sitting in two collections has eight places a decision could have come from, and
 * the panel has to say which.
 */
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

/**
 * The rest of the line after the word Shared or Restricted, in two pieces.
 *
 * "by the folder" + "Holiday". "by the person" + "Neve Arbour". The verb is kept out of it so the
 * panel can colour that, and the NAME is kept apart from the words around it for the same reason
 * one step further in: a name is the thing being pointed at, so it is drawn in the ink the panel
 * uses for the thing it is open on, and the sentence holding it stays quiet.
 *
 * `name` is absent where there is nothing to name: a grant made on the thing itself, or one made
 * over everything.
 */
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

/**
 * Why a grant is not the answer, for the ones that lost.
 *
 * Shown on the faded line rather than left to be worked out. "There is a share here and it is doing
 * nothing" is a confusing thing to look at; "a restrict beats it" is a fact somebody can act on.
 */
/**
 * How narrow a decision is, smallest first.
 *
 * The order the resolver reads them in, so a list sorted by it reads as the ladder: the file
 * itself, then the folder it is in, then the library, then the things it belongs to, then a grant
 * over everything. It is what makes "why is this shared" answerable by reading downwards.
 */
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

/**
 * Where a named thing lives, so a line about it can be a way to it.
 *
 * Null for the two that have nowhere to go. A folder and a library are shown in the tree inside
 * Settings, which is a panel over whatever screen you were on rather than an address. Choosing
 * one there is deliberately not a navigation, because navigating would close the panel the tree
 * is drawn in. Rather than invent a destination, those two stay as plain words.
 *
 * `item` is absent for a different reason: a grant on the thing itself is named "on this file
 * itself", so there is no name to click and nowhere to go but here.
 */
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

/**
 * How much MORE than the named thing a decision about a site covers, or nothing at all.
 *
 * A site can be part of another site: a network holds Sites within it, and those are what files
 * are actually filed under. So sharing a network shares everything its Sites released, and hiding one
 * hides all of it, which is much the biggest decision anybody makes in either panel and the one
 * with nothing on screen to say so. The name in the heading is the network's, and a network's own
 * file count is nought, so both panels read as a decision about almost nothing.
 *
 * Here rather than in either panel because both say it, and one sentence said twice is one
 * sentence that comes to be said two ways.
 *
 * Nought is null and not "Includes the 0 Sites within it": an ordinary site with nothing under it is the
 * common case, and a line saying so on every panel is noise that teaches people to stop reading
 * the place the real warning appears.
 *
 * The count is the page's own "Sites within" number (`sites_within` on the related counts, which
 * is null under `sites` for a Site), the Sites DIRECTLY part of this one. The decision reaches
 * further than that (a Site's own Sites within are covered too), so on a three-deep
 * network this understates what is happening. Deliberate, and it is the safe direction: the tab and
 * this sentence say the same number, where a second count of the whole subtree would be a number
 * nothing on screen agrees with. The words are the card's own ("3 Sites within"), and the page
 * says what they hold.
 */
export function includesSitesWithin(count: number): string | null {
	if (count < 1) return null;
	return `Includes the ${counted(count)} ${count === 1 ? 'Site' : 'Sites'} within it`;
}

/*
 * A restrict is absolute: it beats every share, made nearer or broader, so a share that is not in
 * force lost to a restrict. A restrict that is not in force can only be one on some of the files
 * under a person, tag or Site the user still reaches through others.
 */
export function beatenBy(source: GrantSource): string {
	return source.effect === 'share'
		? 'Not in force: a restrict beats any share'
		: "Not in force: they reach it through files this doesn't cover";
}

/**
 * One thing YOU have hidden that is concealing this, and where you did it.
 *
 * The other rule, and it is genuinely a different one: a share is about WHO MAY, and hiding is
 * about what you want on your own screen: a thing you hid is withheld from you and from nobody
 * else, and no share you hold overrides it. It inherits down the same shapes, though, so "why can
 * I not see this" is answered the same way "why is this shared" is: by naming the thing you
 * actually hid.
 */
export type VaultSource = components['schemas']['VaultSourceResponse'];

/** The rest of the line after the word Hidden, split the same way the sharing lines are. */
export function hiddenLabel(source: VaultSource, subject: ShareableType = 'item'): SourceWords {
	if (source.here) return { lead: `on this ${NOUNS[subject][0]} itself`, name: null };
	return { lead: `by ${SOURCE_NOUNS[source.source_type]}`, name: source.source_name };
}

/**
 * What YOU have hidden that is concealing this, if anything.
 *
 * Answers with an empty list while Hidden is shut, by design and not as a failure: the names in it
 * are the thing being concealed. So a caller cannot tell "nothing is hidden" from "you have not
 * unlocked", which is correct, and is why the panel only draws this block when it has something
 * to draw. It is also empty for a user that hid nothing, whatever anybody else has hidden.
 */
/**
 * Bring one of the things named above back onto your own screens.
 *
 * Each kind has its own endpoint, because hiding one is a different write for each, and every one
 * of them checks the PIN and the lock for itself. A person goes through the ordinary edit, which is
 * the only route that writes their hidden state; the name is sent back unchanged and `notes` is
 * left out entirely, which that route reads as "leave them alone" rather than as "clear them".
 *
 * Null for a source there is no way to act on from here, which is what stops a button being drawn
 * over one.
 *
 * There is no separate check for the global source: nothing can hide everything. What a HIDE may
 * be attached to is a narrower list than what a grant may name, by exactly that one member, so
 * reading the server's own list already rules it out.
 */
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

/**
 * Who, other than you, can see one thing, and through what.
 *
 * The neighbouring question to everything above and a different one: those read DECISIONS, and this
 * reads the OUTCOME. Nothing needs to have been said about a file for somebody to be able to open
 * it, which is exactly the case nobody can work out from the sharing panel: a share on the folder
 * it sits in, on a tag it carries, on the set it belongs to, or on the network above the label that
 * released it.
 *
 * The server decides every word of it. This module resolves nothing about a reach report, mirrors
 * no ladder and re-sorts no line into a verdict: `sees` is read off the stored table that answers
 * the guest's own next request, and `decides` is settled against it there. A panel that is the only
 * place somebody will ever check "can anybody else see this" is the last place a second opinion
 * belongs.
 */
export type ReachReport = components['schemas']['ReachReport'];
export type ReachUser = components['schemas']['ReachUser'];
export type ReachThrough = components['schemas']['ReachThrough'];

/**
 * The line under a name, in the two pieces the sharing panel splits its own lines into.
 *
 * The same shape and the same reason: the verb is kept out so the panel can colour it, and the NAME
 * is kept apart so it can be drawn as the thing being pointed at and linked to. Three words rather
 * than two, because a share that reaches in from somewhere else reads differently from one made on
 * the thing itself ("shared with them" against "through the Site Foo, which is shared with
 * them"), and that distinction is the whole of what this report adds.
 */
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

/**
 * WHY one user can see an entity, when nothing was ever said about the entity.
 *
 * The second half of the report. A person, a tag, a Site, a collection or a Photo Set is on
 * somebody else's wall because ONE file under it can be reached (that is the rule), so the yes
 * is true and there is usually no grant naming the entity to draw beside it. A blank there reads as
 * a bug; this names the file that makes it so.
 *
 * Asked PER USER, and only for a user whose yes has nothing behind it. It reads a page of that
 * entity's files and works the chain out for each, which is a real read rather than a reshaping of
 * the first one. Asked for everybody on arrival it would turn a panel somebody opens to check one
 * thing into a read per user whether any of them needed it or not.
 */
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

/**
 * One reason as it reads: "through the folder Holiday, which holds 3 of them".
 *
 * The same two pieces `reachLabel` splits its lines into and for the same reason: the NAME is
 * kept apart so it can be drawn as the thing being pointed at and linked to.
 *
 * The count is said only where it is worth saying. One reason covering one file adds nothing to
 * the sentence, and a reason covering every file the page held is the whole answer rather than a
 * fraction of it. A number on either would be arithmetic somebody has to do to learn nothing.
 */
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

/**
 * Make a grant. Returns the whole panel afterwards, not the row just written.
 *
 * The whole panel because a share and a restrict on the same thing are separate decisions that
 * resolve against each other: somebody who has just restricted one clip inside a shared folder
 * needs to see both rows, not a confirmation of the half they typed.
 */
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

/**
 * What one user currently holds on this thing, as one of the three words the interface uses.
 *
 * Restrict beats share wherever both exist, which is the server's rule and not this function's
 * opinion. It is mirrored here so that a row cannot read "Shared" on a screen where the server
 * would answer no. Getting that backwards is the one mistake in this file that would matter.
 */
export function standingOf(grants: Grant[], subjectUserId: string): Standing {
	if (holds(grants, subjectUserId, 'restrict')) return 'restricted';
	if (holds(grants, subjectUserId, 'share')) return 'shared';
	return 'private';
}

/**
 * One answer about one user across everything the panel is open on.
 *
 * The panel can be opened on a selection, and forty files do not have one standing, so the
 * reading is either the word they all agree on or `mixed`, which is a real answer and not a
 * failure to compute one. Pressing a button on a mixed row sets every one of them, which is the
 * only reading of "share these" that does something predictable.
 *
 * An empty panel reads as private, matching what a single thing with no grants reads as.
 */
export function readingOf(perTarget: Grant[][], subjectUserId: string): Reading {
	const words = perTarget.map((grants) => standingOf(grants, subjectUserId));
	const first = words[0] ?? 'private';
	return words.every((word) => word === first) ? first : 'mixed';
}

/**
 * Make one user's standing on one thing what somebody asked for, and nothing else.
 *
 * Written as "get to this state" rather than as "toggle", because the panel stages a decision
 * and applies it later: what it has at that point is the word somebody chose, not the presses that
 * got them there. Private is the one that takes two calls: a share and a restrict can both be
 * recorded on one thing, and leaving either behind would mean the row said Private while a grant
 * was still there.
 */
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

/**
 * Whether one specific row exists, which is a different question from the one above.
 *
 * A share and a restrict on the same thing for the same person are two rows, and both can exist;
 * the resolved answer is Restricted, because restrict wins. The controls need to know about the
 * rows rather than about the resolution, and the difference is not pedantry: a Share button lit
 * from the resolved answer sits dark while a share really is stored, so pressing it appears to do
 * nothing: it writes a row that was already there. Lit from the row, it says what is recorded,
 * and taking the restrict off then reveals the share that was underneath.
 */
export function holds(grants: Grant[], subjectUserId: string, effect: Effect): boolean {
	return grants.some((entry) => entry.subject_user_id === subjectUserId && entry.effect === effect);
}
