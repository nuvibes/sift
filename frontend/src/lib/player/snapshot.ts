/*
 * Keeping a piece of what is playing: the last few seconds as a clip, or a screenshot.
 *
 * The clip is an edit like any other, through the editor's own door (`clipTheStretch`), so it is
 * filed beside the source and both Histories say what it was made from.
 *
 * A screenshot is the frame on screen (a video's, or a picture's), or in the desktop app the player
 * as it is drawn or the whole window. A browser can read the pixels of a video but never of the page
 * around it, so there the frame is the only picture on offer.
 *
 * WHERE IT GOES is the person's setting (`playback.screenshot`): copied to the clipboard, the
 * default, or saved into the library, in the folder the setting names or else the default
 * downloads folder. Never into the machine's own Downloads folder: in the app a download goes
 * wherever the shell's saved-files folder points, which is the operating system's Downloads unless
 * somebody changed it, and a screenshot saved there would be a stray file Sift could not find again.
 * Where a browser refuses the clipboard (a plain http address has none for pictures) the picture is
 * downloaded instead, so the press always leaves the person holding it; the app never needs that,
 * because it saves into the library instead.
 */

import { stampForAFileName } from '$lib/shell/when';
import { bridge, type CaptureArea } from '$lib/bridge';
import { triggerDownload } from '$lib/capture/copy-out';
import { request, ApiError } from '$lib/api/client';
import { fetchSettingValues } from '$lib/settings-ui/settings';
import { session } from '$lib/shell/session.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing } from '$lib/components/common/toast-pieces';
import { clipTheStretch } from '$lib/edit/edit.svelte';
import type { components } from '$lib/api/schema';
import { ACTS } from './acts';

/** How far back a clip of the last moments can reach, in seconds, as the menu offers them. */
export const LAST_SECONDS = [5, 10, 30, 60] as const;

/**
 * The stretch that ends where the playhead is and runs back `seconds`, in milliseconds.
 *
 * Near the start of a file there is less behind the playhead than was asked for, and the clip is
 * what there is rather than refused. Null when there is nothing behind it at all.
 */
export function lastStretch(
	playheadSeconds: number,
	seconds: number
): { startMs: number; durationMs: number } | null {
	if (!Number.isFinite(playheadSeconds) || playheadSeconds <= 0 || seconds <= 0) return null;
	const endMs = Math.round(playheadSeconds * 1000);
	const startMs = Math.max(0, endMs - Math.round(seconds * 1000));
	return { startMs, durationMs: endMs - startMs };
}

/**
 * The Clip menu's one press: the last `seconds` of the file `of` that end at the playhead, kept as
 * a new file beside it, said in a toast. One function for the player and a Theater cell, so the two
 * cut the same stretch and say the same words (`ClipButton`).
 */
export async function keepTheLast(
	of: string,
	playheadSeconds: number,
	seconds: number
): Promise<void> {
	const stretch = lastStretch(playheadSeconds, seconds);
	if (stretch === null) {
		toasts.show('Nothing has played yet to keep', { tone: 'error' });
		return;
	}
	const cut = await clipTheStretch(of, stretch.startMs, stretch.durationMs);
	toasts.show(cut.made ? `Saving the last ${seconds} seconds as a new file` : cut.because, {
		tone: cut.made ? 'success' : 'error'
	});
}

/** What a frame can be read from: a video, a picture, or the canvas a paused GIF is drawn on. */
export type Pictured = HTMLVideoElement | HTMLImageElement | HTMLCanvasElement;

