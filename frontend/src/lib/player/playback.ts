/*
 * Getting a plan from the server, and attaching whatever it says to a video element.
 *
 * Kept apart from the component so that the decision logic can be tested without mounting
 * anything: the interesting parts are "which of the three paths did we take" and "was the player
 * torn down properly", and neither needs a DOM to be worth asserting.
 */

import Hls from 'hls.js';
// Its own file, so the stream is unpacked off the page's thread; a `blob:` worker the policy refuses.
import hlsWorker from 'hls.js/dist/hls.worker.js?url';
import { api } from '$lib/api/client';
import {
	capabilities,
	capabilitiesWithout,
	playsHlsNatively,
	type Capabilities
} from '$lib/player/capabilities';
import type { components } from '$lib/api/schema';

/** `unread` is not a way of playing: Sift has not read the file, so nothing attaches until it
 *  has, and the player asks again when the file moves. */
export type Route = 'direct' | 'remux' | 'transcode' | 'unread';

/** One entry in the quality menu, as the server describes it. */
export type Quality = components['schemas']['Quality'];

export type PlaybackPlan = components['schemas']['PlaybackPlan'];

/** Ask the server how this browser should play this file. */
/**
 * How long an ask is given. Without a timeout a plan queued behind six streaming connections waits
 * on the browser's connection limit rather than failing: a cell saying "finding something to
 * play" for as long as the wall is open. A plan is one small read; fifteen seconds is far past
 * anything a working server takes and short enough to be told about.
 */
const PLAN_TIMEOUT_MS = 15_000;

/**
 * Ask how this file plays here. `reported` is what the browser can do, and defaults to what it
 * says about itself; a caller that has just watched the browser fail on a codec passes the
 * report without it. See `capabilitiesWithout`.
 */
export function planFor(
	id: string,
	reported: Capabilities = capabilities()
): Promise<PlaybackPlan> {
	return api.post<PlaybackPlan>(`/assets/${id}/playback`, {
		body: reported,
		signal: AbortSignal.timeout(PLAN_TIMEOUT_MS)
	});
}

/** The same ask, without a codec the browser said it played and then did not. */
export function planForWithout(
	id: string,
	codec: string | null | undefined
): Promise<PlaybackPlan> {
	return planFor(id, capabilitiesWithout(codec));
}

/**
 * Why the element refused a file it was handed directly, in a sentence. The element says which
 * of four things went wrong and nothing more; the sentence says what somebody can do about it.
 */
export function directFailure(video: HTMLVideoElement): string {
	// The numbers rather than `MediaError`'s names: the global is the browser's, and a test
	// runtime without it would make this throw on the way to explaining a failure.
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

/** How close to the end a seek is allowed to land, in seconds. */
const END_MARGIN = 0.25;

/**
 * Where to move the playhead to, in seconds, or null to leave it at the beginning.
 *
 * Whether there is a place to go back to at all is the server's decision: it holds the length of
 * the file and the account's own minimum. This is only the arithmetic of applying that answer to a
 * particular element, and the one thing it adds is the clamp.
 *
 * The clamp is not paranoia. The two lengths come from different places: one measured when the file
 * was imported, one read out of the container by this browser, and they disagree by a frame often
 * enough. Seeking to or past the end of a video is how a player opens on a black frame with nothing
 * left to play and no obvious way back.
 */
export function startAt(resumeMs: number | null | undefined, duration: number): number | null {
	if (!resumeMs || resumeMs <= 0) return null;
	if (!Number.isFinite(duration) || duration <= 0) return null;
	const latest = Math.max(0, duration - END_MARGIN);
	const wanted = Math.min(resumeMs / 1000, latest);
	return wanted > 0 ? wanted : null;
}

/** Something attached to a video element that has to be undone when the player goes away. */
export interface Attachment {
	detach: () => void;
}

/**
 * Point a video element at what the plan says, and hand back how to undo it.
 *
 * Three cases, and the ordering matters:
 *
 * 1. **The file itself** (direct, or a remux whose repackaged copy `/stream` serves): its address
 *    goes straight into `src`. No player library is involved and none is loaded.
 * 2. **Native HLS**: Safari, which plays a playlist from `src` by itself. This is checked
 *    *before* reaching for hls.js, because on iPhone there is no alternative: Media Source
 *    Extensions do not exist there, so hls.js cannot work at all.
 * 3. **hls.js**: everything else, where a playlist has to be fed through Media Source Extensions
 *    by JavaScript.
 */
export function attach(
	video: HTMLVideoElement,
	plan: PlaybackPlan,
	onfail?: (message: string) => void
): Attachment {
	// Every route but a transcode names the file itself (`/stream`): only a playlist needs hls.js.
	if (plan.route !== 'transcode' || playsHlsNatively()) {
		/* The element's own `error` is the only word a direct failure gives, so it is heard: a
		   10-bit file on a decoder that takes Main only, a truncated MP4, a ProRes `.mov`:
		   each would otherwise be a black stage with no sentence, while a streaming failure two
		   branches down is reported. The same handler, the same sentence shape. */
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
		/*
		 * How far ahead of the picture the player is allowed to get.
		 *
		 * The server makes each piece of video on demand, so this number is also "how much work is
		 * queued for someone who might close the tab", which is why there is a ceiling at all:
		 * Sift only converts what is actually being watched.
		 *
		 * Too small, and scrubbing is where it shows. Landing somewhere new means waiting for that
		 * piece to be made, and with ten seconds of slack the player spends the rest of the clip
		 * making the next piece just barely in time, so every drag of the bar stutters.
		 *
		 * Thirty keeps the promise and pays for itself. The cost of somebody opening a clip and
		 * leaving is thirty seconds of video converted instead of ten, a couple of seconds of a
		 * graphics card's time; the gain is that the player is a piece or two ahead of the picture
		 * at all times, which is the difference between a scrub that moves and one that hangs.
		 */
		maxBufferLength: 30,
		maxMaxBufferLength: 60,
		workerPath: hlsWorker,
		// A segment that is still being made is a slow response, not a failed one.
		fragLoadingTimeOut: 30_000,
		manifestLoadingTimeOut: 20_000,
		// Cookies. The playlist and every segment are permission-scoped, so a request without the
		// session is a 401 and a blank player.
		xhrSetup(xhr) {
			xhr.withCredentials = true;
		}
	});

	/*
	 * hls.js does not recover a fatal error by itself, and without this nothing observes one: the
	 * player simply stops, with a picture that never arrives and no explanation. A segment that
	 * exceeds the load timeout is the likely case on exactly the modest hardware this targets
	 * (the server is still transcoding it), so a network error is retried once before giving up.
	 */
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
			// Without this the player keeps fetching segments for a video nobody is watching any
			// more, which on this design means it keeps the server transcoding, too.
			hls.destroy();
		}
	};
}

