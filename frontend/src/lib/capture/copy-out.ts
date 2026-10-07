/* Getting a file back out of Sift and into something else: Discord, most of the time.
 *
 * There are TWO ways out, and they are separate on purpose.
 *
 * `dragOut` is the real thing: a drag of the file straight into another application, which only the
 * desktop client can do. It hangs off a genuine drag gesture, because an operating-system drag runs
 * inside that gesture and ends when the button comes up.
 *
 * `copyOut` is what a MENU does, in every shell including the desktop one. A still image is copied
 * to the clipboard where that is possible, and everything else (and any image where it is not)
 * is downloaded so it can be dragged in by hand.
 *
 * "Where that is possible" is load-bearing. The clipboard image API only exists in a secure context
 * (https, or localhost), and a self-hosted Sift reached over plain http has no clipboard to write
 * to. There the honest answer is a download, not an error, so the action always does something.
 *
 * What it never does is silently post a link somewhere. That is the behaviour the whole approach
 * exists to avoid: a "share" that turns out to have published a URL is worse than no share at all.
 */

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

/** Whether this browser can put an image on the clipboard at all. Absent over plain http, and the
 *  whole reason the image path has a download fallback rather than an error. */
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

/* How this asset would leave the app THROUGH A MENU, so the item can be labelled for what it does.
 *
 * A native drag is deliberately not one of the answers, and that is a fact about Windows rather
 * than a preference. An OS drag runs inside the mouse gesture and ends the moment the button comes
 * up. So a menu item, which is a click, would start a drag that instantly "drops" wherever the
 * pointer happens to be. Taking a file out by dragging has its own entry point, on the media
 * itself, where there is a real drag to hang it on. See `dragOut`.
 */
export function copyOutAction(kind: Copyable['kind']): 'copy' | 'download' {
	return kind === 'image' && canCopyImages() ? 'copy' : 'download';
}

/** Take a file out of Sift by the best means this shell has, from a menu. */
export async function copyOut(asset: Copyable): Promise<void> {
	if (asset.kind === 'image' && canCopyImages()) {
		if (await copyImageToClipboard(asset.src)) return;
		// The copy did not go: fall through to a download rather than leaving with nothing.
	}
	await download(asset.src, asset.filename);
}

/**
 * Drag one asset out of the window into another application. Only the desktop client can.
 *
 * Called from a real `dragstart`, and it cancels the browser's own drag first: the two cannot both
 * run, and the browser's would carry a link to the page rather than the file. The id goes over and
 * nothing else: there is no path in this call, by design, so the gesture has no way to reach a
 * file the person could not already open.
 *
 * When the library is on another machine the bytes have to arrive before there is anything to drag.
 * That cannot happen inside the gesture, so the first attempt fetches with a progress bar and the
 * second one (of the same clip) is instant.
 */
/* Whether an operating-system drag STARTED HERE is still in the air.
 *
 * A drag out of the window is handed to Windows, and Windows keeps delivering it to this page
 * while the pointer is still over it, as `dragenter` carrying `Files`, which is exactly what an
 * import looks like. So dragging a clip towards another application would raise a full-window
 * "Drop to add" over the library on the way out, offering to import the file it was already
 * sending.
 *
 * `dragstart` cannot answer this. Its default is prevented for a native drag, which means no
 * `dragend` will ever arrive: the same asymmetry that can leave a window deaf to every drop.
 * The OS drag DOES have an end, though: `startDrag` settles when Windows' own drag loop finishes.
 * So this is raised and lowered around that call, and by nothing else.
 */
let nativeDragInFlight = false;

/** Whether a drag this application handed to Windows is still running. */
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
		/* The toast is created on the FIRST progress report rather than up front. A file already on
		 * this machine never sends one, so the common case, the whole point of the feature, puts
		 * nothing on the screen at all. */
		/* `null` when the server never said how big the file is, which draws a SWEEP rather than a
		 * bar that has not moved: two different things to be looking at. */
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
			/* The bar, if there is one, is about a download that is STILL RUNNING: the drop has
			 * happened and the receiving application is reading the file as it arrives. Left to
			 * finish on its own rather than settled here, because "dropped" and "finished arriving"
			 * are two different moments and this is only the first of them. */
			return;
		}
		await announceFailure(asset.filename, announced, asset.id);
	} catch {
		/* The shell threw rather than answering: a missing or mismatched drag addon is the case
		 * that produces it, and it is exactly the one somebody hits after an Electron upgrade.
		 *
		 * Caught here rather than left to escape. The caller does `void prepareOrDrag(...)` inside a
		 * `dragstart` handler, so an escaping rejection is an unhandled one: nothing tells the person
		 * their drag went nowhere, and nothing tells us either. Saying the same sentence a refusal
		 * says is right, because from where they are standing it is the same thing: the file did
		 * not go. */
		await announceFailure(asset.filename, announced, asset.id);
	} finally {
		stopWatching();
		/* Lowered HERE, in the one place that knows the gesture is over. Windows' drag loop is
		 * synchronous, so this settles when the file has been dropped, or the drag abandoned, or the
		 * fetch that had to happen first has failed, all three of which end the drag. */
		nativeDragInFlight = false;
	}
}

