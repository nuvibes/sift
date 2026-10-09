/* Faces: who is in a file, where somebody turns up, and the piles waiting for a name. */

import { accountTurn } from '$lib/shell/account-scoped';
import { isFinished } from '$lib/jobs/queue.svelte';
import { asked, type PageAsk } from '$lib/grid/anchor';
import { api, ApiError } from '$lib/api/client';
import { hiddenMark } from '$lib/entity/art';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';
import { clock } from '$lib/shell/duration';

/** The consent gate: whether Sift may look for faces at all. */
export const FACES_ENABLED_KEY = 'faces.enabled';

/** What recognition runs on. Named here rather than typed out at each of the four screens that
 * read it. */
export const FACES_DEVICE_KEY = 'faces.device';

export type Attribution = 'matched' | 'suggested' | 'confirmed';
export type PileStatus = 'open' | 'ignored';

export type Sighting = components['schemas']['SightingView'];
/** What a bulk yes or no is ABOUT: the whole tab, this page's faces, or the picked ones. */
export type RunWrite = components['schemas']['RunWrite'];

export type FaceGroup = components['schemas']['GroupCard'];

/** One page of recognized appearances: of one person, or of everyone lately. */
export type AppearancePage = components['schemas']['AppearancePage'];

export type FaceSettings = components['schemas']['FaceSettingsView'];

/** Draw a blank where the server refused a picture. */
export const BLANK_PICTURE =
	'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';

export function blankOnRefusal(event: Event): void {
	const picture = event.currentTarget as HTMLImageElement;
	if (picture.src === BLANK_PICTURE) return;
	picture.src = BLANK_PICTURE;
}

/** Where a face's picture lives. Takes the row rather than its id, because the address carries a
 * token off that row. */
export function cropUrl(face: { track_id: string; art?: string | null; locked?: boolean }): string {
	if (face.locked) return hiddenMark();
	const path = `/api/faces/${encodeURIComponent(face.track_id)}/crop`;
	return face.art ? `${path}?v=${encodeURIComponent(face.art)}` : path;
}

/** Where a face's display-sized picture lives: the portrait, not the recognizer's square. */
export function faceCoverUrl(trackId: string, art?: string | null): string {
	const path = `/api/faces/${encodeURIComponent(trackId)}/cover`;
	return art ? `${path}?v=${encodeURIComponent(art)}` : path;
}

/* Faces are optional and most installs never turn them on, so every read here treats a refusal
 * as "there is nothing to show" rather than as an error worth a banner. */
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

/* One pile with its faces, rather than the handful a card previews. */
export async function faceGroup(
	pileId: string,
	page: PageAsk = { limit: FACES_PER_PAGE, offset: 0 }
): Promise<FaceGroupDetail> {
	return api.get<FaceGroupDetail>(`/faces/groups/${encodeURIComponent(pileId)}`, {
		query: asked(page)
	});
}

/** How many crops a card on a faces wall has room for. */
export const CROPS_ON_A_CARD = 12;

/** How many recognized faces one page of the Identified screen carries. */
export const IDENTIFIED_PER_PAGE = 60;

/** How many people one page of the Identified wall carries. */
export const IDENTIFIED_PEOPLE_PER_PAGE = 24;

export type IdentifiedPerson = components['schemas']['IdentifiedCard'];

/** One page of the People Sift can recognize wall, with the two counts its control carries: how
 * many People Sift knows from starter pictures alone, and how many it knows otherwise (the
 * server's `IdentifiedPage.starters_only` and `others`). */
type IdentifiedPeoplePage = components['schemas']['IdentifiedPage'];

/** Which People the wall shows by what Sift knows them from: only the ones known from starter
 *  pictures alone, or everybody else. The server's own `StartersShow`; null is everybody. */
export type StartersShow = 'only' | 'without';