/** Whether hls.js can run here at all. False on iPhone, where Safari's native HLS is used. */
export function hlsIsSupported(): boolean {
	return Hls.isSupported();
}

/**
 * Ask an element to play, tolerating BOTH ways it can decline.
 *
 * A browser returns a promise and REJECTS: the autoplay policy, a source that never loaded, a
 * seek that overtook the request. jsdom has no media engine at all and THROWS synchronously, which
 * is the case `void element.play().catch(...)` does not cover: the throw happens before there is a
 * promise to attach a handler to, so it escapes as an ordinary exception.
 *
 * That is not a testing curiosity: in a component the same throw would leave the screen
 * half-rendered rather than merely not playing.
 *
 * In one place because there are four callers, and four copies of a try/catch is how three of them
 * end up without one.
 */
export function requestPlay(video: HTMLVideoElement): void {
	try {
		const started: unknown = video.play();
		if (started instanceof Promise) void started.catch(() => undefined);
	} catch {
		// Declined. Not playing is a state the caller can live with; a thrown error is not.
	}
}

/**
 * Move to a different quality without losing the place.
 *
 * Detach and attach again rather than anything cleverer, because a quality is a different ADDRESS
 * (a different playlist, or the file itself), and hls.js is built around one source at a time.
 * Tearing the old one down is also what stops the server transcoding segments of a stream nobody
 * is watching any more, which is the whole reason `detach` exists.
 *
 * The position is carried across by hand. The new stream starts at zero as far as the element is
 * concerned, and a person who changes quality four minutes into something expects to still be four
 * minutes into it: being sent back to the beginning is the failure that makes a quality menu
 * something people learn not to touch.
 *
 * Whether it was playing is carried too, for the same reason: changing quality is not a request to
 * stop.
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
		// Guarded: a stream whose length is not known yet cannot be seeked into, and seeking past
		// the end opens on a black frame with no obvious way back.
		if (Number.isFinite(at) && at > 0 && Number.isFinite(video.duration) && at < video.duration) {
			video.currentTime = at;
		}
		/* BOTH branches are said out loud, and the second one is not defensive padding.
		 *
		 * Loading a new source into an element is a fresh start as far as the element is
		 * concerned, and anything that starts playback on a load (an `autoplay` attribute, most
		 * obviously) fires again here. So "leave it alone and it will stay paused" is not true:
		 * picking a smaller size on a paused video would start it playing. Saying what the state
		 * must be is the only version of this that keeps working.
		 */
		if (wasPlaying) requestPlay(video);
		else video.pause();
	};
	video.addEventListener('loadedmetadata', restore);
	return attachment;
}