/** "It did not go", said once, because two callers say it and they must say the same thing. Settles
 *  the progress toast where there was one, so a bar does not sit at half for the rest of the day.
 *
 *  The shell answers a drag with "dragged" or "not dragged" and nothing else, which is right: an
 *  IPC bridge that carried HTTP statuses would be a second copy of the server's refusals. So the
 *  reason is asked for HERE, and only once a drag has already failed: one HEAD on the path that
 *  knows, on a path nobody is waiting on. */
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

/** The shape `copyOut` understands, worked out from an asset's `media_type` string. */
function copyableKind(mediaType: string): Copyable['kind'] {
	if (mediaType === 'video') return 'video';
	if (mediaType === 'gif') return 'gif';
	return 'image';
}

/** A menu label that says what the action will actually do in this shell. `count` above one is a
 *  whole selection, which is always a download regardless of what any one of them is. */
export function saveActionLabel(mediaType: string, count = 1): string {
	// Grouped like the selection bar above it ("1,000 files selected"), never a bare 1000.
	if (count > 1) return `Save ${count.toLocaleString()} to device`;
	return copyOutAction(copyableKind(mediaType)) === 'copy' ? 'Copy image' : 'Save to device';
}

/** The icon to pair with `saveActionLabel`. */
export function saveActionIcon(mediaType: string, count = 1): 'content_copy' | 'download' {
	if (count > 1) return 'download';
	return copyOutAction(copyableKind(mediaType)) === 'copy' ? 'content_copy' : 'download';
}

/** Take one asset out of Sift, given only what a tile or a detail view already holds.
 *
 * A still is copied by its real image bytes where the clipboard allows it, so the clipboard gets a
 * picture rather than an octet stream; everything else (and any still where the clipboard is out
 * of reach) is fetched through the save endpoint, which names the download and records it.
 *
 * The still's bytes come from `/outgoing`, never `/stream`: a photo copied into a chat window
 * would carry where it was taken. `/outgoing` is the same picture with any location taken out, and
 * the download a failed copy falls back to takes the same address.
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

/**
 * Put one file on this device, whatever kind it is. Never the clipboard.
 *
 * `saveAsset` above asks what the BEST way out is for this kind of file, and for a still where the
 * clipboard is reachable the answer is to copy it, which is right for a menu item that says "Copy
 * image" and wrong for anything that says Save. Ctrl-S putting a photograph on the clipboard and
 * leaving the downloads folder empty reads exactly like a key that does nothing.
 *
 * So this is the other question, asked plainly: the file, on the disk, under the name it has. In the
 * desktop client the shell then writes it straight into the download folder without a dialog; in a
 * browser it is an ordinary download and the browser decides where.
 */
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

/** Save a whole selection in one go. Every one is a download (copying several images to the
 *  clipboard means nothing), and each keeps the name it was imported under. */
