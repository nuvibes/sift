/* Faces: who is in a file, where somebody turns up, and the piles waiting for a name.
 *
 * One place, because three screens read it (the item detail, a person's own page, and the two
 * sections under People), and a second copy would let one of them go on showing a face another
 * had just confirmed away.
 *
 * **Nothing here decides who may see anything.** Every number and every name arriving from the
 * server has already been resolved against whoever is asking: a pile's size is how much of it this
 * account may see, and a face whose person they have no other way of knowing about arrives with no
 * name on it. Recomputing any of that here would be a second opinion about concealment living in
 * the browser, which is the one place it can be read.
 *
 * So a face with no `person_name` is drawn as an unnamed face, deliberately and without asking why.
 */

import { accountTurn } from '$lib/shell/account-scoped';
import { isFinished } from '$lib/jobs/queue.svelte';
import { asked, type PageAsk } from '$lib/grid/anchor';
import { api, ApiError } from '$lib/api/client';
import { hiddenMark } from '$lib/entity/art';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';
import { clock } from '$lib/shell/duration';

/**
 * The consent gate: whether Sift may look for faces at all.
 *
 * Named here rather than inside the pane that draws it, because more than one screen reads it:
 * the Identify pane turns it on, and another reads it to decide whether to say the models still
 * have to be fetched. A string typed out again for every reader is a string that drifts.
 */
export const FACES_ENABLED_KEY = 'faces.enabled';

/** What recognition runs on. Named here rather than typed out at each of the four screens that
 *  read it. A settings key spelt wrong answers `undefined`, which draws as "not set" rather than
 *  as a mistake. */
export const FACES_DEVICE_KEY = 'faces.device';

export type Attribution = 'matched' | 'suggested' | 'confirmed';
export type PileStatus = 'open' | 'ignored';

export type Sighting = components['schemas']['SightingView'];
/** What a bulk yes or no is ABOUT: the whole tab, this page's faces, or the picked ones. Empty
 *  means the whole tab, which is what the card on the People Sift can recognize wall sends. A page is sent
 *  as its faces because the client sizes pages to the window and faces shift up as others are
 *  answered; see the server's `RunWrite`. */
export type RunWrite = components['schemas']['RunWrite'];

export type FaceGroup = components['schemas']['GroupCard'];

/**
 * One page of recognized appearances: of one person, or of everyone lately.
 *
 * Named after the server's own schema for it, not after the neighbouring route's schema, which the
 * server uses for something different (the same decisions gathered by person). One name meaning two
 * things across a boundary is how the next person reads the wrong one.
 */
export type AppearancePage = components['schemas']['AppearancePage'];

export type FaceSettings = components['schemas']['FaceSettingsView'];

/** Draw a blank where the server refused a picture.
 *
 *  For the screens that draw a picture for a RECORD (a folder, a decision, an Organize card),
 *  whose rows say nothing about the vault, so a refusal there cannot tell "not there yet" from
 *  "kept back". The Hidden mark means kept back and only the server may say so (`cropUrl`, where
 *  the row says `locked`); a blank keeps the card's shape and claims nothing. One rule, here.
 *
 *  Swapped once: the blank is a data address that cannot fail, and the guard keeps a handler that
 *  reassigns on every failure from becoming a loop. */
export const BLANK_PICTURE =
	'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';

export function blankOnRefusal(event: Event): void {
	const picture = event.currentTarget as HTMLImageElement;
	if (picture.src === BLANK_PICTURE) return;
	picture.src = BLANK_PICTURE;
}

/**
 * Where a face's picture lives.
 *
 * Takes the row rather than its id, because the address carries a token off that row. A crop is
 * written once when the face is found and never rewritten, so what the token says is how many times
 * what this account may see has changed, which is what stops a face going on being shown out of
 * the browser's own store after the person in it was hidden.
 *
 * A face on a file in the vault gets the hidden mark instead, decided here rather than at each of
 * the nine places that draw one. The server refuses that crop exactly as it refuses the file, so a
 * screen that asked anyway would draw the browser's torn-page glyph, which reads as Sift being
 * broken rather than as something being kept back. `locked` is only ever set for somebody whose
 * concealment mode keeps a placeholder; in the default mode the face is simply not in the list.
 *
 * Still a plain address an `<img>` can hold, and the server still re-checks permission on every
 * request that reaches it.
 */
export function cropUrl(face: { track_id: string; art?: string | null; locked?: boolean }): string {
	if (face.locked) return hiddenMark();
	const path = `/api/faces/${encodeURIComponent(face.track_id)}/crop`;
	return face.art ? `${path}?v=${encodeURIComponent(face.art)}` : path;
}

/**
 * Where a face's display-sized picture lives: the portrait, not the recognizer's square.
 *
 * The token is required for the same reason the crop's is, and there is a second reason here: this
 * address serves two different pictures over its life, the small square while the real one cannot
 * be cut and the portrait afterwards. The server only lets the portrait be kept, and a bare address
 * is re-checked rather than kept (`keeps` on the server refuses a week-long promise to an address
 * that names nothing). But a kept portrait with no token would outlive the person being hidden.
 */
export function faceCoverUrl(trackId: string, art?: string | null): string {
	const path = `/api/faces/${encodeURIComponent(trackId)}/cover`;
	return art ? `${path}?v=${encodeURIComponent(art)}` : path;
}

/* Faces are optional and most installs never turn them on, so every read here treats a refusal as
 * "there is nothing to show" rather than as an error worth a banner.
 *
 * The two are genuinely the same to a screen. A block on the item detail headed "Who is in this"
 * has nothing to draw whether the feature is off, no model is installed, or the file simply has no
 * faces in it, and an error message in place of the block would be reporting a fault on every
 * install that has not opted in.
 *
 * A refusal that is NOT about faces still propagates: a 401 has to reach the client's own handler
 * or a session that ended goes unnoticed.
 */
async function quietly<T>(work: Promise<T>, fallback: T): Promise<T> {
	try {
		return await work;
	} catch (error) {
		if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
			return fallback;
		}
		throw error;
	}
}

