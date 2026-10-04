/* Adding things: a dropped file, a pasted link, an upload, a link typed into the Add panel.
 *
 * The rule for what a captured item *is* (a link to fetch, or bytes to import) lives on the
 * server, in one place, so this does not decide it a second time and cannot drift from it. A drop
 * or a paste hands over whatever it has, a link and a file both, and the server routes it: a usable
 * link wins (it fetches the original, not the thumbnail the drag carried), and the file is the
 * fallback. The client's job is only to gather what the browser gives and to say what happened.
 *
 * All of it is admin-only on the server. This does not gate on that (hiding a control is not a
 * permission), but a refusal comes back as a plain toast rather than a mystery.
 */

import { api, ApiError, request, type ApiPath } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { readClipboard } from '$lib/shell/clipboard';
import { toasts } from '$lib/shell/toasts.svelte';
import { imports } from '$lib/library/imports.svelte';
import type { components } from '$lib/api/schema';

/** A file that is being taken in, for as long as it takes to settle. A grid draws these as
 *  shimmer placeholders; it clears one when its thumbnail exists. */
export interface Importing {
	id: string;
	name: string;
	/** The "Importing..." toast this file raised, so the answer that settles it can take its place. */
	toast?: number;
}

type Accepted = components['schemas']['CaptureAccepted'];

type Origin = 'drop' | 'paste';

class Capture {
	/** Files taken in and not yet drawn. Published for the grid to render as placeholders. */
	imports = $state<Importing[]>([]);

	/**
	 * Forget a placeholder once its tile exists. Called by whatever renders the grid.
	 *
	 * A file handed over that the library already has lands ONCE: nothing is copied, and the
	 * import's note is the sentence that says where it already is ("Already here: Photos >
	 * Summer"). Said here, when the import settles, because this is where the drop that asked is
	 * still remembered; the note is read off the queue page the grid has just been told about.
	 */
	settled(id: string): void {
		const item = this.imports.find((one) => one.id === id);
		if (!item) return;
		const note = imports.page?.jobs.find((job) => job.id === id)?.note;
		if (note) {
			/* The answer replaces the question: "Importing..." left standing over "Already here" is
			   two messages about one drop, and the first of them is no longer true. */
			if (item.toast !== undefined) toasts.dismiss(item.toast);
			toasts.show(note);
		}
		this.imports = this.imports.filter((item) => item.id !== id);
	}

	/** A link typed into the Add panel. Always a download: someone who typed a link meant the link. */
	async submitUrl(url: string, destFolderId: string | null = null): Promise<void> {
		try {
			await api.post<Accepted>('/capture/import/url', {
				body: { url, dest_folder_id: destFolderId }
			});
			toasts.show('Downloading\u2026', { icon: 'download' });
		} catch (error) {
			this._failed(error);
		}
	}

	/** A file chosen from the file picker. */
	async submitFile(file: File, destFolderId: string | null = null): Promise<void> {
		const form = new FormData();
		form.set('file', file);
		if (destFolderId) form.set('dest_folder_id', destFolderId);
		await this._send('/capture/import/file', form, file.name);
	}

	/** A drop anywhere on the page. The drop target, if any, sets the destination. */
	async handleDrop(data: DataTransfer, destFolderId: string | null = null): Promise<void> {
		const url = (data.getData('text/uri-list') || data.getData('text/plain')).trim();
		const files = [...data.files];

		if (files.length > 1) {
			// Several files and no single link that could describe them all: import each.
			for (const file of files) await this._clipboard('drop', destFolderId, { file });
			return;
		}
		await this._clipboard('drop', destFolderId, { url: url || undefined, file: files[0] });
	}

	/** A clipboard paste: Ctrl-V, or right-click Paste. Same routing as a drop. */
	async handlePaste(data: DataTransfer, destFolderId: string | null = null): Promise<void> {
		const url = (data.getData('text/uri-list') || data.getData('text/plain')).trim();
		const files = [...data.files];
		if (!url && files.length === 0) return; // nothing on the clipboard we can use
		await this._clipboard('paste', destFolderId, { url: url || undefined, file: files[0] });
	}

	/**
	 * A Paste BUTTON, rather than a paste event. Same routing as everything else here.
	 *
	 * The two are not the same path and cannot be: an event hands over its own data, while this has
	 * to ASK the clipboard, which only the desktop client and a secure context are allowed to do.
	 * `canReadClipboard` is what decides whether the button is on screen at all; this is what it
	 * does when it is.
	 */
	async pasteFromClipboard(destFolderId: string | null = null): Promise<void> {
		const { text, file } = await readClipboard();
		if (!text && file === null) {
			toasts.show("There's nothing on the clipboard Sift can add");
			return;
		}
		await this._clipboard('paste', destFolderId, {
			url: text || undefined,
			file: file ?? undefined
		});
	}

	private async _clipboard(
		origin: Origin,
		destFolderId: string | null,
		item: { url?: string; file?: File }
	): Promise<void> {
		if (!item.url && !item.file) return;
		const form = new FormData();
		form.set('origin', origin);
		if (item.url) form.set('url', item.url);
		if (item.file) form.set('file', item.file);
		if (destFolderId) form.set('dest_folder_id', destFolderId);
		await this._send('/capture/import/clipboard', form, item.file?.name ?? 'link');
	}

	private async _send(path: ApiPath, form: FormData, name: string): Promise<void> {
		try {
			const accepted = await request<Accepted>('POST', path, { body: form });
			if (accepted.download_id) {
				toasts.show('Downloading\u2026', { icon: 'download' });
			} else if (accepted.job_id) {
				const toast = toasts.show('Importing\u2026');
				this.imports = [...this.imports, { id: accepted.job_id, name, toast }];
			}
		} catch (error) {
			this._failed(error);
		}
	}

	private _failed(error: unknown): void {
		// Adding media is one of the flows the server writes real guidance for: "Choose where this
		// should go, or set a default download folder" tells someone exactly what to change, and the
		// flat "That request was not valid" leaves them stuck. So this screen opts into the server's
		// own words when it sent any, and only falls back to the one-liner when it did not.
		const message = error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
		toasts.show(message, { tone: 'error' });
	}
}

export const capture = new Capture();
