/* What a person, a site, a tag or a file is made of, as the server describes it.
 *
 * Fetched once per session and shared, because it is a description of the application rather than
 * of anything in the library: it cannot change while the page is open, and asking for it per screen
 * would be the same answer several times over.
 *
 * Nothing here holds a VALUE. This says what a field is called, what type it is and whether it
 * belongs on the record without asking; the screen pairs that with whatever it is drawing.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { pageOf, type EntityKind } from '$lib/entity/related.svelte';
import { fetchSettingValues, onSettingsSaved } from '$lib/settings-ui/settings';

/**
 * Whether a record draws the fields only a stash-box ever fills in.
 *
 * Read here rather than by each screen, because "which fields does this record show" has to have
 * one answer: the record, the panel and the edit form all ask this object, and a screen that read
 * the preference for itself would be a second opinion about what belongs on a record.
 */
const SHOW_EVERY_FIELD_KEY = 'records.show_every_field';

/** The server's own shape. `links_to` says which kind of entity a value names; see `linkFor`. */
export type FieldDescription = components['schemas']['FieldDescription'];

/** What a field IS, which is what decides how it is drawn. Mirrors the server's own list. */
export type FieldKind = FieldDescription['kind'];

/** The subjects that have a record. */
/* What kind of thing a record belongs to. The registry's own list, from the server, rather than
   a second copy of it here. The whole point of this module is that the fields are the server's. */
export type RecordSubject = components['schemas']['Subject'];

/**
 * Exported so a test can build one, the way `Collections` and `Imports` are.
 *
 * The app uses the single instance below and only that one: two registries in a running app could
 * answer differently about the same field, which is the drift this whole arrangement exists to
 * prevent. A test needs a fresh one per case because the ask is memoised for the session.
 */
export class Fields {
	#subjects = $state<Record<string, FieldDescription[]>>({});
	/** Whether the one request has landed. Screens draw nothing from the registry until it has. */
	loaded = $state(false);
	/**
	 * Whether the extra fields are being shown. Off is what a fresh install has, and off is what
	 * this starts as, so a record does not fill with dashes for a moment on every page load and
	 * then empty itself once the answer arrives.
	 */
	showEvery = $state(false);
	#asking: Promise<void> | null = null;

	/** The one request, however many screens ask. Failure leaves the registry empty rather than
	 *  raising: a record with no rows is a worse screen than one with them, and not an error page.
	 *
	 *  Reading `#asking` rather than `loaded` is what stops this looping: a read that happens while
	 *  the request is out returns the same promise instead of starting a second one, and a request
	 *  that FAILED still sets `loaded`, so a dead server is one attempt and not a retry per frame. */
	async load(): Promise<void> {
		this.#asking ??= (async () => {
			try {
				const answer = await api.get<components['schemas']['FieldRegistry']>('/records/fields');
				this.#subjects = answer.subjects;
			} catch {
				this.#subjects = {};
			}
			try {
				// A separate try, so a preference that cannot be read leaves the registry intact.
				// The two are one request each and neither is worth failing the other over: a
				// record with no rows is a broken screen, and one drawn without the extras is the
				// ordinary screen.
				this.showEvery = (await fetchSettingValues()).get(SHOW_EVERY_FIELD_KEY) === true;
			} catch {
				this.showEvery = false;
			}
			this.loaded = true;
		})();
		return this.#asking;
	}

	/**
	 * Every field of one subject, in the server's reading order.
	 *
	 * Asks for the registry itself on the first read, rather than leaving each screen to remember.
	 * A screen that forgets draws NOTHING, silently, with no error anywhere (an empty list of
	 * fields looks exactly like a subject that has none), and a one-line effect on every page is a
	 * rule that has to be re-obeyed every time another page is written.
	 *
	 * The same shape the site marks use: ask once, answer empty until it lands, and let the screen
	 * redraw when it does.
	 */
	of(subject: RecordSubject): FieldDescription[] {
		if (!this.loaded) void this.load();
		return this.#subjects[subject] ?? [];
	}

	/**
	 * The fields a RECORD draws: everything except the ones another surface already draws.
	 *
	 * Who is in a file and what tags it carries are declared so that an import can write them, and
	 * they are drawn above the record rather than in it. Without this filter they arrive twice: as
	 * chips at the top and as a row of dashes underneath on every file that has none.
	 */
	drawn(subject: RecordSubject): FieldDescription[] {
		return this.of(subject).filter((one) => this.#isDrawn(one));
	}

	/** The fields drawn without asking: the default view of the thing. */
	onRecord(subject: RecordSubject): FieldDescription[] {
		return this.of(subject).filter((one) => one.shown === 'record');
	}

	/**
	 * The rest of the panel: the ordinary fields one step in, plus the stash-box-only ones when
	 * they have been asked for.
	 *
	 * They are separate placements so the switch can hide one without the other: a site's `Part of`
	 * and a person's breast type would otherwise both be `more`, and hiding the second would take
	 * the first with it. This is where they come back together, in the server's own order rather
	 * than one group after the other. A record read with the switch on should be the record read
	 * with it off, with rows filled in between.
	 */
	behindMore(subject: RecordSubject): FieldDescription[] {
		return this.of(subject).filter(
			(one) => one.shown === 'more' || (one.shown === 'every' && this.showEvery)
		);
	}

	/** Whether one field belongs on a record at all, right now.
	 *
	 * `elsewhere` never does: another surface already draws it. `every` does only while the
	 * preference is on, and that is the whole of what the preference does. */
	#isDrawn(one: FieldDescription): boolean {
		if (one.shown === 'elsewhere') return false;
		return one.shown !== 'every' || this.showEvery;
	}

	/** One field, or nothing when this server does not describe it. Not a fault: a screen asking
	 *  about a field an older server has never heard of draws nothing rather than an error. */
	one(subject: RecordSubject, key: string): FieldDescription | undefined {
		return this.of(subject).find((field) => field.key === key);
	}
}

