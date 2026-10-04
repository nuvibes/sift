/* What Sift would not take, and what has left the machine.
 *
 * Two admin-only records that exist whether or not anything shows them, and a record shown
 * nowhere is the worst state for one to be in: the facts exist, somebody looking for them has no
 * way in, and the absence of a screen reads exactly like the absence of the thing.
 *
 * They sit in one module because they are one screen's worth of reading ("what happened to my
 * files"), and because neither is big enough to be worth its own.
 */

import { api, ApiError } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { components } from '$lib/api/schema';

/* LIVE: followed by lib/components/organize/QuarantinePanel.svelte (loadRefused on the library bell; SkippedPanel the same) */

/** One file Sift moved into a folder of its own because it would not take it. */
export type Quarantined = components['schemas']['QuarantinedView'];

/** One file left exactly where it was, with a note that Sift walked past it. */
export type Skipped = components['schemas']['RejectionView'];

/* One library folder's worth of skipped files.
 *
 * Not exported: it is the shape of a field on the store below and nothing outside reads it by name.
 * A screen iterating `leftAlone` gets the type from the store, which is where it should come from:
 * an exported name nothing imports is a promise to keep something stable that nobody depends on. */
type SkippedInRoot = components['schemas']['RootRejectionsView'];

/** One line of the save log: somebody kept a copy of a file on their own device. */
export type Save = components['schemas']['SaveRecord'];

/* Why a file was refused, in words rather than the name of a rule.
 *
 * The same words the folder screen uses, because they describe the same decision made by the same
 * gate: a reader meeting "not a kind of file Sift imports" in one place and "signature_not_allowed"
 * in the other would reasonably think they were two different problems.
 */
const WHY: Record<string, string> = {
	empty: 'an empty file',
	unreadable: "Sift couldn't read it",
	signature_not_allowed: "its bytes aren't a picture or a video",
	extension_contradicts_signature: 'the name and the contents disagree',
	does_not_end_where_it_should: 'it stops early, so it may be a part-finished download',
	not_decodable: "it wouldn't open",
	animated_webp_unreadable: "the GIF wouldn't read",
	no_video_stream: "there's no video in it",
	pixels_exceeded: 'the picture is too large to open safely',
	removed_from_sift: "removed from Sift; it's inside a ZIP file",
	unknown: "Sift didn't record why"
};

/* What the refused bytes turned out to be, in words, for the kinds that are worth naming. The
   gate labels them with a media type (`ingress.describe`), which is shorthand nobody reading this
   list should have to know. A label that is not here is shown as it is rather than dropped: it is
   still the one fact about the file somebody might act on. */
const LOOKS_LIKE: Record<string, string> = {
	'application/zip': 'a ZIP archive',
	'application/pdf': 'a PDF',
	'application/x-msdownload': 'a Windows program',
	'text/html': 'a web page'
};

/* What the gate says when it could not tell what the bytes were. Saying "detected as
   unrecognized" would tell the reader nothing the sentence before it had not. */
const UNRECOGNIZED = 'unrecognized';

/* The name endings that promise a video, so a web page under one reads as "saved as a video". */
const VIDEO_NAME = /\.(mp4|m4v|mov|mkv|webm)$/i;

/** What a refusal means, in a sentence, with what the file turned out to be where that is known.
 *
 * `name` is the file's name where the caller has it: a web page refused under a media name (a
 * site's error page saved in place of the clip) is said as exactly that, because "its bytes are
 * not a picture or video" is true and leaves the reader to guess why. */
export function explain(
	reason: string,
	detected: string | null | undefined,
	name?: string | null
): string {
	const said = WHY[reason] ?? reason.replaceAll('_', ' ');
	const looks = detected && detected !== UNRECOGNIZED ? detected : null;
	if (looks === 'text/html' && reason === 'signature_not_allowed') {
		const as = name && VIDEO_NAME.test(name) ? 'a video' : 'a picture';
		return sentence(`a web page saved as ${as}`);
	}
	if (!looks) return sentence(said);
	return sentence(`${said}; it looks like ${LOOKS_LIKE[looks] ?? looks}`);
}