export async function facesOf(assetId: string): Promise<Sighting[]> {
	return quietly(api.get<Sighting[]>(`/assets/${encodeURIComponent(assetId)}/faces`), []);
}

/** How many piles one page holds. The server's own default; sent so the two cannot disagree. */
export const PILES_PER_PAGE = 24;

type FaceGroupPage = components['schemas']['GroupPage'];

export async function faceGroups(
	status: PileStatus,
	page: PageAsk = { limit: PILES_PER_PAGE, offset: 0 }
): Promise<FaceGroupPage> {
	return quietly(api.get<FaceGroupPage>('/faces/groups', { query: { status, ...asked(page) } }), {
		groups: [],
		total: 0,
		offset: 0
	});
}

/** How many faces one page of a single pile's own screen carries. Matches the server's cap. */
export const FACES_PER_PAGE = 60;

type FaceGroupDetail = components['schemas']['GroupDetail'];

/* One pile with its faces, rather than the handful a card previews.
 *
 * Not wrapped in `quietly`: on the list screen a failure means one card is short and the rest of
 * the page is still worth drawing, but here the pile IS the page, and a screen that silently
 * draws an empty pile is telling somebody their faces are gone.
 */
export async function faceGroup(
	pileId: string,
	page: PageAsk = { limit: FACES_PER_PAGE, offset: 0 }
): Promise<FaceGroupDetail> {
	return api.get<FaceGroupDetail>(`/faces/groups/${encodeURIComponent(pileId)}`, {
		query: asked(page)
	});
}

/**
 * How many crops a card on a faces wall has room for.
 *
 * The same number the server puts on one (`FaceService`'s `faces_per_card`), said here because the
 * card is what reserves the room: every card on a wall draws this many cells whether it was handed
 * that many faces or one, which is what makes them all the same size. A smaller number here would
 * start hiding crops the server had already chosen to send, and the card would then be holding back
 * part of a question whose count is written across the top of it.
 */
export const CROPS_ON_A_CARD = 12;

/** How many recognized faces one page of the Identified screen carries. */
export const IDENTIFIED_PER_PAGE = 60;

/** How many people one page of the Identified wall carries. */
export const IDENTIFIED_PEOPLE_PER_PAGE = 24;

export type IdentifiedPerson = components['schemas']['IdentifiedCard'];

/**
 * One page of the People Sift can recognize wall, with the two counts its control carries: how many People
 * Sift knows from starter pictures alone, and how many it knows otherwise (the server's
 * `IdentifiedPage.starters_only` and `others`).
 */
type IdentifiedPeoplePage = components['schemas']['IdentifiedPage'];

/** Which People the wall shows by what Sift knows them from: only the ones known from starter
 *  pictures alone, or everybody else. The server's own `StartersShow`; null is everybody. */
export type StartersShow = 'only' | 'without';

/* The same decisions, gathered by person rather than listed face by face.
 *
 * A wall of one card per face said nothing about who was on it: thirteen appearances of one person
 * read as thirteen separate answers. Grouped, somebody with faces waiting to be agreed to is
 * visible as such, which is the state the screen exists to act on.
 */
export async function identifiedPeople(
	page: PageAsk = { limit: IDENTIFIED_PEOPLE_PER_PAGE, offset: 0 },
	starters: StartersShow | null = null,
	words = ''
): Promise<IdentifiedPeoplePage> {
	/* The route's own `attribution` is left unasked: one person's own screen filters through the
	   appearance route beside this, which is where that question belongs. The wall sends two
	   narrowings: `starters`, the control on its tab line, and the search box's words as `q`,
	   matched on the server by name and alias (see `IdentifiedPanel`). */
	const query = {
		...asked(page),
		...(starters ? { starters } : {}),
		...(words.trim() ? { q: words.trim() } : {})
	};
	return quietly(api.get<IdentifiedPeoplePage>('/faces/identified/people', { query }), {
		people: [],
		total: 0,
		offset: 0,
		starters_only: 0,
		others: 0
	});
}

/** How many items one page of the review list carries. The server's own default, sent so the two
 *  cannot disagree. */
export const TO_CHECK_PER_PAGE = 24;

/** One item of the review list: a person's proposals, or a group nobody has named. */
export type ToCheckItem = components['schemas']['ToCheckCard'];

/** Which part of the list is being asked for. See the server's own `ToCheckShow`. */
export type ToCheckShow = 'waiting' | 'small' | 'ignored';

/** Which of the questions the list holds. See the server's own `ToCheckKind`. `may_be` is a
 *  card of the unnamed groups that may be one person, drawn on Needs your input beside her
 *  standing questions. */
export type ToCheckKind = 'person' | 'may_be' | 'group' | 'mismatch';

/** One group on a `may_be` card: its faces, whether it starts ticked, and why it may be her. */
export type MayBeGroup = ToCheckItem['groups'][number];

/** How many crops one group's row on a `may_be` card holds: the server's own
 *  `tuning.FACES_PER_GROUP`, which is how many faces of each group it sends and a Yes confirms.
 *  Drawn at that width so a row of three faces keeps the height of a row of six. */
export const FACES_ON_A_GROUP_ROW = 6;

type ToCheckListing = components['schemas']['ToCheckPage'];

/* What is left to check, as ONE list: a person's proposals first, then the groups by size.
 *
 * One read rather than one per kind, because the ordering, the floor and the count of what the
 * floor holds back are one question about the library. Two reads stitched together in the browser
 * would put the order in the client and leave the pager counting one population while the screen
 * drew another.
 *
 * Quiet on failure like the walls it replaced: this is a list of cards, so a failure leaves the
 * screen empty rather than leaving one card short of the truth.
 */