/** The frame a video or a picture is showing, as a PNG, or null where it has none to give. */
async function frameOf(source: Pictured): Promise<Blob | null> {
	const width =
		source instanceof HTMLVideoElement
			? source.videoWidth
			: source instanceof HTMLImageElement
				? source.naturalWidth
				: source.width;
	const height =
		source instanceof HTMLVideoElement
			? source.videoHeight
			: source instanceof HTMLImageElement
				? source.naturalHeight
				: source.height;
	if (!width || !height) return null;
	const canvas = document.createElement('canvas');
	canvas.width = width;
	canvas.height = height;
	const context = canvas.getContext('2d');
	if (context === null) return null;
	context.drawImage(source, 0, 0);
	return new Promise((settle) => {
		try {
			canvas.toBlob((blob) => settle(blob), 'image/png');
		} catch {
			// A canvas the browser has marked as holding another site's pixels refuses to be read.
			settle(null);
		}
	});
}

/** What a saved frame is called: the moment it was taken from, so two frames of one file differ. */
export function frameName(playheadSeconds: number): string {
	const whole = Math.max(0, Math.floor(playheadSeconds));
	const minutes = Math.floor(whole / 60);
	const rest = String(whole % 60).padStart(2, '0');
	return `frame-${minutes}m${rest}s.png`;
}

/** The browser's own download, the fallback where the clipboard is refused. */
function download(blob: Blob, filename: string): void {
	const address = URL.createObjectURL(blob);
	triggerDownload(address, filename);
	// After the click has been taken: revoking immediately can cancel the download in some browsers.
	setTimeout(() => URL.revokeObjectURL(address), 1000);
}

/** What a picture can be of: the video's own frame, the player as drawn, or the whole window. */
export type Shot = 'frame' | 'player' | 'window';

/** Each picture as the menu offers it, under a trigger that says what the menu is for. */
export const SHOT_MENU = ACTS.screenshot;
export const SHOT_LABELS: Readonly<Record<Shot, string>> = {
	frame: 'This frame',
	player: 'The player',
	window: 'The whole window'
};

/** The pictures this shell can take. A browser has the frame alone, and then no menu is needed. */
export function shotsOffered(): Shot[] {
	return bridge.canCaptureWindow() ? ['frame', 'player', 'window'] : ['frame'];
}

/** What a screenshot is called: the moment it was taken, to the second, in this device's time. */
export function screenshotName(at: Date): string {
	return `screenshot-${stampForAFileName(at)}.png`;
}

/** The setting's key and its two answers, as the player slice registers them. */
const SCREENSHOT_KEY = 'playback.screenshot';
const SCREENSHOT_FOLDER_KEY = 'playback.screenshot_folder';
type ShotAction = 'copy' | 'save';

/* WHY NOT FOLLOWED: read fresh at every press and never held, so a change made anywhere
   applies to the next screenshot with nothing to follow. */
/** What this person asked a screenshot to do, and where a saved one goes ('' is the default). */
async function asked(): Promise<{ action: ShotAction; folder: string }> {
	try {
		const values = await fetchSettingValues();
		const folder = values.get(SCREENSHOT_FOLDER_KEY);
		return {
			action: values.get(SCREENSHOT_KEY) === 'save' ? 'save' : 'copy',
			folder: typeof folder === 'string' ? folder : ''
		};
	} catch {
		// A preference that could not be read is its default.
		return { action: 'copy', folder: '' };
	}
}

/** Whether this page may put a picture on the clipboard. Absent over plain http. */
function canCopyPictures(): boolean {
	return (
		typeof window !== 'undefined' &&
		window.isSecureContext === true &&
		typeof ClipboardItem !== 'undefined' &&
		navigator.clipboard != null &&
		typeof navigator.clipboard.write === 'function'
	);
}

async function copyPicture(picture: Blob): Promise<boolean> {
	if (!canCopyPictures()) return false;
	try {
		await navigator.clipboard.write([new ClipboardItem({ [picture.type]: picture })]);
		return true;
	} catch {
		return false;
	}
}

const SAVED = 'Screenshot saved to your library';

/** How a screenshot's import is asked whether its file exists yet: soon, then less often, for as
 * long as a small picture could take. A quarter second first, so the link is up inside a second
 * on a library that files it immediately. */
const LANDED_WAITS_MS = [250, 250, 500, 1000] as const;
const LANDED_FOR_MS = 30_000;

