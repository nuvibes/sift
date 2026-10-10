/* Getting a plan from the server and attaching it to a video element, testable without a DOM, and
   the account's playback preferences. */

import Hls from 'hls.js';
// Its own file, so the stream is unpacked off the page's thread; a `blob:` worker is refused.
import hlsWorker from 'hls.js/dist/hls.worker.js?url';
import { api } from '$lib/api/client';
import {
	capabilities,
	capabilitiesWithout,
	playsHlsNatively,
	type Capabilities
} from '$lib/player/capabilities';
import type { components } from '$lib/api/schema';
import { fetchSettingValues } from '$lib/settings-ui/settings';
import { dwell, LOOP_MODE_KEY } from '$lib/player/dwell.svelte';
import { loudness } from '$lib/player/loudness.svelte';
import { DEFAULT_LOOP_MODE, isLoopMode } from '$lib/player/loop-modes';

/** `unread`: nothing attaches until Sift has read the file. */
export type Route = 'direct' | 'remux' | 'transcode' | 'unread';

export type Quality = components['schemas']['Quality'];

export type PlaybackPlan = components['schemas']['PlaybackPlan'];

/** A plan queued behind six streaming connections would wait forever without a timeout. */
const PLAN_TIMEOUT_MS = 15_000;

/** `reported` defaults to what the browser says; see `capabilitiesWithout`. */
export function planFor(
	id: string,
	reported: Capabilities = capabilities()
): Promise<PlaybackPlan> {
	return api.post<PlaybackPlan>(`/assets/${id}/playback`, {
		body: reported,
		signal: AbortSignal.timeout(PLAN_TIMEOUT_MS)
	});
}

export function planForWithout(
	id: string,
	codec: string | null | undefined
): Promise<PlaybackPlan> {
	return planFor(id, capabilitiesWithout(codec));
}

/** The registered keys, spelled once. What happens at the end is the dwell's, beside it. */
export const VOLUME_KEY = 'playback.volume';
export const MUTED_KEY = 'playback.muted';

/**
 * The preferences handed to the places that hold them: the loop to the dwell, the level to the
 * loudness. Whether it is muted is returned, or null where they could not be read.
 */
/* WHY NOT FOLLOWED: the player asks again on `settingChanges` (`Player.svelte`), and it is a screen. */
export async function readPlaybackPreferences(): Promise<boolean | null> {
	try {
		const values = await fetchSettingValues();
		const mode = values.get(LOOP_MODE_KEY);
		dwell.repeats(isLoopMode(mode) ? mode : DEFAULT_LOOP_MODE);
		loudness.heard(values.get(VOLUME_KEY));
		/* Muted, remembered apart from how loud, so the switch never destroys the level. */
		const off = values.get(MUTED_KEY);
		return off === true || off === 'true' || off === 1 || off === '1';
	} catch {
		// These are niceties. Failing to read them must not stop a video playing.
		return null;
	}
}

/** Why the element refused a file, as something somebody can act on. */
export function directFailure(video: HTMLVideoElement): string {
	// The numbers, not `MediaError`'s names, which a test runtime may lack.
	const code = video.error?.code;
	if (code === MEDIA_ERR_SRC_NOT_SUPPORTED || code === MEDIA_ERR_DECODE) {
		return "Your browser said it could play this file and then couldn't.";
	}
	if (code === MEDIA_ERR_NETWORK)
		return 'The connection to Sift dropped while this file was loading.';
	return 'This file stopped before it could play.';
}

const MEDIA_ERR_NETWORK = 2;
const MEDIA_ERR_DECODE = 3;
const MEDIA_ERR_SRC_NOT_SUPPORTED = 4;

const END_MARGIN = 0.25;

/**
 * Where to put the playhead, or null. Clamped: the imported length and the browser's differ by a
 * frame often enough, and seeking past the end opens on a black frame.
 */