export async function toCheck(
	page: PageAsk = { limit: TO_CHECK_PER_PAGE, offset: 0 },
	show: ToCheckShow = 'waiting',
	kind?: ToCheckKind | readonly ToCheckKind[],
	words = ''
): Promise<ToCheckListing> {
	/* `kind` is the tier one tab asks for, filtered on the SERVER. The Faces group draws its
	   three questions as three tabs and each asks this one door for its own tier: reading the
	   whole list and filtering it here would page the wrong rows, and the pager would then count
	   one population while the screen drew another. Several tiers go up as a repeated `kind`:
	   Needs your input asks for a person's questions and the groups that may be her. */
	return quietly(
		/* The search box's words, matched on the server by name and alias, so the pager and the
		   count follow them. Sent only when there are some. */
		api.get<ToCheckListing>('/faces/to-check', {
			query: { ...asked(page), show, kind, ...(words.trim() ? { q: words.trim() } : {}) }
		}),
		{
			items: [],
			total: 0,
			offset: 0,
			small_groups: 0
		}
	);
}

/* Agree with every proposal standing for one person, in one press.
 *
 * No list of faces goes up with it, deliberately: what is being agreed to is "every proposal for
 * this person", which the server already holds. A card drawn a minute ago must not be able to
 * confirm a proposal somebody has since taken off.
 */
