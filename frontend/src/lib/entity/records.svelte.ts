/* What a person, a site, a tag or a file is made of, as the server describes it. */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { pageOf, type EntityKind } from '$lib/entity/related.svelte';
import { fetchSettingValues, onSettingsSaved } from '$lib/settings-ui/settings';

/** Whether a record draws the fields only a stash-box ever fills in. */
const SHOW_EVERY_FIELD_KEY = 'records.show_every_field';

/** The server's own shape. `links_to` says which kind of entity a value names; see `linkFor`. */
export type FieldDescription = components['schemas']['FieldDescription'];

/** What a field IS, which is what decides how it is drawn. Mirrors the server's own list. */
export type FieldKind = FieldDescription['kind'];

/** The subjects that have a record. */
/* What kind of thing a record belongs to: the registry's own list, from the server. */
export type RecordSubject = components['schemas']['Subject'];

/** Exported so a test can build one, the way `Collections` and `Imports` are. */
export class Fields {
	#subjects = $state<Record<string, FieldDescription[]>>({});
	/** Whether the one request has landed. Screens draw nothing from the registry until it has. */
	loaded = $state(false);
	/** Whether the extra fields are being shown. */
	showEvery = $state(false);
	#asking: Promise<void> | null = null;

	/** The one request, however many screens ask. Failure leaves the registry empty, not raising. */
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
				this.showEvery = (await fetchSettingValues()).get(SHOW_EVERY_FIELD_KEY) === true;
			} catch {
				this.showEvery = false;
			}
			this.loaded = true;
		})();
		return this.#asking;
	}

	/** Every field of one subject, in the server's reading order. */
	of(subject: RecordSubject): FieldDescription[] {
		if (!this.loaded) void this.load();
		return this.#subjects[subject] ?? [];
	}

	/** The fields a RECORD draws: everything except the ones another surface already draws. */
	drawn(subject: RecordSubject): FieldDescription[] {
		return this.of(subject).filter((one) => this.#isDrawn(one));
	}

	/** The fields drawn without asking: the default view of the thing. */
	onRecord(subject: RecordSubject): FieldDescription[] {
		return this.of(subject).filter((one) => one.shown === 'record');
	}

	/** The rest of the panel: the ordinary fields, plus the stash-box-only ones once asked for. */
	behindMore(subject: RecordSubject): FieldDescription[] {
		return this.of(subject).filter(
			(one) => one.shown === 'more' || (one.shown === 'every' && this.showEvery)
		);
	}

	/** Whether one field belongs on a record at all, right now. */
	#isDrawn(one: FieldDescription): boolean {
		if (one.shown === 'elsewhere') return false;
		return one.shown !== 'every' || this.showEvery;
	}

	/** One field, or nothing when this server does not describe it. */
	one(subject: RecordSubject, key: string): FieldDescription | undefined {
		return this.of(subject).find((field) => field.key === key);
	}
}

/** The one instance every screen shares. */
export const fields = new Fields();

/** What one value leads to: the thing it names, and where its page is. */
export interface RecordLink {
	kind: EntityKind;
	id: string;
	href: string;
}

/** Where one value LEADS, or null where a value is only a word. */
export function linkFor(one: FieldDescription, values: Record<string, unknown>): RecordLink | null {
	if (!one.links_to) return null;
	const id = values[`${one.key}_id`];
	if (typeof id !== 'string' || !id) return null;
	const kind = one.links_to as EntityKind;
	try {
		return { kind, id, href: pageOf(kind, id) };
	} catch {
		// A kind this client has never heard of.
		return null;
	}
}

/** Which HALF of a record a field is in: what somebody wrote, or what the file measures. */
export type FieldGroup = 'record' | 'media';

/** Whether one field belongs in the half asked for. */
export function inGroup(one: FieldDescription, group: FieldGroup): boolean {
	return (one.group === 'media') === (group === 'media');
}

/** The measured facts that belong to a MOVING picture and to nothing else. */
const MOVING_PICTURE_ONLY = ['vcodec', 'fps'];

/** Whether one field says anything at all about a file of this KIND. */
export function appliesToKind(
	one: FieldDescription,
	mediaType: string | null | undefined
): boolean {
	if (mediaType !== 'image') return true;
	return !MOVING_PICTURE_ONLY.includes(one.key);
}

/** Whether a stored value is anything at all. */
export function filledIn(value: unknown): boolean {
	if (value === null || value === undefined) return false;
	if (typeof value === 'string') return value.trim() !== '';
	if (Array.isArray(value)) return value.length > 0;
	return true;
}

/** A refusal a record's save wants the person who typed it to READ, in words the screen wrote. */
export class SaveRefused extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'SaveRefused';
	}
}

/** What to put in front of somebody when their record would not save. */
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

/* Follow the switch, on the shared instance only, so a test's `Fields` leaves no listener. */
onSettingsSaved((saved) => {
	if (SHOW_EVERY_FIELD_KEY in saved) fields.showEvery = saved[SHOW_EVERY_FIELD_KEY] === true;
});