export function startAt(resumeMs: number | null | undefined, duration: number): number | null {
	if (!resumeMs || resumeMs <= 0) return null;
	if (!Number.isFinite(duration) || duration <= 0) return null;
	const latest = Math.max(0, duration - END_MARGIN);
	const wanted = Math.min(resumeMs / 1000, latest);
	return wanted > 0 ? wanted : null;
}

export interface Attachment {
	detach: () => void;
}

/**
 * Point a video element at the plan: the file itself into `src`; else native HLS (Safari, and the
 * only way on an iPhone, which has no Media Source Extensions); else hls.js.
 */
export function attach(
	video: HTMLVideoElement,
	plan: PlaybackPlan,
	onfail?: (message: string) => void
): Attachment {
	if (plan.route !== 'transcode' || playsHlsNatively()) {
		/*
		 * The element's own `error` is heard, so a direct failure gets a sentence as a stream's
		 * does.
		 */
		const broke = () => onfail?.(directFailure(video));
		video.addEventListener('error', broke);
		video.src = plan.url;
		return {
			detach() {
				video.removeEventListener('error', broke);
				video.removeAttribute('src');
				video.load();
			}
		};
	}

	const hls = new Hls({
		/* Thirty seconds ahead: segments are made on demand, and ten leaves every scrub waiting. */
		maxBufferLength: 30,
		maxMaxBufferLength: 60,
		workerPath: hlsWorker,
		// A segment still being made is slow, not failed.
		fragLoadingTimeOut: 30_000,
		manifestLoadingTimeOut: 20_000,
		// The playlist and segments are permission-scoped: without the cookie, a 401.
		xhrSetup(xhr) {
			xhr.withCredentials = true;
		}
	});

	/* hls.js does not recover a fatal error itself; a network error is retried once. */
	hls.on(Hls.Events.ERROR, (_event, data) => {
		if (!data.fatal) return;
		if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
			hls.startLoad();
			return;
		}
		if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
			hls.recoverMediaError();
			return;
		}
		hls.destroy();
		onfail?.("This video stopped playing and couldn't be recovered.");
	});

	hls.loadSource(plan.url);
	hls.attachMedia(video);

	return {
		detach() {
			// Or it keeps fetching, and the server keeps transcoding, for nobody.
			hls.destroy();
		}
	};
}

export function afterTheFrame(video: HTMLVideoElement | null, go: () => void): void {
	if (video && typeof video.requestVideoFrameCallback === 'function')
		video.requestVideoFrameCallback(() => go());
	else go();
}

export function hlsIsSupported(): boolean {
	return Hls.isSupported();
}

/**
 * Play, tolerating both refusals: a browser REJECTS, jsdom THROWS synchronously, before there is a
 * promise to catch. One place, for four callers.
 */
export function requestPlay(video: HTMLVideoElement): void {
	try {
		const started: unknown = video.play();
		if (started instanceof Promise) void started.catch(() => undefined);
	} catch {
		// Declined: not playing is a state the caller can live with.
	}
}

/**
 * A different quality is a different address, so detach and attach again, carrying the position and
 * whether it was playing by hand.
 */
export function changeQuality(
	video: HTMLVideoElement,
	current: Attachment,
	plan: PlaybackPlan,
	quality: Quality,
	onfail?: (message: string) => void
): Attachment {
	const at = video.currentTime;
	const wasPlaying = !video.paused;
	current.detach();

	const attachment = attach(video, { ...plan, url: quality.url }, onfail);
	const restore = () => {
		video.removeEventListener('loadedmetadata', restore);
		// A length not known yet cannot be seeked into.
		if (Number.isFinite(at) && at > 0 && Number.isFinite(video.duration) && at < video.duration) {
			video.currentTime = at;
		}
		/*
		 * Both branches are said: a new source re-fires `autoplay`, so a paused video would start.
		 */
		if (wasPlaying) requestPlay(video);
		else video.pause();
	};
	video.addEventListener('loadedmetadata', restore);
	return attachment;
}
