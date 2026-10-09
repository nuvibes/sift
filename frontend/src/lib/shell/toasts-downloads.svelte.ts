// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Saying a download finished on whatever screen is open, from the `downloadChanges` bell.
 * `download.finished_message`: Off (the default, costing nothing), each, or once for many. The
 * first read only remembers.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';
import { downloadChanges, settingChanges } from '$lib/library/changes.svelte';
import { fetchSettings } from '$lib/settings-ui/settings';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing, type ToastPiece, type ToastWords } from '$lib/components/common/toast-pieces';

const FINISHED_MESSAGE_KEY = 'download.finished_message';

type FinishedMessage = 'off' | 'each' | 'together';

type DownloadsPage = components['schemas']['DownloadsPage'];

export type Finished = Pick<components['schemas']['DownloadItem'], 'id' | 'asset_id' | 'filename'>;

export interface FinishedToast {
	message: ToastWords;
	assetId: string | null;
}

/** How many of the newest finished downloads one look reads. */
const LOOK = 50;

/**
 * THE SHAPE RULE: one download is its name and Open; several together under once-for-many, a count.
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

/** Anything else is Off, the safe direction. */
function asAnswer(value: unknown): FinishedMessage {
	return value === 'each' || value === 'together' ? value : 'off';
}

/** One per tab; `say` is handed in so a test can listen. */
export class FinishedDownloads {
	answer = $state<FinishedMessage>('off');

	#seen: Set<string> | null = null;
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

	follow(isAdmin: boolean): void {
		if (!isAdmin || this.#started) return;
		this.#started = true;
		downloadChanges.subscribe(() => void this.look());
		settingChanges.subscribe(() => void this.load());
		void this.load();
	}

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

function readFinished(): Promise<DownloadsPage> {
	return api.get<DownloadsPage>('/downloads', {
		query: { show: 'done', sort: 'newest', limit: LOOK, offset: 0 }
	});
}

function showFinished(toast: FinishedToast): void {
	toasts.show(toast.message, { tone: 'success' });
}

function named(filename: string, assetId: string | null | undefined): string | ToastPiece {
	return assetId ? thing('asset', assetId, filename) : filename;
}

export const finishedDownloads = new FinishedDownloads();