/* The same decisions, gathered by person rather than listed face by face. */
export async function identifiedPeople(
	page: PageAsk = { limit: IDENTIFIED_PEOPLE_PER_PAGE, offset: 0 },
	starters: StartersShow | null = null,
	words = ''
): Promise<IdentifiedPeoplePage> {
	/* The route's own `attribution` is left unasked: one person's own screen filters through the
	   appearance route beside this, which is where that question belongs. */
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

/** How many items one page of the review list carries. */
export const TO_CHECK_PER_PAGE = 24;

/** One item of the review list: a person's proposals, or a group nobody has named. */
export type ToCheckItem = components['schemas']['ToCheckCard'];

/** Which part of the list is being asked for. See the server's own `ToCheckShow`. */
export type ToCheckShow = 'waiting' | 'small' | 'ignored';

/** Which of the questions the list holds. See the server's own `ToCheckKind`. */
export type ToCheckKind = 'person' | 'may_be' | 'group' | 'mismatch';

/** One group on a `may_be` card: its faces, whether it starts ticked, and why it may be her. */
export type MayBeGroup = ToCheckItem['groups'][number];

type ToCheckListing = components['schemas']['ToCheckPage'];

/* What is left to check, as ONE list: a person's proposals first, then the groups by size. */
export async function toCheck(
	page: PageAsk = { limit: TO_CHECK_PER_PAGE, offset: 0 },
	show: ToCheckShow = 'waiting',
	kind?: ToCheckKind | readonly ToCheckKind[],
	words = ''
): Promise<ToCheckListing> {
	/* `kind` is the tier one tab asks for, filtered on the SERVER. */
	return quietly(
		/* The search box's words, matched on the server by name and alias, so the pager and the
		   count follow them. */
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

/* Agree with every proposal standing for one person, in one press. */
export async function confirmLookAlikes(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<FacesDecided> {
	return api.post<FacesDecided>(`/faces/look-alikes/${encodeURIComponent(personId)}/confirm`, {
		body: scope
	});
}

/* And refuse every one of them, in one press. The other answer to the same question, sending the
 * same nothing for the same reason. */
export async function rejectLookAlikes(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<FacesRefused> {
	return api.post<FacesRefused>(`/faces/look-alikes/${encodeURIComponent(personId)}/reject`, {
		body: scope
	});
}

/* Yes on a "these groups may be her" card: the groups left ticked, and the faces the card SHOWED
 * of them. */
export async function confirmGroups(
	personId: string,
	pileIds: readonly string[],
	trackIds: readonly string[]
): Promise<FacesDecided> {
	return api.post<FacesDecided>(`/faces/may-be/${encodeURIComponent(personId)}/confirm`, {
		body: { pile_ids: pileIds, track_ids: trackIds }
	});
}

/* And No on it: every face of these groups refused as her, so none of them is offered again, not
 * even after the groups are rebuilt. */
export async function rejectGroups(
	personId: string,
	pileIds: readonly string[]
): Promise<FacesRefused> {
	return api.post<FacesRefused>(`/faces/may-be/${encodeURIComponent(personId)}/reject`, {
		body: { pile_ids: pileIds }
	});
}

/* One person's decided faces: the card from the wall, opened up. */
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

/* How reliably Sift can identify one person. */
export async function recognitionOf(personId: string): Promise<Strength | null> {
	return quietly(
		api.get<Strength>(`/people/${encodeURIComponent(personId)}/recognition`),
		null as Strength | null
	);
}

export type ReferenceStrengths = components['schemas']['ReferenceStrengths'];

/* Everybody's reference count in one answer, for a screen choosing between people. */
export async function referenceStrengths(): Promise<ReferenceStrengths | null> {
	return quietly(
		api.get<ReferenceStrengths>('/faces/references/strength'),
		null as ReferenceStrengths | null
	);
}

/** One person's verdict token out of the wall's reading: the server's word, never banded here. */
export function referenceVerdict(personId: string, strengths: ReferenceStrengths | null): string {
	return strengths?.verdicts[personId] ?? 'none';
}

export type FacesDecided = components['schemas']['FacesDecided'];

/** A No over a person's run of faces: the count, and the receipt an Undo takes back. */
export type FacesRefused = components['schemas']['FacesRefused'];

export type MatchesAgreed = components['schemas']['MatchesAgreed'];

/* Agree with every match Sift made for one person, in one press. */
export async function confirmMatches(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<MatchesAgreed> {
	return api.post<MatchesAgreed>(`/faces/people/${encodeURIComponent(personId)}/confirm-matches`, {
		body: scope
	});
}

/* And say every one of those matches is not them, in one press. */
export async function rejectMatches(
	personId: string,
	scope: RunWrite = { scope: 'all' }
): Promise<FacesRefused> {
	return api.post<FacesRefused>(`/faces/people/${encodeURIComponent(personId)}/reject-matches`, {
		body: scope
	});
}

/* Say who some faces are: somebody who already exists, or somebody new by name. */
export async function nameFaces(
	trackIds: string[],
	who: { personId: string } | { name: string },
	/** Also offer this name for every other unnamed face GROUPED with these. */
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

/* Set some faces aside, leaving the rest of their pile alone. */
export async function setAsideFaces(trackIds: string[]): Promise<FacesDecided> {
	return api.post<FacesDecided>('/faces/set-aside', { body: { track_ids: trackIds } });
}

export type MovedFaces = components['schemas']['MovedFaces'];

/* Merge some faces into another group, or split them into a group of their own. */
export async function moveFaces(trackIds: string[], pileId: string | null): Promise<MovedFaces> {
	return api.post<MovedFaces>('/faces/move', {
		body: { track_ids: trackIds, pile_id: pileId }
	});
}

/* Take some faces away for good. For a detection that was never a face (a hand, a logo, a
 * pattern in a curtain) and for a real face whose crop is worthless. */
export async function removeFaces(trackIds: string[]): Promise<FacesDecided> {
	return api.post<FacesDecided>('/faces/remove', { body: { track_ids: trackIds } });
}

/** Agree with what Sift proposed for these faces, each as the person already suggested for it. */
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

/* Ask for the models. Hands back the job doing it. */
/* `again` fetches the files that are already here too: a damaged model counts as installed, so
   without it a second download would skip everything and report success. */
export async function fetchModels(again = false): Promise<string> {
	const started = await api.post<components['schemas']['FetchStarted']>(
		'/faces/weights/fetch',
		again ? { query: { again: true } } : {}
	);
	return started.job_id;
}

/* Look at everything in the library that wants looking at. */
export async function scanLibrary(options: { everything?: boolean } = {}): Promise<string> {
	const started = await api.post<components['schemas']['FetchStarted']>('/faces/scan', {
		query: options.everything ? { force: true } : undefined
	});
	return started.job_id;
}

/** Pile the unclaimed faces up again, now. Grouping otherwise happens only when a batch of
 * scanning settles, which leaves no way to ask for it. */
export async function regroupFaces(): Promise<string> {
	const started = await api.post<components['schemas']['FetchStarted']>('/faces/regroup');
	return started.job_id;
}

/* The download's job kind, as the queue knows it. */
export const FETCHING_WEIGHTS = 'face_fetch_weights';

/* Whether THIS run is still going, and what its last page had to say. */
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

/* How many pages of a sweep will be asked what they queued. */
const MOST_PAGES_COUNTED = 60;

/* How many sweep pages are read back when working out what a run is doing. */
const SWEEP_PAGES_READ = MOST_PAGES_COUNTED + 1;

/* How many files this run put in the queue, counted from the sweep's own pages. */
async function filesThisRunQueued(pages: string[]): Promise<number | null> {
	return countThisRun(pages);
}

/* What this run could not read, and why. */
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

/* A job's error as a sentence rather than as a stack trace's opening line. */
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

/* How many files are still waiting to be looked at, or being looked at right now. */
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

/* Stop a sweep, and everything it put in the queue. */
/* How much a running scan still has to get through, in files and in the work they amount to. */
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

/* How often a running sweep is re-read. Two seconds. */
const WATCH_EVERY = 2000;

/* How many reads the rate is worked out over, and the least time they must span. */
const MOST_WATCHED = 12;
const LEAST_WATCHED = 20;

/* How often the work outstanding is asked for. Five seconds: it is a sampled aggregate rather than
 * a counter, and twelve readings of it is a minute of history to measure a rate over. */
const MEASURE_EVERY = 5000;

/* The sweep that is running, if one is, held here rather than on the screen that started it. */
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

	/* What the count has done lately, for working out how long the rest will take. */
	#seen = $state<{ at: number; moments: number }[]>([]);

	/* How much work is left, in moments, and when it was last asked for. */
	#momentsLeft = $state(0);
	#askedAt = 0;

	/* Roughly how long is left, in seconds, or null when there is nothing honest to say. */
	get remaining(): number | null {
		/* *Both of these have to be `$state` and it is not obvious why.** A getter is re-run
		 * when something reactive it READ has changed, and this one returns on the first line
		 * while the window is empty, which is every time it is called for the first twenty
		 * seconds of a run. */
		const first = this.#seen[0];
		const last = this.#seen[this.#seen.length - 1];
		if (!first || !last || this.left <= 0) return null;
		const seconds = (last.at - first.at) / 1000;
		const got_through = first.moments - last.moments;
		if (seconds < LEAST_WATCHED || got_through <= 0) return null;
		return Math.round(this.#momentsLeft / (got_through / seconds));
	}

	/* Ask the server what is left, and remember it. */
	async #measure(): Promise<void> {
		const now = Date.now();
		if (now - this.#askedAt < MEASURE_EVERY) return;
		this.#askedAt = now;
		const work = await workLeft().catch(() => null);
		if (!work) return;
		this.#momentsLeft = work.moments;
		this.#seen = [...this.#seen, { at: now, moments: work.moments }].slice(-MOST_WATCHED);
	}

	/* Which watcher is the live one. A counter rather than an "am I watching" flag. */
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
		// Checked again: the read above is a round trip, and a sweep started while it was in flight
		// is the one to follow.
		if (root && !this.jobId) this.follow(root);
	}

	/* Somebody pressed stop. The watcher is retired immediately rather than left to notice on its own
	 * tick, because cancelling is instant and a screen still counting down reads as a button that
	 * did nothing. */
	stopped(): void {
		this.#generation += 1;
		this.jobId = null;
		this.queueing = false;
		this.#seen = [];
	}

	/* What to say once a run is over. NOT the sweep's own note. */
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
			// Asked for once, on the first tick after queueing ends.
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
			/* An empty queue is not the same as a library that has been looked at. */
			const still = await faceSettings().catch(() => null);
			if (still && !still.enabled) {
				this.#say?.(
					'Recognition was switched off, so the files still waiting were not looked at. ' +
						'Nothing found so far has been lost.'
				);
				return;
			}
			// A run where nothing could be read must not end with a cheerful toast and a full bar.
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

/* `$lib/jobs/waiting`'s, re-exported for the screens and tests that name it through this module. */
export { describeWait } from '$lib/jobs/waiting';

/* The job a running sweep started from, or null if nothing is going. */
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

/* A time range, as a person reads it. */
export function formatRange(startedMs: number, endedMs: number): string {
	const start = formatMoment(startedMs);
	return endedMs > startedMs ? `${start} – ${formatMoment(endedMs)}` : start;
}

function formatMoment(ms: number): string {
	return clock(ms / 1000);
}

export type KnownPerson = components['schemas']['KnownPerson'];
type KnownPeople = components['schemas']['KnownPeople'];

/* Who Sift can already recognize, searchable by name. */
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

/* How many People "Use stash-box pictures as starters" would act on: linked to a stash-box and
 * with no confirmed face. */
export async function startersOffer(): Promise<StartersOffer> {
	return api.get<StartersOffer>('/faces/starters');
}

/* The press: queue the task that checks each of those People's stash-box pictures and keeps the
 * ones that pass as starters. A starter only ever makes Sift ask. */
export async function useStarters(): Promise<StartersQueued> {
	return api.post<StartersQueued>('/faces/starters');
}