/** The first letter up: each line under a file's name starts a sentence. */
function sentence(words: string): string {
	return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The whole line under a quarantined file's name.
 *
 * One sentence rather than three joined by dots, because a file quarantined before Sift wrote notes
 * knows neither its reason nor where it came from, and composing the unknowns would produce "Sift
 * did not record why, from somewhere Sift did not record", which says the same thing twice and reads
 * like a fault. Where nothing is known, say that once.
 */
export function aboutQuarantined(one: Quarantined, when: string): string {
	if (!one.explained) return `Sift didn't record why this was quarantined — ${when}`;
	return `${explain(one.reason, one.detected, one.original_name)}, from ${cameFrom(one.origin)} — ${when}`;
}

/* Where a file came from, said the way somebody would say it. The stored words are the ingress
   gate's own, and two of them ("scan", "watch") never reach this list at all: a file Sift found in
   somebody's library is left where it is and is in the other pile. */
const FROM: Record<string, string> = {
	download: 'a download',
	upload: 'an upload',
	drop: 'a file you dropped in',
	paste: 'something you pasted',
	unknown: "somewhere Sift didn't record"
};

/* Not exported: the one caller is `aboutQuarantined` below, which is what a screen actually wants.
   Exporting it as well would offer two ways to write the same line and invite them to differ. */
function cameFrom(origin: string): string {
	return FROM[origin] ?? origin.replaceAll('_', ' ');
}

function refusal(error: unknown): string {
	return error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
}

export class Refused {
	moved = $state<Quarantined[]>([]);
	leftAlone = $state<SkippedInRoot[]>([]);
	keepDays = $state(0);
	saves = $state<Save[]>([]);
	savesTotal = $state(0);
	/** True once a load has succeeded. A failed attempt leaves it false. See `Maintenance`. */
	loaded = $state(false);
	loading = $state(false);
	problem = $state<string | null>(null);
	busy = $state(false);

	/** How many files are in the second pile, across every library folder. */
	get skippedCount(): number {
		/* The whole count, which the server says beside each root's page: a root can refuse a hundred
		   thousand files and the listing carries the first page of them. */
		return this.leftAlone.reduce((count, root) => count + root.rejections_total, 0);
	}

	/* Three screens read this and none of them wants all of it.
	 *
	 * The two refused piles are cards on the Organize board and the save log stays under
	 * Maintenance, so each half is loadable on its own: a card that fetched the save log to draw
	 * a list of quarantined files would be a request nobody on that screen can see the result of.
	 * `load` is still here for a caller that genuinely shows both.
	 */
	async load(): Promise<void> {
		await this.#reading(() => Promise.all([this.#readRefused(), this.#readSaves()]).then());
	}

	/** The two piles of files Sift would not take, and how long the moved ones are kept. */
	async loadRefused(): Promise<void> {
		await this.#reading(() => this.#readRefused());
	}

	/** Every time somebody kept a copy of a file on their own device. */
	async loadSaves(): Promise<void> {
		await this.#reading(() => this.#readSaves());
	}

	async #reading(work: () => Promise<void>): Promise<void> {
		this.loading = true;
		this.problem = null;
		try {
			await work();
			/* Only now. Setting it in the failure path too would let a screen go on to say "nothing
			   has been quarantined" on the strength of a request that was refused. */
			this.loaded = true;
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.loading = false;
		}
	}

	async #readRefused(): Promise<void> {
		const quarantine =
			await api.get<components['schemas']['QuarantineView']>('/library/quarantine');
		this.moved = quarantine.moved;
		this.leftAlone = quarantine.left_alone;
		this.keepDays = quarantine.keep_days;
	}

	async #readSaves(): Promise<void> {
		const log = await api.get<components['schemas']['SaveLogResponse']>('/save-log', {
			query: { limit: 50 }
		});
		this.saves = log.items;
		this.savesTotal = log.total;
	}

	/** Delete one quarantined file for good. */
	async remove(one: Quarantined): Promise<string | undefined> {
		this.busy = true;
		try {
			await api.del(`/library/quarantine/${encodeURIComponent(one.id)}`);
			await this.#readRefused();
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}

	/** Forget that a file was refused, so the next scan looks at it again.
	 *
	 * Not "import it": the refusal is what stops the scanner re-reading the same unreadable file on
	 * every pass, so removing it puts the file back in front of the gate rather than past it. A file
	 * that really is a disguised executable is refused again, which is the right answer for somebody
	 * who pressed this on a hunch.
	 */
	async allow(rootId: string, relPath: string): Promise<string | undefined> {
		this.busy = true;
		try {
			await api.post(`/library/roots/${rootId}/rejections/allow`, { body: { rel_path: relPath } });
			await this.#readRefused();
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}
}
