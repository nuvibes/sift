/* Adding things: a dropped file, a pasted link, an upload, a typed link. The server decides what a
 * captured item is; this gathers what the browser gives and says what happened. */

import { api, ApiError, request, type ApiPath } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { readClipboard } from '$lib/shell/clipboard';
import { toasts } from '$lib/shell/toasts.svelte';
import { imports } from '$lib/library/imports.svelte';
import type { components } from '$lib/api/schema';

/** A file being taken in, drawn as a placeholder until its thumbnail exists. */
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

	/** Forget a placeholder once its tile exists, saying "Already here" for a duplicate. */
	settled(id: string): void {
		const item = this.imports.find((one) => one.id === id);
		if (!item) return;
		const note = imports.page?.jobs.find((job) => job.id === id)?.note;
		if (note) {
			/* The answer replaces "Importing...", which is no longer true. */
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

	/** A Paste button asks the clipboard, which only the desktop or a secure context may. */
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
		// The server's own words for adding media say what to change; else the one-liner.
		const message = error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
		toasts.show(message, { tone: 'error' });
	}
}

export const capture = new Capture();
