/* Getting a file back out of Sift: `dragOut` drags the file into another application (desktop
 * only, inside a real drag gesture); `copyOut` is what a menu does, copying a still where the
 * clipboard exists and downloading otherwise. Never a link posted anywhere. */

import { counted } from '$lib/entity/entity-counts';
import { bridge, type DragOutcome, type DragProgress } from '$lib/bridge';
import { toasts, type ToastProgress } from '$lib/shell/toasts.svelte';
import { thing, type ToastPiece } from '$lib/components/common/toast-pieces';
import { vaultPrompt } from '$lib/shell/vault.svelte';
import type { components } from '$lib/api/schema';

export interface Copyable {
	id: string;
	kind: 'image' | 'gif' | 'video';
	/** Where the app can fetch the file itself, for the browser fallback. */
	src: string;
	filename: string;
}

/** Whether this browser can put an image on the clipboard; absent over plain http. */
function canCopyImages(): boolean {
	return (
		typeof window !== 'undefined' &&
		window.isSecureContext === true &&
		typeof ClipboardItem !== 'undefined' &&
		typeof navigator !== 'undefined' &&
		navigator.clipboard != null &&
		typeof navigator.clipboard.write === 'function'
	);
}

/* How this asset leaves through a menu. Never a native drag: an OS drag runs inside the mouse
 * gesture, so one started by a click would drop immediately. */
export function copyOutAction(kind: Copyable['kind']): 'copy' | 'download' {
	return kind === 'image' && canCopyImages() ? 'copy' : 'download';
}

export async function copyOut(asset: Copyable): Promise<void> {
	if (asset.kind === 'image' && canCopyImages()) {
		if (await copyImageToClipboard(asset.src)) return;
		// The copy did not go: fall through to a download rather than leaving with nothing.
	}
	await download(asset.src, asset.filename);
}

/* Whether an OS drag started here is still in the air: Windows keeps delivering it to this page
 * as `Files`, which would raise "Drop to add" over the clip being sent. `dragend` never comes for
 * a native drag, so this is raised and lowered around `startDrag` alone. */
let nativeDragInFlight = false;

export function draggingOut(): boolean {
	return nativeDragInFlight;
}

export function dragOut(event: DragEvent, asset: { id: string; filename: string }): boolean {
	if (!bridge.canNativeDrag()) return false;
	event.preventDefault();
	nativeDragInFlight = true;
	void prepareOrDrag(asset);
	return true;
}

async function prepareOrDrag(asset: { id: string; filename: string }): Promise<void> {
	let announced: number | null = null;
	const stopWatching = bridge.onDragProgress((progress: DragProgress) => {
		if (progress.assetId !== asset.id) return;
		/* Created on the first progress report, so a file already here shows nothing. A null
		 * total draws a sweep rather than a bar that has not moved. */
		const bar: ToastProgress = {
			value: progress.total === null ? null : progress.received,
			max: progress.total ?? 0
		};
		if (announced === null) {
			const named = thing('asset', asset.id, asset.filename);
			announced = toasts.show(['Getting ', named, ' ready to drag\u2026'], { progress: bar });
			return;
		}
		toasts.advance(announced, bar);
	});

	try {
		const outcome: DragOutcome = await bridge.startDrag(asset.id);
		if (outcome === 'dragged') {
			/* A download still running into the receiving application: left to finish. */
			return;
		}
		await announceFailure(asset.filename, announced, asset.id);
	} catch {
		/* The shell threw (a drag addon missing after an upgrade): said as a refusal is, since the
		 * caller's `void` would leave it unhandled. */
		await announceFailure(asset.filename, announced, asset.id);
	} finally {
		stopWatching();
		/* Lowered here, the one place that knows the gesture is over. */
		nativeDragInFlight = false;
	}
}