/** The one instance every screen shares. */
export const fields = new Fields();

/**
 * What one value leads to: the thing it names, and where its page is.
 *
 * The kind and the id travel with the address rather than being read back out of it, because a
 * surface draws the thing as well as linking to it (the cover beside the name comes from the
 * same pair), and picking an id apart from a path is how one of the two comes to be wrong.
 */
export interface RecordLink {
	kind: EntityKind;
	id: string;
	href: string;
}

/**
 * Where one value LEADS, or null where a value is only a word.
 *
 * A field can name something that has a page of its own: a site's "Part of" is a network, which is
 * a site, with a wall and a cover and a record of its own. Drawn as text, that page would be
 * reachable only by searching for the word, and the reference the server holds would stop at the
 * screen.
 *
 * Here rather than in each of the three surfaces that draw a record, and that is the point rather
 * than a saving: the record, the panel and the file's grid would each hold their own idea of which
 * fields are links, and the one that drifts is the one nobody looks at. The server says which kind
 * of thing the value names (`links_to`) and carries its id beside the value (`<key>_id`); this is
 * the one place that turns the pair into an address.
 *
 * Null when either half is missing, and that is honest rather than defensive: a name the server
 * could not pair with a row is still worth reading, and a link to nowhere is worse than none.
 */
export function linkFor(one: FieldDescription, values: Record<string, unknown>): RecordLink | null {
	if (!one.links_to) return null;
	const id = values[`${one.key}_id`];
	if (typeof id !== 'string' || !id) return null;
	const kind = one.links_to as EntityKind;
	try {
		return { kind, id, href: pageOf(kind, id) };
	} catch {
		// A kind this client has never heard of. The registry is a description of the SERVER, so a
		// newer one can name something this build has no page for, and the module's whole promise
		// is that such a field still draws. The value is read as the text it is.
		return null;
	}
}

/**
 * Which HALF of a record a field is in: what somebody wrote, or what the file measures.
 *
 * The server's own mark (`group` on the declaration), read here rather than decided here. A file's
 * record is drawn as two panes and the split has to be the registry's: a list of keys in the
 * component that draws them is a list somebody has to remember to add to, and the field they
 * forget lands in the wrong pane with nothing to say so.
 */
export type FieldGroup = 'record' | 'media';

/**
 * Whether one field belongs in the half asked for.
 *
 * Anything that is not the MEASURED half is the written one, which is the direction that keeps an
 * older client honest: a group a newer server invents is unknown here, and a field drawn in
 * neither pane is a field that has disappeared. It draws with the rest of the record instead.
 */
export function inGroup(one: FieldDescription, group: FieldGroup): boolean {
	return (one.group === 'media') === (group === 'media');
}