type JobRow = components['schemas']['JobView'];

/*
 * THE TOAST OPENS THE NEW FILE, so it is said when the file exists rather than when the upload is
 * accepted: a link said at acceptance would lead nowhere for the second the import takes.
 *
 * The upload is answered with an import job, and the file is named by the first step that import
 * starts (reading the file's facts carries the new file's id, which the queue reads back as the
 * step's `subject_id`). Asked soon and then less often, for as long as a small picture could take; the import
 * row itself is read beside it, so a refusal is said as one and a picture the library already had
 * says where it is (its note), rather than either waiting out the clock and claiming a save.
 */
async function sayWhereItLanded(jobId: string, name: string): Promise<void> {
	const job = encodeURIComponent(jobId);
	for (let tried = 0, waited = 0; waited < LANDED_FOR_MS; tried += 1) {
		try {
			const steps = await request<{ jobs: JobRow[] }>('GET', `/jobs/${job}/steps`, {
				query: { limit: 5 }
			});
			const landed = steps.jobs.find((step) => step.subject_id)?.subject_id;
			if (landed) {
				const called = await landedName(landed, name);
				toasts.show([`${SAVED} as `, thing('asset', landed, called)], { tone: 'success' });
				return;
			}
			const page = await request<{ jobs: JobRow[] }>('GET', '/jobs', {
				query: { type: 'import', limit: 50 }
			});
			const row = page.jobs.find((one) => one.id === jobId);
			if (row && (row.state === 'failed' || row.state === 'canceled')) {
				toasts.show(row.error ?? "The screenshot couldn't be saved", { tone: 'error' });
				return;
			}
			if (row?.state === 'done' && row.note) {
				toasts.show(row.note);
				return;
			}
		} catch {
			// A dropped read while the import runs is not an answer. Keep asking.
		}
		const wait = LANDED_WAITS_MS[Math.min(tried, LANDED_WAITS_MS.length - 1)];
		await new Promise((wake) => setTimeout(wake, wait));
		waited += wait;
	}
	toasts.show(SAVED, { tone: 'success' });
}

/* The name the new file was given, which the import decides: a screenshot of a file is named
   after it (`<name>-ss.png`), numbered where that name is taken. The name it was sent under where
   the file cannot be read back. */
async function landedName(assetId: string, sent: string): Promise<string> {
	try {
		const file = await request<{ filename?: string | null; original_filename?: string | null }>(
			'GET',
			`/assets/${encodeURIComponent(assetId)}`
		);
		// The name in the folder first: a second screenshot of one file is numbered there
		// (`-ss-1`), while the name it was imported under is the one both were sent with.
		return file?.filename || file?.original_filename || sent;
	} catch {
		return sent;
	}
}

/*
 * Into the library, through the same door an upload takes, so the screenshot is imported, filed and
 * recorded like any other file. The server resolves an empty folder to the default downloads folder,
 * and refuses in words when there is none. Only an admin may add files; for anybody else a saved
 * screenshot is the download a browser gives.
 */
async function saveToLibrary(
	picture: Blob,
	name: string,
	folder: string,
	of: string | null
): Promise<boolean> {
	const form = new FormData();
	form.set('file', new File([picture], name, { type: 'image/png' }));
	if (folder) form.set('dest_folder_id', folder);
	// The file on screen, whose name the import gives the screenshot.
	if (of) form.set('screenshot_of', of);
	try {
		const accepted = await request<{ job_id?: string | null }>('POST', '/capture/import/file', {
			body: form
		});
		if (accepted?.job_id) void sayWhereItLanded(accepted.job_id, name);
		else toasts.show(SAVED, { tone: 'success' });
		return true;
	} catch (failure) {
		toasts.show(
			failure instanceof ApiError && failure.detail
				? failure.detail
				: "The screenshot couldn't be saved",
			{ tone: 'error' }
		);
		return true;
	}
}