export async function saveEach(
	assets: Pick<components['schemas']['AssetSummary'], 'id' | 'original_filename'>[]
): Promise<void> {
	const went: string[] = [];
	const missed: string[] = [];
	/* Counted apart from the missing, because they are two different situations wearing one
	   word. A selection holding a file on an unplugged drive and a selection holding a file in a
	   locked vault must not both say "those files are not where they were": only one of the
	   two is a sentence somebody can act on. */
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
	/* Said as three cases rather than one, because "eleven of twelve went" is the answer
	   somebody needs and "Downloaded" is not. A selection with one dead file in it is the
	   ordinary case on a drive that has been unplugged, and a single sentence would claim the
	   whole lot had arrived. */
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

/**
 * What a selection left behind, said once however many reasons there were.
 *
 * The vault comes first where both happened, for the reason `BulkWriteDone.after` gives about the
 * same choice on the server: it is the only one of the two the person can do something about, and
 * the Unlock button is only worth offering beside the sentence that explains it.
 */
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

/** The location a file holds could not be taken out, so it stayed. The server's refusal (422),
 *  said in this screen's words: a HEAD has no body to read the server's own from. */
function unplaceable(names: string[]): string {
	if (names.length === 1) {
		return `Sift couldn't take the location out of ${names[0]}, so it wasn't saved.`;
	}
	return `Sift couldn't take the location out of ${names.length} of them, so they weren't saved.`;
}

/** "It is not there", named where naming one is useful and counted where it is not. */
function missing(names: string[]): string {
	if (names.length === 1) return `Sift couldn't get ${names[0]}. The file isn't where it was.`;
	return `Sift couldn't get ${names.length} of them. Those files aren't where they were.`;
}

/*
 * Is the file actually there, asked before anything claims it arrived?
 *
 * ## The fault this exists for
 *
 * A download is an anchor with a `download` attribute that gets clicked. That is fire and forget in
 * the strongest sense: the browser takes over, and the page is never told what happened, not the
 * status, not an error, nothing. So a toast fired on the click would say "Downloaded. Drag it in
 * from your downloads." while the browser's own download list said the file was not available. Two
 * statements about one action, on one screen, disagreeing.
 *
 * There is no event to wait for and there cannot be one, so the honest thing is to ASK first. A
 * HEAD costs one round trip and no bytes (Starlette answers HEAD on any GET route and sends the
 * headers alone), and the server does the same existence check it would do for the real request. A
 * file that vanishes in the moment between this and the click is still possible and is then the
 * browser's message to give; what is fixed is the case where Sift already knew and said otherwise.
 *
 * "There" on anything but a clean refusal, deliberately. A proxy that will not pass a HEAD, or a
 * network that drops it, must not turn into "your file is gone": the download itself is the real
 * test, and being wrong in that direction costs a browser message instead of a silently withheld
 * file.
 *
 * ## Why the answer is three-valued and not a boolean
 *
 * A boolean would make a locked vault come back as false: pressing Save on a hidden file would say
 * "Sift could not get it. The file is not where it was": a sentence about a file that is exactly
 * where it always was, and the more alarming of the two readings. A concealed asset and a missing
 * one both answer 404 to anybody else, on purpose.
 *
 * To the vault's OWNER only, the server says 423. See `sift.kernel.reach` for why that gives
 * nothing away: the vault conceals from onlookers rather than from the person holding the PIN, and
 * this is the person holding the PIN.
 */
type Reachable = 'there' | 'gone' | 'locked' | 'placed';

async function isThere(src: string): Promise<Reachable> {
	try {
		const answer = await fetch(src, { method: 'HEAD' });
		if (answer.status === 423) return 'locked';
		/* The file holds a location the server couldn't take out, so the GET would refuse it:
		   said here, rather than a browser download failing with nothing of Sift's to say why. */
		if (answer.status === 422) return 'placed';
		return answer.status === 404 || answer.status === 410 ? 'gone' : 'there';
	} catch {
		return 'there';
	}
}

/**
 * Say the file could not be had, in the words that fit the reason.
 *
 * One place, because three call sites ask this question (one file, a whole selection, and the
 * drag), and a locked vault has to offer the same way out of all three. The Unlock button is the
 * point: told only that something is locked, somebody has to go and find the control themselves.
 */
function announceUnreachable(named: string | ToastPiece, why: Reachable): void {
	if (why === 'locked') {
		/* This screen's own words rather than the server's. The server's sentence travels in the
		   body of the 423, and a HEAD has no body, so there is nothing to read here even in
		   principle, and a client-side copy of a server string would be two spellings to keep in
		   step for no gain. The STATUS is the fact; this is the wording for this screen. */
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
		// Not an error to report: the caller downloads instead, which is a real outcome.
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

/** Hand an address to the browser's download folder under a name; the one anchor-click in the app. */
export function triggerDownload(src: string, filename: string): void {
	const link = document.createElement('a');
	link.href = src;
	link.download = filename;
	link.rel = 'noopener';
	// In the DOM for the click: some browsers ignore a download on an anchor that was never attached.
	document.body.appendChild(link);
	link.click();
	link.remove();
}