/**
 * The measured facts that belong to a MOVING picture and to nothing else.
 *
 * A photograph has no frames going past, so it has no frame rate and nothing encoding a video
 * stream, yet ffprobe reads a PNG or a JPEG as a one-frame video stream: `vcodec` comes back
 * `png` or `mjpeg` and `fps` comes back the image demuxer's own default of 25. Neither number says
 * anything true about the photograph, so a record repeating them tells a person the file is
 * something it is not.
 *
 * The STORED facts are kept as they are. They are what ffprobe answered and other things read them
 * (the playback plan asks what a file is encoded with), so blanking the columns would be
 * throwing a measurement away to fix a caption. What is wrong is only that the record repeats them
 * at somebody, and this is the one place that says so.
 *
 * A GIF is deliberately NOT a still here: it has real frames at a real rate, and its record should
 * say so. Only `image` is covered.
 */
const MOVING_PICTURE_ONLY = ['vcodec', 'fps'];

/**
 * Whether one field says anything at all about a file of this KIND.
 *
 * Here rather than in the grid that draws the record, because the panel above it counts the same
 * fields: the number on the Media tab is how many of that half are filled, and a rule applied in
 * only one of the two is a tab saying nine over a pane showing seven.
 *
 * Anything that is not a file (a person, a Site, a photo set) has no media type and every
 * field applies, which is what an absent one means.
 */
export function appliesToKind(
	one: FieldDescription,
	mediaType: string | null | undefined
): boolean {
	if (mediaType !== 'image') return true;
	return !MOVING_PICTURE_ONLY.includes(one.key);
}

/**
 * Whether a stored value is anything at all.
 *
 * An empty list and a blank string are both "not filled in"; a nought and a `false` are answers
 * and stay: dropping them would hide "0 chapters" on exactly the file where that is the fact
 * somebody came to read.
 *
 * Here rather than in the grid that draws the record, because the panel above it COUNTS the same
 * thing: the number on a tab is how many of that half's fields are filled, and a second definition
 * of "filled" would be a tab saying three over a pane showing two.
 */
export function filledIn(value: unknown): boolean {
	if (value === null || value === undefined) return false;
	if (typeof value === 'string') return value.trim() !== '';
	if (Array.isArray(value)) return value.length > 0;
	return true;
}

/**
 * A refusal a record's save wants the person who typed it to READ, in words the screen wrote.
 *
 * `RecordForm` answers anything its `onsave` throws with one flat sentence, and that is the right
 * default: most of what goes wrong in a save is a fault in Sift, and a stack message put in front
 * of somebody is noise they can do nothing with. A caller that knows better throws this with its
 * own sentence (a file's Filename box renames the file on disk, and says why a name was refused).
 */
export class SaveRefused extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'SaveRefused';
	}
}

/**
 * What to put in front of somebody when their record would not save.
 *
 * Three answers, in order. The caller's own sentence, where it threw a `SaveRefused`. The route's
 * own sentence, where it refused what was TYPED: a 422 carrying a detail in words (a tag or a Site
 * filed under its own branch). Every record route writes those for the person reading them, so the
 * rule is the seam's and not each page's; a page that had to opt in would be a page that forgot
 * to, its refusal reading "That couldn't be saved." with nothing to change. Anything else is the flat
 * sentence, which keeps a fault in Sift from being reported as though it were about the typing: a
 * validation report is a list rather than words and arrives with no detail at all.
 *
 * Here rather than in each form, because there are TWO forms (`RecordForm` and the file's own
 * `RecordGrid`), and two copies of one rule is how a rule comes to be true in one place and not
 * the other.
 */
/** The field a refusal is about, where the route named one, so a form can say it there. */
export function refusedField(failure: unknown): string | undefined {
	return failure instanceof ApiError && failure.status === 422 ? failure.field : undefined;
}

export function saveProblem(failure: unknown): string {
	if (failure instanceof SaveRefused) return failure.message;
	if (failure instanceof ApiError && failure.status === 422 && failure.detail)
		return failure.detail;
	return "That couldn't be saved.";
}

/* Follow the switch while the app is open.
 *
 * Registered against the shared instance only, and at module scope, so a `Fields` a test builds
 * does not attach a listener that outlives it. The settings sheet sits over the record it changes,
 * so without this the switch would look broken: the rows appear at the next page load rather than
 * behind the sheet that was just closed.
 */
onSettingsSaved((saved) => {
	if (SHOW_EVERY_FIELD_KEY in saved) fields.showEvery = saved[SHOW_EVERY_FIELD_KEY] === true;
});