/**
 * Hand a picture to where this person asked for it, saying where it went. Exported for its test:
 * the choice between the clipboard, the library and a download is the part worth pinning.
 */
export async function deliver(
	picture: Blob,
	name: string,
	of: string | null = null
): Promise<void> {
	const { action, folder } = await asked();
	const inTheApp = bridge.canCaptureWindow();
	if (action === 'save' && session.isAdmin && (await saveToLibrary(picture, name, folder, of)))
		return;
	if (await copyPicture(picture)) {
		toasts.show('Screenshot copied', { tone: 'success' });
		return;
	}
	// The clipboard refused. The app saves into the library rather than to the shell's download
	// folder; a browser downloads, which is the only other place it can put a picture.
	if (inTheApp && session.isAdmin && (await saveToLibrary(picture, name, folder, of))) return;
	download(picture, name);
	toasts.show(`This browser can't copy pictures here, so ${name} was downloaded`);
}

/**
 * Take one picture and deliver it, with the shutter over the screen once it is taken.
 *
 * `stage` is the element the player is drawn in; without one, the player's picture is the frame.
 * `still` is the picture a photograph or GIF is drawn in, whose frame is the picture itself.
 * `of` is the id of the file on screen: a screenshot saved into the library is named after it.
 */
export async function takeShot(
	shot: Shot,
	{
		video,
		stage,
		still = null,
		of = null
	}: {
		video: HTMLVideoElement | null;
		stage: HTMLElement | null;
		still?: HTMLImageElement | HTMLCanvasElement | null;
		of?: string | null;
	}
): Promise<void> {
	const taken = await pictureOf(shot, video ?? still, stage);
	if (taken === null) {
		toasts.show("The screenshot couldn't be taken", { tone: 'error' });
		return;
	}
	shutter(shutterArea(shot, video ?? still, stage));
	await deliver(taken.picture, taken.name, of);
}

/** A box on the screen, in the page's own pixels: the part of a `DOMRect` the shutter is placed by. */
type Box = Pick<DOMRectReadOnly, 'left' | 'top' | 'width' | 'height'>;

/*
 * WHAT THE SHUTTER COVERS IS WHAT WAS PHOTOGRAPHED, and nothing more: the whole screen for the
 * whole window, the player's box for the player as drawn, and the picture itself for a frame. A
 * shutter over the whole screen for a frame would say the whole screen had been taken, which is the
 * one thing the frame is not. Null is the whole screen.
 */
function shutterArea(shot: Shot, source: Pictured | null, stage: HTMLElement | null): Box | null {
	if (shot === 'window') return null;
	if (shot === 'player' && stage !== null) return stage.getBoundingClientRect();
	return source === null ? null : drawnBox(source);
}

/*
 * Where a picture is actually drawn inside its element. A video or a picture fitted inside its
 * box (`object-fit: contain`, which is how every player and cell draws one) leaves bars either
 * side of it or above and below, and the frame is the picture between them, not the bars.
 */
export function drawnBox(source: Pictured): Box {
	const box = source.getBoundingClientRect();
	const natural =
		source instanceof HTMLVideoElement
			? { width: source.videoWidth, height: source.videoHeight }
			: source instanceof HTMLImageElement
				? { width: source.naturalWidth, height: source.naturalHeight }
				: { width: source.width, height: source.height };
	const fit = typeof getComputedStyle === 'function' ? getComputedStyle(source).objectFit : '';
	/* A video element fits its picture inside the box whatever the stylesheet says, which is the
	   element's own default; a picture or a canvas only when it is told to. */
	const contained =
		fit === 'contain' || (fit !== 'fill' && fit !== 'cover' && source instanceof HTMLVideoElement);
	if (!contained || !natural.width || !natural.height || !box.width || !box.height) {
		return { left: box.left, top: box.top, width: box.width, height: box.height };
	}
	const scale = Math.min(box.width / natural.width, box.height / natural.height);
	const width = natural.width * scale;
	const height = natural.height * scale;
	return {
		left: box.left + (box.width - width) / 2,
		top: box.top + (box.height - height) / 2,
		width,
		height
	};
}