/** Say "it did not go" once for both callers, settling the toast; the reason is asked by HEAD. */
async function announceFailure(
	filename: string,
	announced: number | null,
	id: string
): Promise<void> {
	const why = await isThere(`/api/assets/${id}/save-to-device`);
	if (why === 'locked') {
		if (announced !== null) toasts.dismiss(announced);
		announceUnreachable(thing('asset', id, filename), why);
		return;
	}
	const failed = ["Sift couldn't get ", thing('asset', id, filename)];
	if (announced === null) toasts.show(failed, { tone: 'error' });
	else toasts.settle(announced, failed, 'error');
}

function copyableKind(mediaType: string): Copyable['kind'] {
	if (mediaType === 'video') return 'video';
	if (mediaType === 'gif') return 'gif';
	return 'image';
}

/** A menu label saying what the action will do in this shell; a selection is always a download. */
export function saveActionLabel(mediaType: string, count = 1): string {
	// Grouped like the selection bar above it ("1,000 files selected"), never a bare 1000.
	if (count > 1) return `Save ${count.toLocaleString()} to device`;
	return copyOutAction(copyableKind(mediaType)) === 'copy' ? 'Copy image' : 'Save to device';
}

export function saveActionIcon(mediaType: string, count = 1): 'content_copy' | 'download' {
	if (count > 1) return 'download';
	return copyOutAction(copyableKind(mediaType)) === 'copy' ? 'content_copy' : 'download';
}

/**
 * Take one asset out of Sift: a still is copied as image bytes where the clipboard allows,
 * anything else downloaded. Both from `/outgoing`, never `/stream`, so no location travels.
 */
export async function saveAsset(
	asset: Pick<components['schemas']['AssetSummary'], 'id' | 'media_type' | 'original_filename'>
): Promise<void> {
	const kind = copyableKind(asset.media_type);
	const copyable = kind === 'image' && canCopyImages();
	const src = copyable
		? `/api/assets/${asset.id}/outgoing`
		: `/api/assets/${asset.id}/save-to-device`;
	await copyOut({ id: asset.id, kind, src, filename: asset.original_filename ?? asset.id });
}

/** Put one file on this device, never the clipboard: Save must not copy a photograph. */
export async function saveToDevice(
	asset: Pick<components['schemas']['AssetSummary'], 'id' | 'media_type' | 'original_filename'>
): Promise<void> {
	const src = `/api/assets/${asset.id}/save-to-device`;
	const filename = asset.original_filename ?? asset.id;
	const reachable = await isThere(src);
	if (reachable !== 'there') {
		announceUnreachable(thing('asset', asset.id, filename), reachable);
		return;
	}
	triggerDownload(src, filename);
	toasts.show(['Saved ', thing('asset', asset.id, filename)]);
}

/** Save a whole selection, each a download under the name it was imported under. */
export async function saveEach(
	assets: Pick<components['schemas']['AssetSummary'], 'id' | 'original_filename'>[]
): Promise<void> {
	const went: string[] = [];
	const missed: string[] = [];
	/* Counted apart from the missing: only a locked vault is something somebody can act on. */
	let locked = 0;
	const placed: string[] = [];
	for (const asset of assets) {
		const src = `/api/assets/${asset.id}/save-to-device`;
		const name = asset.original_filename ?? asset.id;
		const reachable = await isThere(src);
		if (reachable === 'there') {
			triggerDownload(src, name);
			went.push(name);
		} else if (reachable === 'locked') {
			locked += 1;
		} else if (reachable === 'placed') {
			placed.push(name);
		} else {
			missed.push(name);
		}
	}
	const left = missed.length + locked + placed.length;
	/* Three cases, since "eleven of twelve went" is what somebody needs to hear. */
	if (went.length === 0) {
		announceLeftBehind(missed, locked, placed, null);
		return;
	}
	if (left > 0) {
		announceLeftBehind(missed, locked, placed, `${went.length} of ${assets.length} downloaded.`);
		return;
	}
	toasts.show(
		went.length === 1
			? 'Downloaded. Drag it in from your downloads.'
			: `Downloading ${counted(went.length)} files. Find them in your downloads.`
	);
}

