/*
 * Keeping a piece of what plays: the last seconds as a clip (`clipTheStretch`), or a screenshot of
 * the frame, the player or the window (those two in the app only). Where it goes is
 * `playback.screenshot`: the clipboard by default, or the library; never the machine's Downloads.
 * Where a browser refuses the clipboard, it downloads.
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

export const LAST_SECONDS = [5, 10, 30, 60] as const;

/** Near the start, what there is; null when nothing is behind the playhead. */
export function lastStretch(
	playheadSeconds: number,
	seconds: number
): { startMs: number; durationMs: number } | null {
	if (!Number.isFinite(playheadSeconds) || playheadSeconds <= 0 || seconds <= 0) return null;
	const endMs = Math.round(playheadSeconds * 1000);
	const startMs = Math.max(0, endMs - Math.round(seconds * 1000));
	return { startMs, durationMs: endMs - startMs };
}

/** One function for the player and a cell (`ClipButton`). */
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

export type Pictured = HTMLVideoElement | HTMLImageElement | HTMLCanvasElement;

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
			settle(null);
		}
	});
}

export function frameName(playheadSeconds: number): string {
	const whole = Math.max(0, Math.floor(playheadSeconds));
	const minutes = Math.floor(whole / 60);
	const rest = String(whole % 60).padStart(2, '0');
	return `frame-${minutes}m${rest}s.png`;
}

function download(blob: Blob, filename: string): void {
	const address = URL.createObjectURL(blob);
	triggerDownload(address, filename);
	// Revoking immediately can cancel the download in some browsers.
	setTimeout(() => URL.revokeObjectURL(address), 1000);
}

export type Shot = 'frame' | 'player' | 'window';

export const SHOT_MENU = ACTS.screenshot;
export const SHOT_LABELS: Readonly<Record<Shot, string>> = {
	frame: 'This frame',
	player: 'The player',
	window: 'The whole window'
};

export function shotsOffered(): Shot[] {
	return bridge.canCaptureWindow() ? ['frame', 'player', 'window'] : ['frame'];
}

export function screenshotName(at: Date): string {
	return `screenshot-${stampForAFileName(at)}.png`;
}

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
		return { action: 'copy', folder: '' };
	}
}

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

/** Soon, then less often, for as long as a small picture could take. */
const LANDED_WAITS_MS = [250, 250, 500, 1000] as const;
const LANDED_FOR_MS = 30_000;

type JobRow = components['schemas']['JobView'];

/*
 * THE TOAST OPENS THE NEW FILE, so it waits for the import's first step to name it (`subject_id`).
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

/* The import decides the name (`<name>-ss.png`, numbered). */
async function landedName(assetId: string, sent: string): Promise<string> {
	try {
		const file = await request<{ filename?: string | null; original_filename?: string | null }>(
			'GET',
			`/assets/${encodeURIComponent(assetId)}`
		);
		return file?.filename || file?.original_filename || sent;
	} catch {
		return sent;
	}
}

/* Through the upload's door; an empty folder is the default downloads folder. An admin's alone. */
async function saveToLibrary(
	picture: Blob,
	name: string,
	folder: string,
	of: string | null
): Promise<boolean> {
	const form = new FormData();
	form.set('file', new File([picture], name, { type: picture.type || 'image/png' }));
	if (folder) form.set('dest_folder_id', folder);
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

/** Exported for its test: the choice between clipboard, library and download. */
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
	// The app saves into the library; a browser downloads.
	if (inTheApp && session.isAdmin && (await saveToLibrary(picture, name, folder, of))) return;
	download(picture, name);
	toasts.show(`This browser can't copy pictures here, so ${name} was downloaded`);
}

/** `stage` for the player as drawn, `still` for a picture, `of` to name a saved one. */
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

type Box = Pick<DOMRectReadOnly, 'left' | 'top' | 'width' | 'height'>;

/* WHAT THE SHUTTER COVERS IS WHAT WAS PHOTOGRAPHED; null is the whole screen. */
function shutterArea(shot: Shot, source: Pictured | null, stage: HTMLElement | null): Box | null {
	if (shot === 'window') return null;
	if (shot === 'player' && stage !== null) return stage.getBoundingClientRect();
	return source === null ? null : drawnBox(source);
}

/* Inside its `object-fit: contain` bars. */
export function drawnBox(source: Pictured): Box {
	const box = source.getBoundingClientRect();
	const natural =
		source instanceof HTMLVideoElement
			? { width: source.videoWidth, height: source.videoHeight }
			: source instanceof HTMLImageElement
				? { width: source.naturalWidth, height: source.naturalHeight }
				: { width: source.width, height: source.height };
	const fit = typeof getComputedStyle === 'function' ? getComputedStyle(source).objectFit : '';
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

/*
 * The shutter, after the picture, inside whatever fills the screen (`sift-shutter` in `app.css`).
 */
function shutter(area: Box | null): void {
	if (typeof document === 'undefined') return;
	const veil = document.createElement('div');
	veil.className = 'sift-shutter';
	veil.setAttribute('aria-hidden', 'true');
	if (area !== null) {
		veil.style.inset = 'auto';
		veil.style.left = `${area.left}px`;
		veil.style.top = `${area.top}px`;
		veil.style.width = `${area.width}px`;
		veil.style.height = `${area.height}px`;
	}
	veil.addEventListener('animationend', () => veil.remove(), { once: true });
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
	/* The player without its controls (`shooting`); the window as it is. */
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

const SETTLE_LIMIT_MS = 500;

/* Let the menu leave first: one frame, then every animation with an end, up to a limit. */
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

function nextFrame(): Promise<void> {
	return new Promise((settle) => {
		const fallback = setTimeout(settle, 100);
		requestAnimationFrame(() => {
			clearTimeout(fallback);
			settle();
		});
	});
}
