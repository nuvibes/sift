// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Saying that a download finished, on whatever screen is open.
 *
 * ## Why the toaster's and not the Downloads screen's
 *
 * Said by the Downloads screen, the message would reach nobody who had gone anywhere else, which
 * is where somebody who pasted forty links and walked off is. The connection already
 * rings `downloadChanges` in every window an admin has open; this listens to that bell for as long
 * as the tab lives, so the message is said wherever the person is.
 *
 * ## Three answers, and Off is the default
 *
 * `download.finished_message` (Settings > Downloads): Off says nothing, and is the default,
 * because a queue finishing one file at a time is a message a second nobody asked for. Each download says
 * every file by its name, with Open once it is in the library. Once for many holds what finished
 * until nothing is left downloading, then says it once: the file's own message when exactly one
 * finished, the count when more did. The sound is a separate setting and stays on the Downloads
 * screen, which owns the audio.
 *
 * ## How a finish is noticed
 *
 * The bell carries a word, never a row, so each ring re-reads the newest finished downloads and
 * compares their ids with the ones already seen. The first read only remembers: opening Sift is
 * not the moment to be told about every download that ever finished. Nothing is read while the
 * setting is Off, so the default costs nothing.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';
import { downloadChanges, settingChanges } from '$lib/library/changes.svelte';
import { fetchSettings } from '$lib/settings-ui/settings';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing, type ToastPiece, type ToastWords } from '$lib/components/common/toast-pieces';

/** The setting, by the key the server declared it under. */
const FINISHED_MESSAGE_KEY = 'download.finished_message';

/** Its three answers, as stored. */
type FinishedMessage = 'off' | 'each' | 'together';

type DownloadsPage = components['schemas']['DownloadsPage'];

/** What the rule reads of a finished download. */
export type Finished = Pick<components['schemas']['DownloadItem'], 'id' | 'asset_id' | 'filename'>;

/** One message to say, and the file its name reaches (null for none). */
export interface FinishedToast {
	message: ToastWords;
	assetId: string | null;
}

/** How many of the newest finished downloads one look reads. More finishing inside one second of
 *  each other than this is a batch whose count is still told by the ones read. */
const LOOK = 50;

/**
 * THE SHAPE RULE: what a set of finished downloads says, under each answer.
 *
 * One download is always the same message, whichever answer asked for it: its file's name, and
 * Open where it landed in the library (a download that was skipped or filed nowhere has no file to
 * open, and an Open that went nowhere would be a door to nothing). Several at the same time under
 * Once for many is the count and no Open: twenty files have no single thing to open.
 */
export function finishedToasts(answer: FinishedMessage, finished: Finished[]): FinishedToast[] {
	if (answer === 'off' || finished.length === 0) return [];
	if (answer === 'together' && finished.length > 1) {
		return [{ message: `${counted(finished.length)} downloads finished`, assetId: null }];
	}
	return finished.map((one) => ({
		message: one.filename
			? ['Downloaded ', named(one.filename, one.asset_id)]
			: 'Download finished',
		assetId: one.asset_id ?? null
	}));
}

/** Read as one of the three, whatever arrived: anything else is Off, the safe direction. */
function asAnswer(value: unknown): FinishedMessage {
	return value === 'each' || value === 'together' ? value : 'off';
}

/**
 * The listener: one per tab, started by the toaster once an admin is signed in.
 *
 * `say` is the toaster, handed in so a test can listen to what would be said.
 */
export class FinishedDownloads {
	answer = $state<FinishedMessage>('off');

	/** Ids already seen finished. Null until the first look, which only remembers. */
	#seen: Set<string> | null = null;
	/** Finished and not yet said, for Once for many. */
	#held: Finished[] = [];
	#started = false;
	#reading = false;
	#again = false;

	readonly #read: () => Promise<DownloadsPage>;
	readonly #say: (toast: FinishedToast) => void;

	constructor(
		read: () => Promise<DownloadsPage> = readFinished,
		say: (toast: FinishedToast) => void = showFinished
	) {
		this.#read = read;
		this.#say = say;
	}

	/** Begin listening, once, for an admin; a guest has no downloads to hear about. */
	follow(isAdmin: boolean): void {
		if (!isAdmin || this.#started) return;
		this.#started = true;
		downloadChanges.subscribe(() => void this.look());
		settingChanges.subscribe(() => void this.load());
		void this.load();
	}

	/** Take the current answer. Turned on from Off, the next look only remembers. */
	async load(): Promise<void> {
		try {
			for (const section of await fetchSettings()) {
				const entry = section.settings?.find((one) => one.key === FINISHED_MESSAGE_KEY);
				if (entry) this.answer = asAnswer(entry.value);
			}
		} catch {
			// Unread is Off. A message missed is quieter than one said wrongly.
		}
		if (this.answer === 'off') {
			this.#seen = null;
			this.#held = [];
		} else if (this.#seen === null) {
			await this.look();
		}
	}

	/** One ring of the bell: read, compare, say. One read at a time; a ring during it reads again. */
	async look(): Promise<void> {
		if (this.answer === 'off') return;
		if (this.#reading) {
			this.#again = true;
			return;
		}
		this.#reading = true;
		try {
			const page = await this.#read();
			this.#take(page);
		} catch {
			// The next ring asks again.
		} finally {
			this.#reading = false;
		}
		if (this.#again) {
			this.#again = false;
			await this.look();
		}
	}

	#take(page: DownloadsPage): void {
		const done = page.downloads.filter((one) => one.status === 'done');
		const first = this.#seen === null;
		const seen = (this.#seen ??= new Set());
		const fresh = done.filter((one) => !seen.has(one.id));
		for (const one of fresh) seen.add(one.id);
		if (first) return;
		if (this.answer === 'each') {
			for (const toast of finishedToasts('each', fresh)) this.#say(toast);
			return;
		}
		this.#held.push(...fresh);
		const busy = (page.summary?.running ?? 0) + (page.summary?.queued ?? 0) > 0;
		if (busy || this.#held.length === 0) return;
		const held = this.#held;
		this.#held = [];
		for (const toast of finishedToasts('together', held)) this.#say(toast);
	}
}

/** The newest finished downloads: newest means most recently finished for a settled row. */
function readFinished(): Promise<DownloadsPage> {
	return api.get<DownloadsPage>('/downloads', {
		query: { show: 'done', sort: 'newest', limit: LOOK, offset: 0 }
	});
}

/** Said through the toaster: a success, the file's name the way to it where it landed. */
function showFinished(toast: FinishedToast): void {
	toasts.show(toast.message, { tone: 'success' });
}

function named(filename: string, assetId: string | null | undefined): string | ToastPiece {
	return assetId ? thing('asset', assetId, filename) : filename;
}

/** The tab's one listener. */
export const finishedDownloads = new FinishedDownloads();