/** What a selection left behind, the vault first: only it can be acted on, with Unlock. */
function announceLeftBehind(
	missed: string[],
	locked: number,
	placed: string[],
	went: string | null
): void {
	const lead = went ? `${went} ` : '';
	if (locked > 0) {
		const many = locked === 1 ? 'One is' : `${locked} are`;
		toasts.show(
			`${lead}${many} in your vault. Unlock the vault to save ${locked === 1 ? 'it' : 'them'}.`,
			{
				tone: 'error',
				icon: 'lock',
				action: { label: 'Unlock', run: () => vaultPrompt.ask() }
			}
		);
		return;
	}
	if (missed.length === 0) {
		toasts.show(`${lead}${unplaceable(placed)}`, { tone: 'error' });
		return;
	}
	toasts.show(`${lead}${missing(missed)}`, { tone: 'error' });
}

/** The server's 422 in this screen's words: a HEAD has no body to read. */
function unplaceable(names: string[]): string {
	if (names.length === 1) {
		return `Sift couldn't take the location out of ${names[0]}, so it wasn't saved.`;
	}
	return `Sift couldn't take the location out of ${names.length} of them, so they weren't saved.`;
}

function missing(names: string[]): string {
	if (names.length === 1) return `Sift couldn't get ${names[0]}. The file isn't where it was.`;
	return `Sift couldn't get ${names.length} of them. Those files aren't where they were.`;
}

/*
 * Is the file there, asked by HEAD before anything claims it arrived: a download anchor tells the
 * page nothing. "There" on anything but a clean refusal. Three-valued, since a locked vault's 423
 * must not read as a missing file.
 */
type Reachable = 'there' | 'gone' | 'locked' | 'placed';

async function isThere(src: string): Promise<Reachable> {
	try {
		const answer = await fetch(src, { method: 'HEAD' });
		if (answer.status === 423) return 'locked';
		/* A location the server could not take out: said here, not a silent failed download. */
		if (answer.status === 422) return 'placed';
		return answer.status === 404 || answer.status === 410 ? 'gone' : 'there';
	} catch {
		return 'there';
	}
}

/** Say the file could not be had, in the words for the reason, with Unlock for a locked vault. */
function announceUnreachable(named: string | ToastPiece, why: Reachable): void {
	if (why === 'locked') {
		/* This screen's words: a HEAD carries no body with the server's. */
		toasts.show(
			["Sift couldn't get ", named, ". It's in your vault. Unlock the vault to save it."],
			{
				tone: 'error',
				icon: 'lock',
				action: { label: 'Unlock', run: () => vaultPrompt.ask() }
			}
		);
		return;
	}
	if (why === 'placed') {
		toasts.show(unplaceable([typeof named === 'string' ? named : named.text]), { tone: 'error' });
		return;
	}
	toasts.show(["Sift couldn't get ", named, ". The file isn't where it was."], { tone: 'error' });
}

async function copyImageToClipboard(src: string): Promise<boolean> {
	try {
		const answer = await fetch(src);
		// A refusal is not a picture: the download below asks again and says why.
		if (!answer.ok) return false;
		const blob = await answer.blob();
		await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
		toasts.show('Copied to the clipboard', { tone: 'success' });
		return true;
	} catch {
		return false;
	}
}

async function download(src: string, filename: string): Promise<void> {
	// Asked before it is claimed. See `isThere`.
	const reachable = await isThere(src);
	if (reachable !== 'there') {
		announceUnreachable(filename, reachable);
		return;
	}
	triggerDownload(src, filename);
	toasts.show('Downloaded. Drag it in from your downloads.');
}

export function triggerDownload(src: string, filename: string): void {
	const link = document.createElement('a');
	link.href = src;
	link.download = filename;
	link.rel = 'noopener';
	// Attached for the click: some browsers ignore a download on a detached anchor.
	document.body.appendChild(link);
	link.click();
	link.remove();
}