export async function confirmLookAlikes(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<FacesDecided> {
	return api.post<FacesDecided>(`/faces/look-alikes/${encodeURIComponent(personId)}/confirm`, {
		body: scope
	});
}

/* And refuse every one of them, in one press.
 *
 * The other answer to the same question, sending the same nothing for the same reason. Not wrapped
 * in `quietly`: this writes, and a refusal that fails silently leaves somebody believing a pile of
 * proposals has gone.
 */
export async function rejectLookAlikes(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<FacesRefused> {
	return api.post<FacesRefused>(`/faces/look-alikes/${encodeURIComponent(personId)}/reject`, {
		body: scope
	});
}

/* Yes on a "these groups may be her" card: the groups left ticked, and the faces the card SHOWED
 * of them. The server confirms those faces, offers the rest of each group as questions, and
 * filters both lists to what the card offers now, so a stale card cannot confirm anything else.
 * Not wrapped in `quietly`: this writes.
 */
export async function confirmGroups(
	personId: string,
	pileIds: readonly string[],
	trackIds: readonly string[]
): Promise<FacesDecided> {
	return api.post<FacesDecided>(`/faces/may-be/${encodeURIComponent(personId)}/confirm`, {
		body: { pile_ids: pileIds, track_ids: trackIds }
	});
}

/* And No on it: every face of these groups refused as her, so none of them is offered again,
 * not even after the groups are rebuilt. Every group the card shows goes up, ticked or not: the
 * question is the card's, whole.
 */
export async function rejectGroups(
	personId: string,
	pileIds: readonly string[]
): Promise<FacesRefused> {
	return api.post<FacesRefused>(`/faces/may-be/${encodeURIComponent(personId)}/reject`, {
		body: { pile_ids: pileIds }
	});
}

/* One person's decided faces: the card from the wall, opened up.
 *
 * Not wrapped in `quietly`, for the same reason the single pile is not: on the wall a failure
 * leaves one card short and the rest is still worth drawing, but here the person IS the page, and
 * silently drawing an empty one tells somebody their faces are gone.
 */
export async function identifiedForPerson(
	personId: string,
	page: PageAsk = { limit: IDENTIFIED_PER_PAGE, offset: 0 },
	attribution?: Attribution
): Promise<AppearancePage> {
	return api.get<AppearancePage>(`/faces/identified/people/${encodeURIComponent(personId)}`, {
		query: { ...asked(page), attribution }
	});
}

export type Strength = components['schemas']['RecognitionStrength'];

/* How reliably Sift can identify one person.
 *
 * Quiet on failure, unlike the pile reader above: this is one line on a screen that is about
 * something else, and a person's page is still worth reading when the bar could not be drawn.
 */
export async function recognitionOf(personId: string): Promise<Strength | null> {
	return quietly(
		api.get<Strength>(`/people/${encodeURIComponent(personId)}/recognition`),
		null as Strength | null
	);
}

export type ReferenceStrengths = components['schemas']['ReferenceStrengths'];

/* Everybody's reference count in one answer, for a screen choosing between people.
 *
 * Attaching a face to somebody is the moment their count matters: matching compares a new face
 * against every reference they have, so four pictures and fifty behave very differently and read
 * identically in a list of names.
 *
 * Quiet on failure. Naming somebody has to keep working on an install where this cannot be
 * answered. The hint is worth having and is not worth blocking the decision it annotates.
 */
export async function referenceStrengths(): Promise<ReferenceStrengths | null> {
	return quietly(
		api.get<ReferenceStrengths>('/faces/references/strength'),
		null as ReferenceStrengths | null
	);
}

/**
 * One person's verdict token out of the wall's reading: the server's word, never banded here.
 * Banding the count against the floor and the target here would draw a different verdict on the
 * wall from the one on the person's own page. The rule lives in one place and this only looks it
 * up; `none` for a person the reading does not hold, which is the server's own word for no
 * references.
 */
export function referenceVerdict(personId: string, strengths: ReferenceStrengths | null): string {
	return strengths?.verdicts[personId] ?? 'none';
}

export type FacesDecided = components['schemas']['FacesDecided'];

/** A No over a person's run of faces: the count, and the receipt an Undo takes back. */
export type FacesRefused = components['schemas']['FacesRefused'];

export type MatchesAgreed = components['schemas']['MatchesAgreed'];

/* Agree with every match Sift made for one person, in one press.
 *
 * No list of faces goes up with it, for the reason `confirmLookAlikes` sends none: what is being
 * agreed to is "everything Sift matched to this person", which the server already holds, and a
 * page drawn a minute ago could send back a face somebody has since moved.
 *
 * Two numbers come back and they are not the same number. `confirmed` is how many appearances now
 * carry somebody's own answer; `references` is how many pictures of that person Sift learned from
 * them, which is always fewer. So a screen must say each of them in its own words rather than
 * reporting one twice.
 *
 * Not wrapped in `quietly`: this writes, and a write that fails silently leaves somebody believing
 * a decision was taken.
 */
export async function confirmMatches(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<MatchesAgreed> {
	return api.post<MatchesAgreed>(`/faces/people/${encodeURIComponent(personId)}/confirm-matches`, {
		body: scope
	});
}

/* And say every one of those matches is not them, in one press.
 *
 * The other half of the same question, at the same address and sending the same nothing for the
 * same reason: a card that only offered to agree would leave disagreeing to one face at a time.
 *
 * One number back rather than two. Agreeing files reference pictures and has to report them;
 * refusing withdraws whatever those faces had taught, which is not a second thing somebody chose
 * and is not theirs to be told as an outcome.
 *
 * Not wrapped in `quietly`: this writes, and a refusal that fails silently leaves somebody
 * believing a page of names has come off.
 */
export async function rejectMatches(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<FacesRefused> {
	return api.post<FacesRefused>(`/faces/people/${encodeURIComponent(personId)}/reject-matches`, {
		body: scope
	});
}

/* Say who some faces are: somebody who already exists, or somebody new by name.
 *
 * One call rather than one per face. Every one of them writes to the same person's gallery, so
 * forty separate requests are forty round trips queueing on the same write, and a failure halfway
 * through leaves a decision half made with nothing saying where it stopped.
 */
export async function nameFaces(
	trackIds: string[],
	who: { personId: string } | { name: string },
	/**
	 * Also offer this name for every other unnamed face GROUPED with these.
	 *
	 * Off by default, and which surfaces turn it on is a real distinction rather than a setting.
	 * From a file, or from a whole group's own card, "this face is Marion" means the group is
	 * Marion: the clustering already claims they are one person, and made that claim at import.
	 * From INSIDE a group, picking three faces out of forty is a deliberate act of separating them
	 * from the rest, which is exactly what the deliberately over-splitting grouping needs somebody
	 * to be able to do. So that surface leaves this off.
	 *
	 * The extras come back SUGGESTED, never confirmed, and land on the person's own wall to be
	 * agreed to. `FacesDecided.offered` says how many.
	 */
	wholeGroup = false
): Promise<FacesDecided> {
	return api.post<FacesDecided>('/faces/name', {
		body: {
			track_ids: trackIds,
			whole_group: wholeGroup,
			...('personId' in who ? { person_id: who.personId } : { name: who.name })
		}
	});
}

/* Set some faces aside, leaving the rest of their pile alone.
 *
 * They become a pile of their own under Ignored: listed, reversible, and kept out of every future
 * regrouping. Nothing is deleted and no file is touched.
 */
export async function setAsideFaces(trackIds: string[]): Promise<FacesDecided> {
	return api.post<FacesDecided>('/faces/set-aside', { body: { track_ids: trackIds } });
}

export type MovedFaces = components['schemas']['MovedFaces'];

/* Merge some faces into another group, or split them into a group of their own.
 *
 * One call with two destinations: a group id merges them into that group, and no group id makes a
 * group of exactly them. The grouping deliberately over-splits, so the same stranger arriving as
 * two groups is the ordinary case rather than a fault.
 *
 * The result is kept against re-grouping and against a rescan, so nothing arranged by hand is
 * undone. It stops absorbing new faces by itself, which is the trade: a group somebody built is
 * theirs, and the clustering has no opinion about it.
 */
export async function moveFaces(trackIds: string[], pileId: string | null): Promise<MovedFaces> {
	return api.post<MovedFaces>('/faces/move', {
		body: { track_ids: trackIds, pile_id: pileId }
	});
}

/* Take some faces away for good.
 *
 * For a detection that was never a face (a hand, a logo, a pattern in a curtain) and for a real
 * face whose crop is worthless. The same answer either way: there is nothing here worth
 * recognizing, and Sift should stop asking about it.
 *
 * There is no undo, and nothing lists these afterwards. What Sift keeps is the description, so the
 * next scan of that file drops the same thing silently instead of finding it again. No file is
 * touched.
 */
export async function removeFaces(trackIds: string[]): Promise<FacesDecided> {
	return api.post<FacesDecided>('/faces/remove', { body: { track_ids: trackIds } });
}

/**
 * Agree with what Sift proposed for these faces, each as the person already suggested for it.
 *
 * No person is sent: a page of suggestions is a page about several different people, and the row
 * already knows which. Faces nobody proposed anybody for are skipped rather than refused, because
 * a selection dragged across a page picks up settled ones too.
 */
export async function acceptFaces(trackIds: string[]): Promise<FacesDecided> {
	return api.post<FacesDecided>('/faces/accept', { body: { track_ids: trackIds } });
}

export async function confirmFace(trackId: string, personId: string): Promise<void> {
	await api.post(`/faces/${encodeURIComponent(trackId)}/confirm`, {
		body: { person_id: personId }
	});
}

export async function rejectFace(trackId: string, personId: string): Promise<void> {
	await api.post(`/faces/${encodeURIComponent(trackId)}/reject`, {
		body: { person_id: personId }
	});
}

/** What setting a group aside did, and the record it can be taken back from. */
export type SetAside = components['schemas']['GroupSetAside'];

/** Every face in a pile this account may see, by id: what a verb over the WHOLE pile acts on. */
export async function pileTrackIds(pileId: string): Promise<string[]> {
	const answer = await api.get<components['schemas']['PileTracks']>(
		`/faces/groups/${encodeURIComponent(pileId)}/tracks`
	);
	return answer.track_ids;
}

export async function ignoreGroup(pileId: string): Promise<SetAside> {
	return api.post<SetAside>(`/faces/groups/${encodeURIComponent(pileId)}/ignore`, {});
}

export async function restoreGroup(pileId: string): Promise<void> {
	await api.post(`/faces/groups/${encodeURIComponent(pileId)}/restore`);
}

export async function faceSettings(): Promise<FaceSettings | null> {
	return quietly(api.get<FaceSettings | null>('/faces/settings'), null);
}

/* Queued: on a large library it is minutes of writes. Hands back the job doing it. */
export async function forgetFaces(): Promise<string> {
	const started = await api.post<components['schemas']['FetchStarted']>('/faces/forget');
	return started.job_id;
}

/* Ask for the models. Hands back the job doing it.
 *
 * A job rather than a request that waits, because over a domestic connection this is minutes. The
 * id is what a screen watches, on the same bar the jobs dashboard already draws for everything
 * else, and what it cancels with, through the cancel that already exists. Nothing here builds a
 * second progress mechanism.
 */
/* `again` fetches the files that are already here too: a damaged model counts as installed, so
   without it a second download would skip everything and report success. */
export async function fetchModels(again = false): Promise<string> {
	const started = await api.post<components['schemas']['FetchStarted']>(
		'/faces/weights/fetch',
		again ? { query: { again: true } } : {}
	);
	return started.job_id;
}

/* Look at everything in the library that wants looking at.
 *
 * Turning recognition on examines nothing by itself (consent is given once and the work is asked
 * for separately), so this is the control that covers what was already here on the day it was
 * switched on. Files that arrive afterwards are examined as they land and never reach this.
 *
 * It also covers everything examined under settings that have since changed: the tuning behind each
 * result is stored with it, so a file looked at under the old depth or the old quality bar is
 * offered again. Without that, turning the depth up changes nothing about a library already scanned.
 *
 * `everything` ignores that comparison and offers the whole library. It is the answer to what the
 * comparison cannot see (a model swapped underneath, or crops that came out badly), and it is
 * the expensive one.
 *
 * The job re-queues itself a page at a time, so this returns as soon as the first page is in
 * rather than holding a request open for a library-sized amount of work.
 */
export async function scanLibrary(options: { everything?: boolean } = {}): Promise<string> {
	const started = await api.post<components['schemas']['FetchStarted']>('/faces/scan', {
		query: options.everything ? { force: true } : undefined
	});
	return started.job_id;
}

/**
 * Pile the unclaimed faces up again, now.
 *
 * Grouping otherwise happens only when a batch of scanning settles, which leaves no way to ask for
 * it. So a change to how grouping works would appear to do nothing until something else happened
 * to trigger a rebuild.
 */
export async function regroupFaces(): Promise<string> {
	const started = await api.post<components['schemas']['FetchStarted']>('/faces/regroup');
	return started.job_id;
}

/*
 * The download's job kind, as the queue knows it.
 *
 * A constant rather than a string typed at each reader: a jobs query filtered by a type that does
 * not exist answers with an empty list, which is indistinguishable from a job that has already
 * finished, so a misspelling here reads as a download that ended the instant it began.
 *
 * The polling itself is `jobs/watch-download`, which the first-run flow shares: two functions
 * polling one job would be two answers to one question. `faces-runs` points it at this.
 */
export const FETCHING_WEIGHTS = 'face_fetch_weights';

/* Whether THIS run is still going, and what its last page had to say.
 *
 * A sweep is one job per page of the library and each one queues the next, so the job somebody
 * started is done long before the sweep is: watching that id alone reports "finished" after the
 * first couple of hundred files and reads whatever note it happened to be carrying.
 *
 * So the run is identified by the moment it started rather than by where it sits in a list.
 * Taking "the newest sweep" from the top of the list trusts the ordering to mean recency, and one
 * stale row with a timestamp in the wrong unit would sit above everything for ever and announce
 * another run's count. Anything older than the job we started is somebody else's run and is
 * ignored, and the answer is the last page of ours to finish.
 */
async function sweepState(startedId: string): Promise<{
	queueing: boolean;
	note: string | null;
	left: number;
	pages: string[];
}> {
	const page = await api.get<components['schemas']['JobsPage']>('/jobs', {
		query: { type: 'face_sweep', limit: SWEEP_PAGES_READ }
	});

	const started = page.jobs.find((one) => one.id === startedId);
	// Aged out of the list, so there is nothing left of this run to read. Over, with nothing to say.
	if (!started) return { queueing: false, note: null, left: 0, pages: [] };

	const ours = page.jobs.filter((one) => one.created_at >= started.created_at);
	const queueing = ours.some((one) => !isFinished(one.state));
	const last = ours
		.filter((one) => isFinished(one.state) && one.note)
		.sort((a, b) => b.created_at - a.created_at)[0];
	return {
		queueing,
		note: last?.note ?? null,
		left: await filesLeftToScan(),
		pages: ours.map((one) => one.id)
	};
}

/* How many pages of a sweep will be asked what they queued.
 *
 * A page is TWO HUNDRED files, not five hundred: the server asks for five hundred and the access
 * layer clamps every page to its own maximum, so a sweep walks two hundred at a time. Past this
 * limit the total is not attempted at all, so the screen has no total and shows only what one
 * page produced.
 *
 * Sixty, so a twelve thousand file library is still counted exactly. Past that the count is
 * abandoned rather than guessed, which is the honest failure and the reason there is a limit at
 * all: a total taken from the first sixty pages of a hundred would be a bar that filled up and
 * stopped, and a bar that lies is worse than no bar.
 */
const MOST_PAGES_COUNTED = 60;

/* How many sweep pages are read back when working out what a run is doing.
 *
 * One more than the most that will ever be counted, so the read can never be the thing that cuts
 * a run short. The server caps a page of jobs at two hundred, so this stays well inside what it
 * will answer.
 */
const SWEEP_PAGES_READ = MOST_PAGES_COUNTED + 1;

/* How many files this run put in the queue, counted from the sweep's own pages.
 *
 * Each page enqueues its scans as its children, so the server can be asked directly how many each
 * one produced, rather than the client counting rows, which would be paged long before the number
 * was right. Null when there are too many pages to ask about; the caller then has no total and says
 * so by not drawing a bar.
 *
 * Only worth calling once, when queueing has finished: until then the answer is still growing, and
 * a fraction whose denominator grows is a bar that goes backwards.
 */
async function filesThisRunQueued(pages: string[]): Promise<number | null> {
	return countThisRun(pages);
}

/* What this run could not read, and why.
 *
 * A failed scan leaves the queue exactly as a finished one does, so a bar counted from the queue
 * alone would count files that were never opened as scanned, and the run would end with a cheerful
 * toast. That is the worst kind of wrong: the screen reporting work that did not happen, while the
 * reason (a graphics card that is not there) sits in the job's error the whole time.
 */
export async function troubleThisRun(
	pages: string[]
): Promise<{ failed: number; reason: string | null }> {
	const failed = (await countThisRun(pages, 'failed')) ?? 0;
	if (failed === 0) return { failed: 0, reason: null };
	// One reason, not a list. Failures in a run overwhelmingly share a cause (the card is absent,
	// the drive is unplugged), and hundreds of copies of one sentence is not more informative than one.
	const page = await api
		.get<components['schemas']['JobsPage']>('/jobs', {
			query: { type: 'face_scan', state: 'failed', limit: 1 }
		})
		.catch(() => null);
	return { failed, reason: plainly(page?.jobs[0]?.error ?? null) };
}

/* A job's error as a sentence rather than as a stack trace's opening line.
 *
 * The queue records a failure as the exception's class name and its message, which is what an
 * admin reading the jobs dashboard wants. Here it is the sentence somebody is being asked to act
 * on, and "DeviceUnavailable: Recognition was set to use an NVIDIA graphics card" opens by naming
 * a Python class at somebody who wanted to know why their library has no faces in it.
 */
function plainly(error: string | null): string | null {
	return error === null ? null : error.replace(/^[A-Z][A-Za-z0-9_]*:\s*/, '');
}

async function countThisRun(pages: string[], state?: string): Promise<number | null> {
	if (pages.length === 0 || pages.length > MOST_PAGES_COUNTED) return null;
	const totals = await Promise.all(
		pages.map((parent) =>
			api
				.get<components['schemas']['JobsPage']>('/jobs', {
					query: { type: 'face_scan', parent_id: parent, limit: 1, ...(state ? { state } : {}) }
				})
				.then((one) => one.total)
				.catch(() => 0)
		)
	);
	return totals.reduce((all, one) => all + one, 0);
}

/* How many files are still waiting to be looked at, or being looked at right now.
 *
 * Two counts from the server rather than a page of jobs counted here: a sweep of a large library
 * queues thousands, and the list would be paged long before the number was right. The server
 * already answers "how many of this type are in this state", which is the whole question.
 *
 * Not filtered to the sweep's own children, deliberately. A file queued by an import that landed
 * during the sweep is a file waiting to be scanned, and somebody watching a number called "left to
 * scan" is asking about the machine's queue rather than about the provenance of each row in it.
 */
async function filesLeftToScan(): Promise<number> {
	const counts = await Promise.all(
		['queued', 'running'].map((state) =>
			api
				.get<components['schemas']['JobsPage']>('/jobs', {
					query: { type: 'face_scan', state, limit: 1 }
				})
				.then((page) => page.total)
				.catch(() => 0)
		)
	);
	return counts.reduce((all, one) => all + one, 0);
}

/* Stop a sweep, and everything it put in the queue.
 *
 * One request, on the job somebody started. A sweep is a tree (each page queues the next page and
 * a scan per file), and cancelling a job cancels everything under it however deep, so this is a
 * stop rather than a stop-queueing-more. Files queued by anything else are not in that tree and
 * carry on, which is the right answer: an import that arrived mid-sweep was not what was stopped.
 */
/* How much a running scan still has to get through, in files and in the work they amount to.
 *
 * The moments are what the estimate is built on. The server samples the queue to work them out:
 * the durations are already stored from when each file was imported, so nothing is opened to
 * answer this.
 */
async function workLeft(): Promise<{ files: number; moments: number }> {
	return quietly(api.get<components['schemas']['WorkLeft']>('/faces/work-left'), {
		files: 0,
		moments: 0
	});
}

export async function stopScanning(startedId: string): Promise<void> {
	await api.post(`/jobs/${startedId}/cancel`);
	sweep.stopped();
}

/* How often a running sweep is re-read.
 *
 * Two seconds. What moves is a count of files, which on any real machine changes slower than that,
 * and the read costs a handful of counting queries.
 */
const WATCH_EVERY = 2000;

/* How many reads the rate is worked out over, and the least time they must span.
 *
 * Thirty reads at two seconds is a minute of history: long enough that one enormous file does not
 * make the estimate lurch, short enough to follow a machine that has genuinely sped up or slowed
 * down. The floor is what stops a number appearing from two reads taken seconds apart, which on a
 * library of any size is a guess dressed as an answer.
 */
const MOST_WATCHED = 12;
const LEAST_WATCHED = 20;

/* How often the work outstanding is asked for. Five seconds: it is a sampled aggregate rather than
 * a counter, and twelve readings of it is a minute of history to measure a rate over. */
const MEASURE_EVERY = 5000;

/* The sweep that is running, if one is, held here rather than on the screen that started it.
 *
 * A watcher born and dying with the settings pane would make the bar and the count vanish the
 * moment somebody clicked away, and come back to nothing when they returned, while the work
 * carried on.
 *
 * A module-level singleton outlives every screen for the session, and `resume` goes further: it
 * finds a run already going and follows it. So a reload, a second tab, or opening the pane for
 * the first time halfway through a sweep all show the same thing, which is what somebody means
 * when they ask whether it is still working.
 */
class Sweep {
	/** The job the run started from, or null when nothing is running. */
	jobId = $state<string | null>(null);
	/** Files queued or being scanned right now. */
	left = $state(0);
	/** How many this run queued in total. Null while that is still being discovered. */
	total = $state<number | null>(null);
	/** True while pages are still being queued, when there is no fixed total to be a fraction of. */
	queueing = $state(false);
	/** Files this run could not read at all, and the reason the first of them gave. */
	failed = $state(0);
	problem = $state<string | null>(null);

	/** Files got through so far (scanned or failed), never outside the total however they race. */
	get done(): number {
		return this.total ? Math.max(0, Math.min(this.total, this.total - this.left)) : 0;
	}

	/** Of those, the ones actually looked at. A failure leaves the queue like a success does. */
	get scanned(): number {
		return Math.max(0, this.done - this.failed);
	}

	/* What the count has done lately, for working out how long the rest will take.
	 *
	 * A window rather than the whole run, and that is not a refinement. Files are wildly unequal:
	 * a photograph is one moment and a feature video is sixty, so an average over everything since
	 * the start keeps reporting a rate the machine has not managed for some time, and the estimate
	 * drifts further from the truth the longer the run goes on. What somebody wants to know is how
	 * long the REST will take at the rate it is going now.
	 */
	#seen = $state<{ at: number; moments: number }[]>([]);

	/* How much work is left, in moments, and when it was last asked for.
	 *
	 * Moments rather than files, and this is the difference between an estimate worth reading and
	 * one that lurches. A photograph is one moment and a two-hour video is sixty, so files-per-minute
	 * describes the stretch of library it was taken from rather than the stretch ahead: a run that
	 * has just cleared a thousand photographs reports a speed it will not see again the moment it
	 * reaches the videos. A moment costs about the same whatever it was cut from.
	 */
	#momentsLeft = $state(0);
	#askedAt = 0;

	/* Roughly how long is left, in seconds, or null when there is nothing honest to say.
	 *
	 * Null until there is enough to divide by: a run that has just started has scanned nothing, and
	 * "0 files a minute" turns into an estimate of forever. Null again if the queue has not moved
	 * across the whole window, which is what a stalled or paused run looks like, and no number is
	 * better than one that ticks up by a second every second.
	 */
	get remaining(): number | null {
		/* **Both of these have to be `$state` and it is not obvious why.** A getter is re-run
		 * when something reactive it READ has changed, and this one returns on the first line
		 * while the window is empty, which is every time it is called for the first twenty
		 * seconds of a run. Reading nothing reactive, it would be recorded as depending on
		 * nothing, and the line would say it was working the estimate out for the whole run while
		 * the numbers behind it moved perfectly well.
		 */
		const first = this.#seen[0];
		const last = this.#seen[this.#seen.length - 1];
		if (!first || !last || this.left <= 0) return null;
		const seconds = (last.at - first.at) / 1000;
		const got_through = first.moments - last.moments;
		if (seconds < LEAST_WATCHED || got_through <= 0) return null;
		return Math.round(this.#momentsLeft / (got_through / seconds));
	}

	/* Ask the server what is left, and remember it.
	 *
	 * Less often than the count beside it, because it is a sampled aggregate rather than two
	 * counters and because an estimate rounded to the nearest ten minutes does not improve for
	 * being recomputed every two seconds.
	 */
	async #measure(): Promise<void> {
		const now = Date.now();
		if (now - this.#askedAt < MEASURE_EVERY) return;
		this.#askedAt = now;
		const work = await workLeft().catch(() => null);
		if (!work) return;
		this.#momentsLeft = work.moments;
		this.#seen = [...this.#seen, { at: now, moments: work.moments }].slice(-MOST_WATCHED);
	}

	/* Which watcher is the live one.
	 *
	 * A counter rather than an "am I watching" flag. A watcher spends nearly all its life asleep
	 * between reads, so a flag it only clears on waking is still set for up to a tick after the
	 * run it followed has gone. And a second sweep started inside that tick would be refused a
	 * watcher by the flag while the first watcher woke, saw the run had changed, and returned:
	 * neither watching, and a bar that never moves. Every start bumps this instead; a watcher
	 * whose number has moved on returns without touching anything, and the newest one always has
	 * its own.
	 */
	#generation = 0;
	#say: ((message: string) => void) | null = null;

	/** How to announce the end. Set once by whatever knows how to show a message. */
	announceWith(say: (message: string) => void): void {
		this.#say = say;
	}

	/** Follow a run that has just been started here. */
	follow(jobId: string): void {
		this.jobId = jobId;
		this.left = 0;
		this.total = null;
		this.queueing = true;
		this.failed = 0;
		this.problem = null;
		this.#seen = [];
		this.#momentsLeft = 0;
		this.#askedAt = 0;
		this.#generation += 1;
		void this.#watch(this.#generation, jobId);
	}

	/* Pick up a run that was already going. Safe to call on every mount: a run already being
	 * followed is left alone, so several screens opening does not mean several watchers. */
	async resume(): Promise<void> {
		if (this.jobId) return;
		const root = await runningSweepRoot().catch(() => null);
		// Checked again: the read above is a round trip, and a sweep started while it was in
		// flight is the one to follow.
		if (root && !this.jobId) this.follow(root);
	}

	/* Somebody pressed stop. The watcher is retired at once rather than left to notice on its own
	 * tick, because cancelling is instant and a screen still counting down reads as a button that
	 * did nothing. */
	stopped(): void {
		this.#generation += 1;
		this.jobId = null;
		this.queueing = false;
		this.#seen = [];
	}

	/*
	 * What to say once a run is over.
	 *
	 * NOT the sweep's own note. That note is about QUEUEING ("40 files queued to scan"), and it
	 * is set the moment the walk finishes, while every one of those scans is still to run. This is
	 * said at the other end: the queue is empty, so the last of them has just finished. Using the
	 * note here would announce forty files as about to be scanned at the moment they had all been
	 * scanned.
	 *
	 * The count is the run's own, already read for the progress bar, so this asks the server
	 * nothing. Its absence is not an error: a count that could not be read costs the number, not
	 * the sentence.
	 */
	#finished(note: string | null): string {
		// Nothing needed a look. The sweep's own sentence for that says exactly this, is already in
		// the past tense, and names the reason (the settings), which no sentence here could.
		if (this.total === 0 && note) return note;
		const done = 'Sift has finished going through the library.';
		if (this.total === null || this.total === 0) return done;
		return `${done} ${this.total} ${this.total === 1 ? 'file was' : 'files were'} looked at.`;
	}

	async #watch(mine: number, startedId: string): Promise<void> {
		const turn = accountTurn();
		for (;;) {
			await new Promise((resume) => setTimeout(resume, WATCH_EVERY));
			// Stopped, superseded by a later run, or its session ended: nothing more to report.
			if (mine !== this.#generation) return;
			if (turn !== accountTurn()) return this.stopped();
			const state = await sweepState(startedId).catch(() => null);
			if (mine !== this.#generation) return;
			if (!state) {
				this.jobId = null;
				return;
			}
			this.queueing = state.queueing;
			this.left = state.left;
			// Asked for once, on the first tick after queueing ends. Before that it is still
			// growing; after that it cannot change, so asking again would be the same answer
			// at the cost of a request per page every couple of seconds.
			if (!state.queueing && this.total === null) {
				this.total = await filesThisRunQueued(state.pages).catch(() => null);
			}
			await this.#measure();
			const trouble = await troubleThisRun(state.pages).catch(() => null);
			if (trouble) {
				this.failed = trouble.failed;
				this.problem = trouble.reason;
			}
			if (state.queueing || state.left > 0) continue;
			this.jobId = null;
			/* An empty queue is not the same as a library that has been looked at.
			 *
			 * Switching recognition off does not cancel what the sweep has already queued: every
			 * one of those scans runs, checks the switch, finds it off and finishes having done
			 * nothing. The queue drains exactly as it would after a completed run, so from out
			 * here the two are identical, and "Sift has finished going through the library" is
			 * a claim somebody acts on.
			 *
			 * Asked once, here, rather than watched throughout: it costs one request at the end
			 * of a run rather than one every two seconds, and this is the only moment the answer
			 * changes what is said.
			 */
			const still = await faceSettings().catch(() => null);
			if (still && !still.enabled) {
				this.#say?.(
					'Recognition was switched off, so the files still waiting were not looked at. ' +
						'Nothing found so far has been lost.'
				);
				return;
			}
			// A run where nothing could be read must not end with a cheerful toast and a full bar.
			// What is said is what happened, and why, because the reason is the only part somebody
			// can act on.
			this.#say?.(
				this.failed > 0
					? `${counted(this.failed)} ${this.failed === 1 ? 'file' : 'files'} couldn't be read.` +
							(this.problem ? ` ${this.problem}` : '')
					: this.#finished(state.note)
			);
			return;
		}
	}
}

export const sweep = new Sweep();

/* `$lib/jobs/waiting`'s, re-exported for the screens and tests that name it through this module.
   Two features run a long pass over the whole library and both have to say the same kind of
   sentence about how long is left. */
export { describeWait } from '$lib/jobs/waiting';

/* The job a running sweep started from, or null if nothing is going.
 *
 * Walks up the chain rather than taking the newest row, because every page of a sweep is its own
 * job and only the first one is the run. Anchored on a page still working where there is one, and
 * otherwise on the newest page: a run whose queueing finished while its scans carry on is still
 * a run, and is exactly the state somebody reopening the screen is most likely to arrive in.
 */
async function runningSweepRoot(): Promise<string | null> {
	const page = await api.get<components['schemas']['JobsPage']>('/jobs', {
		query: { type: 'face_sweep', limit: SWEEP_PAGES_READ }
	});
	if (page.jobs.length === 0) return null;

	const newest = (rows: typeof page.jobs) =>
		[...rows].sort((a, b) => b.created_at - a.created_at)[0];
	let anchor = newest(page.jobs.filter((one) => !isFinished(one.state)));
	if (!anchor) {
		if ((await filesLeftToScan()) === 0) return null;
		anchor = newest(page.jobs);
	}
	if (!anchor) return null;

	const byId = new Map(page.jobs.map((one) => [one.id, one]));
	const seen = new Set<string>();
	let root = anchor;
	// The `seen` guard is not defensive dressing: a parent id that loops would spin here forever,
	// in a function called on every mount.
	while (root.parent_id && byId.has(root.parent_id) && !seen.has(root.id)) {
		seen.add(root.id);
		root = byId.get(root.parent_id) as (typeof page.jobs)[number];
	}
	return root.id;
}

/* A time range, as a person reads it.
 *
 * A still has a range of zero length and reads as a plain moment rather than "0:00 to 0:00", which
 * is the honest answer rather than a special case: a photograph has one moment in it.
 */
export function formatRange(startedMs: number, endedMs: number): string {
	const start = formatMoment(startedMs);
	return endedMs > startedMs ? `${start} – ${formatMoment(endedMs)}` : start;
}

function formatMoment(ms: number): string {
	return clock(ms / 1000);
}

export type KnownPerson = components['schemas']['KnownPerson'];
type KnownPeople = components['schemas']['KnownPeople'];

/* Who Sift can already recognize, searchable by name.
 *
 * Answers "do I have them already", asked before adding somebody. People with reference faces,
 * not People who exist, because somebody with no references is not recognizable, and saying yes
 * for them would answer a different question.
 */
export async function knownPeople(
	query: string
): Promise<{ items: KnownPerson[]; total: number; declined?: string }> {
	/* Declined while recognition is off: a state, said in the server's words, never a fault. */
	return api.get<KnownPeople>('/faces/known', { query: { q: query } }).catch((error: unknown) => {
		if (!(error instanceof ApiError && error.status === 409)) throw error;
		return { items: [], total: 0, declined: error.detail ?? error.message };
	});
}

export type StartersOffer = components['schemas']['StartersOffer'];
export type StartersQueued = components['schemas']['StartersQueued'];

/* How many People "Use stash-box pictures as starters" would act on: linked to a stash-box and with
 * no confirmed face. Read before the press, so the press can say its number first, and nothing is
 * asked of any stash-box to answer it. */
export async function startersOffer(): Promise<StartersOffer> {
	return api.get<StartersOffer>('/faces/starters');
}

/* The press: queue the task that checks each of those People's stash-box pictures and keeps the
 * ones that pass as starters. A starter only ever makes Sift ask. */
export async function useStarters(): Promise<StartersQueued> {
	return api.post<StartersQueued>('/faces/starters');
}