/* The shutter: what was photographed darkens and comes back, once, so a press that makes no sound
   and opens nothing is seen to have done something. Drawn AFTER the picture is taken, so it is
   never in it. Over `area` alone (see `shutterArea`), or the whole screen when there is none.
   Inside whatever fills the screen when something does, because a browser draws nothing outside
   the element that is full screen. `sift-shutter` is declared once, in `app.css`, with the motion
   tokens; reduced motion shortens it to nothing there. */
function shutter(area: Box | null): void {
	if (typeof document === 'undefined') return;
	const veil = document.createElement('div');
	veil.className = 'sift-shutter';
	veil.setAttribute('aria-hidden', 'true');
	if (area !== null) {
		// Through the CSSOM rather than a style attribute: the page's policy refuses those.
		veil.style.inset = 'auto';
		veil.style.left = `${area.left}px`;
		veil.style.top = `${area.top}px`;
		veil.style.width = `${area.width}px`;
		veil.style.height = `${area.height}px`;
	}
	veil.addEventListener('animationend', () => veil.remove(), { once: true });
	// A page with animations turned off never ends one: the veil goes on its own regardless.
	setTimeout(() => veil.remove(), 1000);
	(document.fullscreenElement ?? document.body).append(veil);
}

async function pictureOf(
	shot: Shot,
	source: Pictured | null,
	stage: HTMLElement | null
): Promise<{ picture: Blob; name: string } | null> {
	if (shot === 'frame' || (shot === 'player' && stage === null)) {
		if (source === null) return null;
		const picture = await frameOf(source);
		if (picture === null) return null;
		return {
			picture,
			name:
				source instanceof HTMLVideoElement
					? frameName(source.currentTime)
					: screenshotName(new Date())
		};
	}
	const area: CaptureArea | null = shot === 'player' && stage !== null ? areaOf(stage) : null;
	/* The player without its bar or its drawer: the controls step out of the picture for the
	   moment it is taken (`shooting` on the root, read by `app.css`). The whole window is taken as
	   it is, controls and all, because that is what the whole window is. */
	const bare = shot === 'player';
	if (bare) document.documentElement.classList.add('shooting');
	try {
		await settled();
		const picture = await bridge.captureWindow(area);
		return picture === null ? null : { picture, name: screenshotName(new Date()) };
	} finally {
		if (bare) document.documentElement.classList.remove('shooting');
	}
}

function areaOf(element: HTMLElement): CaptureArea {
	const box = element.getBoundingClientRect();
	return { x: box.left, y: box.top, width: box.width, height: box.height };
}

/** The longest a screenshot waits for the screen to settle before it is taken anyway. */
const SETTLE_LIMIT_MS = 500;

/*
 * Let the menu that asked for the picture finish leaving before the picture is taken, so it is not
 * in it. One frame lets it start leaving; then every animation with an end is waited for (an
 * endless one, a spinner, never ends), up to a limit, and the frame after is the one taken.
 */
async function settled(): Promise<void> {
	await nextFrame();
	const running =
		typeof document.getAnimations === 'function'
			? document
					.getAnimations()
					.filter((one) => Number.isFinite(one.effect?.getComputedTiming().endTime ?? Infinity))
			: [];
	await Promise.race([
		Promise.allSettled(running.map((one) => one.finished)),
		new Promise((settle) => setTimeout(settle, SETTLE_LIMIT_MS))
	]);
	await nextFrame();
	await nextFrame();
}

/** The next drawn frame, or a moment's wait where none is drawn (a window out of sight draws none). */
function nextFrame(): Promise<void> {
	return new Promise((settle) => {
		const fallback = setTimeout(settle, 100);
		requestAnimationFrame(() => {
			clearTimeout(fallback);
			settle();
		});
	});
}
