<script lang="ts">
	import { Button, Separator } from '$lib/components/common';
	/* The player: it asks the server how this browser should play this file, attaches what the
	   answer says, and draws Sift's own controls. Play through is the default, since a library of
	   short clips that stops at every end is a press every few seconds; and a file Sift cannot
	   convert as fast as it plays says so before it starts. */
	import { onMount, untrack } from 'svelte';
	import { api, ApiError } from '$lib/api/client';
	import { fetchSettingValues, saveSettings } from '$lib/settings-ui/settings';
	import { arrivals, settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { run } from '$lib/player/run.svelte';
	import { abLoop } from '$lib/player/loop.svelte';
	import { clipTheStretch } from '$lib/edit/edit.svelte';
	import { keepTheLast } from '$lib/player/snapshot';
	import {
		DEFAULT_LOOP_MODE,
		isLoopMode,
		nextLoopMode,
		type LoopMode
	} from '$lib/player/loop-modes';
	import { ACTS, keyOf } from '$lib/player/acts';
	import { muteEcho, type Echo } from '$lib/theater/echoes';
	import { taken } from '$lib/player/keys';
	// `seekVideoTo`, because `seekTo` is already this player's prop for where to pick a file up.
	import { dropWaitingSeek, seekTo as seekVideoTo } from '$lib/player/seek';
	import { dwell, LOOP_MODE_KEY } from '$lib/player/dwell.svelte';
	import {
		attach,
		changeQuality,
		planFor,
		planForWithout,
		requestPlay,
		startAt,
		type Attachment,
		type PlaybackPlan,
		type Quality
	} from '$lib/player/playback';
	import FullscreenButton from './FullscreenButton.svelte';
	import { keepPlayingAcrossExit } from './fullscreen';
	import { handPlace } from './motion';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import StatsPanel from './StatsPanel.svelte';
	import PlayerBar from './PlayerBar.svelte';
	import PlayerDrawer from './PlayerDrawer.svelte';
	import PlayerState from './PlayerState.svelte';
	import PlayerEchoes from './PlayerEchoes.svelte';
	import { PicturePress } from './picture-press.svelte';
	import { PlayerKeys } from './player-keys';
	import { WatchReport } from './watch-report';
	import { getStage } from './stage.svelte';
	import { usable, type SpriteSheet } from '$lib/player/trickplay';
	import type { FileFacts } from '$lib/player/facts';
	import { handover, mini } from '$lib/player/mini.svelte';
	import { beforeNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { takeDismissal, toggleShuffle } from '$lib/player/asset-view';
	import { popoutLeavesToMini } from '$lib/shell/interface-state.svelte';
	/* Aliased: this file's `watching` is the clip being watched; the store is whether any player runs. */
	import { watching as playback } from '$lib/player/watching.svelte';
	import type { SittingPlace } from '$lib/player/sitting.svelte';
	import { loudness } from '$lib/player/loudness.svelte';
	import { spriteUrl } from '$lib/entity/art';
	import { pressed } from '$lib/shell/shortcuts';
	import type { Offerable } from '$lib/remote/offer.svelte';
	import type { components } from '$lib/api/schema';

	/** The registered keys, spelled once. What happens at the end is the dwell's, beside it. */
	const VOLUME_KEY = 'playback.volume';
	const MUTED_KEY = 'playback.muted';

	interface Props {
		id: string;
		/** Whether the picture can be dragged out of the window, where that means something. */
		draggable?: boolean;
		/** What to show behind the video until the first frame arrives. */
		poster?: string;
		/** How this file's scrub strip is cut up, from the detail the screen already asked for. */
		sprite?: SpriteSheet | null;
		/** What to put on the end of the strip's address, so a browser may keep its copy. */
		art?: string | null;
		/** The small panel rather than the full-size view: the bar is not drawn at all. */
		compact?: boolean;
		/** The file before and after this one in the list it was opened from: the bar's outer pair. */
		onprevious?: () => void;
		onnext?: () => void;
		/** Where the playhead is, in seconds, whenever it moves. For whoever has to hand it on. */
		onprogress?: (seconds: number) => void;
		/** The plan, as it arrives and when it is dropped, for the host's mark (`noticeWords`). */
		onplan?: (plan: PlaybackPlan | null) => void;
		/** What this file is to the account, as the server last said: a starting point. */
		favorite?: boolean;
		rating?: number | null;
		/** Open another file, one this list may not hold; `runs` says whether it plays. Absent, the
		 *  random control is dimmed: there is nowhere for it to send anybody. */
		onopen?: (id: string, runs: boolean) => void;
		/** The file's own facts, for the panel somebody opens. */
		file?: FileFacts | null;
		/** Where a clip goes when it reaches its end and Play through is on: the next thing that
		 *  PLAYS, where Next is the next one whatever it is. */
		onplayedthrough?: () => void;
		/** A moment to be taken to, in milliseconds: a NEW object each time, so pressing the same
		 *  face twice goes there again. */
		seekTo?: { ms: number } | null;
		/** Whether it is playing, reported as it changes, for a frame drawing its own play control. */
		onplaystate?: (playing: boolean) => void;
		/** How long the clip is, once the element knows. For a frame drawing its own progress line. */
		ondurationknown?: (seconds: number) => void;
		/** Open held: a clip handed between the two players while paused stays paused. */
		startPaused?: boolean;
		/** Where this player's sittings happen, since it cannot tell which frame it is in; read
		 *  when a sitting begins. See `SittingPlace`. */
		place?: SittingPlace;
	}

	let {
		id,
		draggable = false,
		poster,
		sprite = null,
		art = null,
		compact = false,
		onprevious,
		onnext,
		onprogress,
		onplan,
		onplaystate,
		ondurationknown,
		favorite = false,
		rating = null,
		onopen,
		file = null,
		onplayedthrough,
		seekTo = null,
		startPaused = false,
		place
	}: Props = $props();

	let video = $state<HTMLVideoElement | null>(null);
	/* The frame this is drawn in, which owns fullscreen and the idle fade; null when drawn alone. */
	const frame = getStage();

	let plan = $state<PlaybackPlan | null>(null);
	let failed = $state(false);
	let overridden = $state(false);
	let problem = $state<string | null>(null);
	/** The ask itself failed, not the file, which is why it is not `failed`. */
	let unasked = $state(false);
	/** Whether the plan has already been asked for again without the codec that failed. */
	let replanned = $state(false);
	/* What happens at the end, read from the one place every player reads it (`dwell.mode`), so a
	   picture's bar and the corner panel change the same answer this follows. */
	const loop = $derived(dwell.mode);
	let playing = $state(false);
	let muted = $state(false);
	let position = $state(0);
	let duration = $state(0);

	/* How loud is `$lib/player/loudness`: one level, read live by every picture, kept on the account. */

	let attachment: Attachment | null = null;

	/* Which size is being watched, once chosen; null is the server's own. Not read by the attach
	   effect: a size change mid-clip must not start the clip again. */
	let chosen = $state<Quality | null>(null);
	let qualityOpen = $state(false);
	/** The Clip menu (the last few seconds), opened from the drawer. Bound so it shuts with the bar. */
	let keepOpen = $state(false);

	/* How many times each key whose control is in the shut drawer has been pressed: the badges. */
	let repeatPresses = $state(0);
	let shufflePresses = $state(0);
	let loopPresses = $state(0);
	/* And every other key's answer, in the corner, in the words a Theater cell uses. */
	let keyed = $state<Echo>(muteEcho(false));
	let keyedPresses = $state(0);

	function echo(what: Echo): void {
		keyed = what;
		keyedPresses += 1;
	}
	/* Which file the numbers are about: the player is not rebuilt for the next clip (that would end
	   fullscreen), so `id` moves under it while this is still the clip that was watched. */
	// svelte-ignore state_referenced_locally: the INITIAL id is exactly what is wanted here. What
	// this holds is the clip being watched now, and the effect below moves it on when `id` changes.
	let watching = $state(id);

	/* Arriving to fill the screen (F on the corner panel), asked of the stage once it is drawn. */
	$effect(() => {
		if (compact || !frame?.element) return;
		untrack(() => {
			if (handover.takeFill(watching) && !document.fullscreenElement) frame.toggleFullscreen();
		});
	});
	/* Where this sitting is happening, fixed when it begins. */
	// svelte-ignore state_referenced_locally: the place at the moment the FIRST sitting begins.
	let sittingPlace = place;

	/* How many slices a file is cut into for the replay curve. MUST equal `HEAT_BUCKETS` in
	   `sift.kernel.content.user_state`, which reads these as indexes into a fixed-width row;
	   `tests/gates/test_replay_buckets_agree.py` holds the two together. */
	const HEAT_BUCKETS = 100;

	/* What is told to the server about this sitting. See `WatchReport`. */
	const watch = new WatchReport(
		{
			position: () => position,
			duration: () => duration,
			rate: () => video?.playbackRate ?? 1,
			playhead: () => (video ? video.currentTime : null),
			viewAt: () => plan?.view_at_ms ?? 0,
			post: (piece, final) =>
				api.post(`/assets/${watching}/view`, {
					body: { ...sittingPlace, ...piece },
					keepalive: final
				})
		},
		HEAT_BUCKETS
	);
	/* Whether the plan's starting point has been applied: once, on the first metadata. */
	let resumed = false;

	/** The plan is only acted on once it says the file will actually play, or once overruled. A
	    file Sift has not read has nothing to act on, whatever anybody presses. */
	const ready = $derived(
		plan !== null && plan.route !== 'unread' && (plan.streamable || overridden)
	);

	/* The element outlives a change of file, held while the next plan is asked for: a browser lets
	   an element play by itself once pressed, and refuses a new one's `play()` outside a press. */
	let heldAcross = $state(false);
	const drawn = $derived(ready || heldAcross);

	/* This account's replay curve for the clip, or null where there is nothing worth drawing; its
	   own request, so a history query never stands in front of the first frame. */
	let replays = $state<readonly number[] | null>(null);

	async function loadReplays(clip: string) {
		replays = null;
		try {
			const curve = await api.get<components['schemas']['ReplayCurve']>(`/assets/${clip}/replays`);
			// Only if this is still the clip being watched: a slower answer can land over a newer one.
			if (clip === watching) replays = curve.worth_drawing ? curve.heat : null;
		} catch {
			// A curve that could not be fetched is simply not drawn.
		}
	}

	/* Pointed at a different clip without being rebuilt, since replacing the element is leaving
	   fullscreen: everything about the last clip is finished off by hand. */
	$effect(() => {
		const next = id;
		untrack(() => {
			if (next === watching) return;
			// Reported before anything is reset, while `watching` still names the clip it describes.
			void watch.report();
			teardown();
			// Off the loop's audience for the old clip and onto the new one's, before `watching` moves.
			abLoop.unwatch(watching);
			abLoop.watch(next);
			watching = next;
			// A new file is a new sitting, minted here so the first piece carries the right name.
			sittingPlace = place;
			watch.restart();
			resumed = false;
			position = 0;
			duration = 0;
			problem = null;
			failed = false;
			overridden = false;
			// Before the plan goes, while the element is still on the stage. See `heldAcross`.
			heldAcross = video !== null;
			plan = null;
			onplan?.(null);
			void load();
			void loadReplays(next);
		});
	});

	/* And again whenever a playback preference moves, wherever it was changed: the corner panel
	 * outlives every screen. The player's own writes come back as what it wrote. */
	whenChanged(settingChanges, () => void readPreferences());

	/* A file Sift has not read is asked about again when files move: the read is what makes it
	   playable, and the read is announced the way an arrival is. Only then: a player on a file
	   that is playing has no reason to re-plan because a scan is bringing files in. */
	whenChanged(arrivals, () => {
		if (plan?.route === 'unread') void load();
	});

	/* A player torn down while it is playing never fires `pause`, so the count of running players
	   would never come back down, and the idle timers would believe somebody was watching for the
	   rest of the session. Closing the panel mid-play is the ordinary way this happens. */
	onMount(() => () => {
		if (playing) playback.stopped();
	});

	onMount(() => {
		// Counted in first, so a clip handed to the corner is never watched by nobody for a moment.
		abLoop.watch(watching);
		watch.start();
		void load();
		void loadReplays(watching);
		// `pagehide` covers closing the tab, navigating away and reloading, which unmount nothing.
		const onLeave = () => void watch.report();
		window.addEventListener('pagehide', onLeave);
		return () => {
			window.removeEventListener('pagehide', onLeave);
			// Flushed: a level set just before closing is the one somebody expects to have stuck.
			loudness.settle();
			teardown();
			watch.stop();
			void watch.report();
			// One fewer player on this clip; the last one out forgets the loop a turn later.
			abLoop.unwatch(watching);
		};
	});

	async function load() {
		const wanted = watching;
		unasked = false;
		replanned = false;
		try {
			const answer = await planFor(wanted);
			// A slower answer for a clip already moved on from must not attach itself to this one.
			if (wanted !== watching) return;
			plan = answer;
			heldAcross = false;
			onplan?.(answer);
			// A new file has its own sizes, and the one chosen for the last file means nothing here.
			chosen = null;
			qualityOpen = false;
			await readPreferences();
		} catch (error) {
			if (wanted !== watching) return;
			heldAcross = false;
			// A 404 is "gone" or "not yours"; anything else is not about the file, and says so.
			if (error instanceof ApiError && error.status === 404) failed = true;
			else unasked = true;
		}
	}

	/* The browser said it could play this directly and then could not (`canPlayType` guesses from a
	   string). Asked again once without the codec that failed; a second failure is reported. */
	async function replanWithout(message: string) {
		const wanted = watching;
		replanned = true;
		try {
			const answer = await planForWithout(wanted, file?.vcodec);
			if (wanted !== watching) return;
			if (answer.route === 'direct') {
				problem = message;
				return;
			}
			problem = null;
			plan = answer;
			onplan?.(answer);
		} catch {
			if (wanted === watching) problem = message;
		}
	}

	/* `sections` is a list, not a map; the flattening lives in `$lib/settings-ui/settings`. */
	async function readPreferences(): Promise<void> {
		try {
			const values = await fetchSettingValues();

			const mode = values.get(LOOP_MODE_KEY);
			dwell.repeats(isLoopMode(mode) ? mode : DEFAULT_LOOP_MODE);

			// One read for both, the level handed to the one place that holds it.
			loudness.heard(values.get(VOLUME_KEY));

			/* Muted, remembered apart from how loud, so the switch never destroys the level. */
			const off = values.get(MUTED_KEY);
			muted = off === true || off === 'true' || off === 1 || off === '1';
		} catch {
			// These are niceties. Failing to read them must not stop a video playing.
		}
	}

	/* The volume onto the element, whichever of the two arrives second. */
	$effect(() => {
		const element = video;
		const level = loudness.level;
		if (element) element.volume = level / 100;
	});

	/* Out of an iPhone's own full screen player still playing: its swipe down hands the video back
	   paused, and nobody asked for that. See `keepPlayingAcrossExit`. */
	$effect(() => {
		const element = video;
		if (!element) return;
		return keepPlayingAcrossExit(element, () => requestPlay(element));
	});

	/* And muted, for the same race. */
	$effect(() => {
		const element = video;
		const off = muted;
		if (element) element.muted = off;
	});

	/* Written where every player reads it, and saved from there (`dwell.choose`). */
	function setLoop(mode: LoopMode) {
		void dwell.choose(mode);
	}

	/* Attach once both the element and the plan exist, in either order. */
	$effect(() => {
		const element = video;
		const current = plan;
		if (!element || !current || !ready) return;

		attachment = attach(element, current, (message) => {
			if (current.route === 'direct' && !replanned) void replanWithout(message);
			else problem = message;
		});
		return () => {
			attachment?.detach();
			attachment = null;
		};
	});

	/** Watch this at a different size, from where it is now. */
	function pickQuality(quality: Quality) {
		const element = video;
		const current = plan;
		if (!element || !current || !attachment) return;
		chosen = quality;
		attachment = changeQuality(element, attachment, current, quality, (message) => {
			problem = message;
		});
	}

	function teardown() {
		attachment?.detach();
		attachment = null;
	}

	function onPlay() {
		if (!playing) playback.started();
		playing = true;
		watch.begin();
		onplaystate?.(true);
	}

	function onPause() {
		if (playing) playback.stopped();
		playing = false;
		watch.accumulate();
		onplaystate?.(false);
	}

	function onTimeUpdate() {
		if (!video) return;
		position = video.currentTime;
		onprogress?.(position);
		// Back to the start of the loop, on the tick that already runs.
		if (looping && pointA !== null && pointB !== null && position >= pointB) {
			video.currentTime = pointA;
			position = pointA;
		}
		if (playing) watch.tick();
	}

	/* The element knows how long the file is, the first moment its playhead can be moved: to where
	   the plan says, clamped to the element's own length, which can differ by a frame. */
	function onMetadata() {
		duration = video?.duration ?? 0;
		ondurationknown?.(duration);
		if (resumed) return;
		resumed = true;
		/* Started here rather than by `autoplay`, which plays on every source load, and the quality
		   menu loads a new source into this element: a paused clip would start on a size change. */
		if (!(startPaused || handover.take(watching)) && video) requestPlay(video);
		const start = startAt(plan?.resume_ms, duration);
		if (video && start !== null) video.currentTime = start;
	}

	/* Act on a seek asked for from outside, once the length is known. */
	$effect(() => {
		const wanted = seekTo;
		if (!wanted || !video || duration <= 0) return;
		video.currentTime = Math.min(Math.max(wanted.ms / 1000, 0), duration);
	});

	function onEnded() {
		watch.accumulate();
		/* Before anything rewinds: after a repeat nothing says this file was watched through. */
		watch.ended();
		if (loop === 'loop_one') {
			/* The pass is reported now, before the playhead moves; not awaited, since `send` builds its
			   body before it suspends. */
			void watch.closePass();
			// Repeat. Rewinding rather than reloading keeps the segments already fetched.
			if (video) {
				video.currentTime = 0;
				requestPlay(video);
			}
			return;
		}
		// The auto-advance route, which skips what cannot play; under Shuffle, Stop at the end moves
		// on too, and the run stops where the list ends (`run.wraps`).
		if (run.movesOnAfter(loop)) onplayedthrough?.();
	}

	function toggle() {
		if (!video) return;
		if (video.paused) requestPlay(video);
		else video.pause();
	}

	/* Pressing and magnifying the picture. See `PicturePress`. */
	const press = new PicturePress({
		video: () => video,
		watching: () => watching,
		frame: () => frame,
		toggle
	});

	/* Play or pause from outside, for a frame with its own control: the same press as the bar's. */
	export function togglePlayback(): void {
		toggle();
	}

	/* Jump, from a frame that draws its own controls rather than using the bar. Same clamping. */
	export function jumpBy(seconds: number): void {
		seekBy(seconds);
	}

	/* The sound, from a frame drawing its own controls: the same press the bar's mute makes. */
	export function toggleSound(): void {
		toggleMute();
	}

	export function isMuted(): boolean {
		return muted;
	}

	/** Whether it is paused right now, for a frame handing this clip on somewhere else. */
	export function isPaused(): boolean {
		return video?.paused ?? true;
	}

	/* Go to a moment, from a frame drawing its own timeline. */
	export function goTo(seconds: number): void {
		// Through `seekVideoTo`, so a drag on the scrub line lets each seek finish and paint; see there.
		if (video) seekVideoTo(video, Math.min(Math.max(seconds, 0), duration));
	}

	/* The shape of the picture in its own pixels, or null until the header is read: a guess is a
	   panel resized to the wrong shape. */
	export function pictureSize(): { width: number; height: number } | null {
		if (!video || !video.videoWidth || !video.videoHeight) return null;
		return { width: video.videoWidth, height: video.videoHeight };
	}

	/* Jump, clamped to the element's own length: seeking to the very end lands on a black frame. */
	function seekBy(seconds: number) {
		if (!video) return;
		const length = Number.isFinite(video.duration) ? video.duration : duration;
		if (!(length > 0)) return;
		dropWaitingSeek(video);
		video.currentTime = Math.min(Math.max(video.currentTime + seconds, 0), length);
		// The controls come back: the person doing this wants to see where it landed.
		frame?.wake();
	}

	/* The player's verbs, for the keyboard and the phone. See `PlayerKeys`. */
	const keys = new PlayerKeys({
		video: () => video,
		frame: () => frame,
		watching: () => watching,
		loop: () => loop,
		muted: () => muted,
		plan: () => plan,
		choosable: () => choosable,
		savable: () => savable,
		canOpen: () => Boolean(onopen),
		previous: () => onprevious,
		next: () => onnext,
		toggle,
		seekBy,
		toggleMute,
		resetZoom: () => press.view.reset(),
		setLoop,
		saveLoop: () => void saveLoop(),
		clipTheLast: (seconds) => void clipTheLast(seconds),
		randomize: () => void randomize(),
		pickQuality,
		markPoint,
		toMini,
		audioOffered: () => !compact && video !== null && !phoneWidth.yes,
		echo,
		pressed: (which) => {
			if (which === 'repeat') repeatPresses += 1;
			else if (which === 'shuffle') shufflePresses += 1;
			else loopPresses += 1;
		}
	});

	/* What this player hands the viewer it is drawn in, which offers it to the phone: the same table
	   the keys answer from, and where it stands. */
	export function remote(): Offerable {
		return {
			actions: keys.actions,
			state: () => ({
				playing: video ? !video.paused : false,
				position: video?.currentTime ?? 0,
				length: duration > 0 ? duration : null,
				file: id,
				volume: loudness.level,
				muted: video?.muted ?? muted,
				// The drawer, as it is drawn: the phone lights the same presses the desk does.
				repeat: loop,
				shuffle: run.shuffle,
				loop_marks: pointA === null ? 0 : pointB === null ? 1 : 2,
				qualities: choosable ? (plan?.qualities ?? []).map((rung) => rung.label) : [],
				quality: choosable
					? (plan?.qualities ?? []).findIndex((rung) => rung.url === (chosen?.url ?? plan?.url))
					: null
			})
		};
	}

	/* The keys. The arrows seek, so stepping files is Shift with them. The panel never takes the
	   keyboard: with two players mounted, a bare Space would pause both. */
	function onKeydown(event: KeyboardEvent) {
		if (compact) return;
		// Somebody has already acted on this press. See `$lib/player/keys`.
		if (taken(event)) return;
		pressed(event, keys.actions);
	}

	/* M let go, which is where the mute happens. See `PlayerKeys.keyup`. */
	function onKeyup(event: KeyboardEvent) {
		if (compact) return;
		keys.keyup(event);
	}

	/* Leaving the popout mid-clip by a chip: where `popout.leave_to_mini` says, the corner panel is
	   filled once the navigation lands. A dismissal (`takeDismissal`) just leaves. */
	beforeNavigate((navigation) => {
		if (compact || page.state.asset !== watching) return;
		// Consumed whatever happens next, so a close left standing cannot pass for the next one.
		if (takeDismissal()) return;
		if (!video || video.paused || !popoutLeavesToMini()) return;
		const held = cornerClip(video, false);
		// A cancelled navigation rejects `complete`, and the panel is then left alone.
		void navigation.complete.then(
			() => mini.open(held, inTheWindow()),
			() => undefined
		);
	});

	/* Carry on watching in the corner, asked for: the shell reads the handover and does the leaving. */
	function toMini(bar = false) {
		if (compact || !video) return;
		/* Silenced rather than stopped: the panel starts before this view goes, and a muted second
		   voice keeps both pictures moving through the handover. */
		video.muted = true;
		const held = video.paused;
		// Where the picture stands, for the corner panel to grow out of (`stageTransition`).
		handPlace(frame?.element?.getBoundingClientRect() ?? null);
		mini.open(cornerClip(video, held), inTheWindow(), { handover: true, bar });
	}

	/** This clip as the corner panel holds it, from where the picture stands. */
	function cornerClip(element: HTMLVideoElement, paused: boolean) {
		const from = sittingPlace;
		return {
			id: watching,
			mediaType: 'video',
			art,
			sprite,
			poster,
			at: element.currentTime,
			paused,
			from
		};
	}

	function inTheWindow() {
		return { width: window.innerWidth, height: window.innerHeight };
	}

	/* The strip, if there is one worth reading. */
	const strip = $derived(usable(sprite) ? sprite : null);

	/* The A-B loop: two points, held in a module so a handover to the corner keeps them, and
	   enforced only on the clip that owns them. */
	const mine = $derived(abLoop.owns(watching));
	const pointA = $derived(mine ? abLoop.a : null);
	const pointB = $derived(mine ? abLoop.b : null);
	const looping = $derived(mine && abLoop.running);
	// What the button will do to THIS clip.
	const abLabel = $derived(mine ? abLoop.nextAction : 'Set the loop start');

	function markPoint() {
		if (!video) return;
		abLoop.mark(watching, video.currentTime);
	}

	/* Keep the marked stretch as a Loop: a real clip in the library, cut by a background job that
	   marks the file it produces (`clipTheStretch`). The marks on the timeline stay. */
	let saving = $state(false);

	const savable = $derived(mine && abLoop.running && !saving);

	const choosable = $derived((plan?.qualities?.length ?? 0) > 1);

	async function saveLoop() {
		if (!savable || pointA === null || pointB === null) return;
		saving = true;
		const startMs = Math.round(pointA * 1000);
		const endMs = Math.round(pointB * 1000);
		const cut = await clipTheStretch(watching, startMs, endMs - startMs, { asLoop: true });
		saving = false;
		toasts.show(
			cut.made ? 'Saving it as a loop \u2014 exactly the stretch you marked' : cut.because,
			{ tone: cut.made ? 'success' : 'error' }
		);
	}

	/* The last few seconds, kept as a new file beside this one: the same cut Save as Loop makes,
	   of the stretch that ends at the playhead. */
	async function clipTheLast(seconds: number) {
		if (!video) return;
		await keepTheLast(watching, video.currentTime, seconds);
	}

	/* The Screenshot menu, bound so it shuts with the bar as the other two menus do. */
	let shotOpen = $state(false);

	/* Something else out of the whole library, chosen by the server, avoiding the file open. */
	let finding = $state(false);

	async function randomize() {
		if (finding) return;
		finding = true;
		try {
			const other = await api.get<components['schemas']['AssetSummary']>('/assets/random', {
				query: { avoiding: watching }
			});
			onopen?.(other.id, other.media_type === 'video' || other.media_type === 'gif');
		} catch {
			// The library is empty, or everything in it is behind the vault: said, not silently ignored.
			toasts.show("There's nothing else to show");
		} finally {
			finding = false;
		}
	}

	/** Whether the panel of facts is open. Opened deliberately, and never on screen by default. */
	let statsOpen = $state(false);

	/* The pointer is on the bar, so nothing is idle; the stage owns the waiting. */
	function holdBar(event?: PointerEvent) {
		frame?.hold(event);
	}

	function releaseBar() {
		frame?.wake();
	}

	/* The menus go when the bar goes (`frame.showing`), or one would hang over the video anchored to
	   a control no longer drawn. */
	$effect(() => {
		if (frame && !frame.showing) {
			qualityOpen = false;
			keepOpen = false;
			shotOpen = false;
		}
	});

	function toggleMute() {
		if (!video) return;
		video.muted = !video.muted;
		muted = video.muted;
		// Written down, like the volume; one press, so not debounced.
		void saveSettings({ [MUTED_KEY]: muted }).catch(() => {
			// A preference that could not be saved must not interrupt what is playing.
		});
	}
</script>

<svelte:window onkeydown={onKeydown} onkeyup={onKeyup} />

<!-- The video and the bar, inside a MediaStage, which owns the frame, fullscreen and the fading.
     The states that are not the video sit over the picture, so the fullscreen element stays. -->
<PlayerState
	{failed}
	{unasked}
	{plan}
	{overridden}
	onretry={() => void load()}
	onoverride={() => (overridden = true)}
/>

{#if drawn}
	<!-- svelte-ignore a11y_media_has_caption: captions are not drawn yet; the file has none -->
	<!-- Not draggable while magnified: a native drag and a pan are one gesture, and the system's wins. -->
	<video
		bind:this={video}
		{poster}
		draggable={draggable && !press.view.magnified}
		playsinline
		style:cursor={press.view.cursor}
		style:scale={press.view.scale}
		style:translate={press.view.offset}
		onplay={onPlay}
		onpause={onPause}
		ontimeupdate={onTimeUpdate}
		onended={onEnded}
		onloadedmetadata={onMetadata}
		onvolumechange={() => (muted = video?.muted ?? false)}
		onwheel={(event) => press.wheel(event)}
		onpointerdown={(event) => press.grab(event)}
		onpointermove={(event) => press.drag(event)}
		onpointerup={(event) => press.view.release(event)}
		onpointercancel={(event) => press.view.release(event)}
		onclick={() => press.press()}
		ondblclick={() => press.pressTwice()}
	></video>

	<!-- The bar, shared with every Theater cell; only the drawer's contents differ. Not drawn in the
	     small panel at all, which draws its own. The outer pair's keys are Ctrl and an arrow. -->
	{#if !compact}
		<PlayerBar
			{position}
			{duration}
			sheet={strip}
			sheetUrl={spriteUrl({ id: watching, art })}
			{replays}
			{pointA}
			{pointB}
			onseek={goTo}
			onmark={(which, seconds) => abLoop.moveTo(which, Math.min(Math.max(seconds, 0), duration))}
			{playing}
			onplay={toggle}
			onback={onprevious}
			onforward={onnext}
			backLabel={ACTS.previous}
			forwardLabel={ACTS.next}
			shuffle={{ on: run.shuffle, onpress: () => toggleShuffle(watching) }}
			repeat={{ mode: loop, onpress: () => setLoop(nextLoopMode(loop)) }}
			keyboard="player"
			{muted}
			volume={loudness.level}
			onmute={toggleMute}
			onvolume={(level) => loudness.set(level)}
			tray={clipControls}
			{trailing}
			onhold={holdBar}
			onrelease={releaseBar}
		/>
	{/if}

	<!-- Where you are once the controls have gone: a hairline, placed and faded by the stage
	     (`player-progress`), hidden from a screen reader since the clock already says it. -->
	<div class="player-progress" aria-hidden="true">
		<span class="elapsed" style:--level="{duration > 0 ? (position / duration) * 100 : 0}%"></span>
	</div>

	{#if statsOpen}
		<!-- The facts, in the corner of the picture: `StatsPanel`, which a Theater cell draws too. -->
		<StatsPanel {file} {plan} {position} {duration} media={video} />
	{/if}
{/if}

{#if problem}
	<p class="reason">{problem}</p>
{/if}

<!-- The drawer's contents, rendered inside the bar. See `PlayerDrawer`. -->
{#snippet clipControls()}
	<PlayerDrawer
		{watching}
		{id}
		{video}
		{frame}
		{plan}
		{chosen}
		{choosable}
		canOpen={Boolean(onopen)}
		{abLabel}
		{looping}
		{savable}
		{saving}
		bind:keepOpen
		bind:shotOpen
		bind:qualityOpen
		bind:statsOpen
		onquality={pickQuality}
		onrandom={() => void randomize()}
		onmark={markPoint}
		onsave={() => void saveLoop()}
		onhold={holdBar}
		onrelease={releaseBar}
	/>
{/snippet}

<!-- What the last key did, in the corner; not in the small panel, which takes no keys. -->
{#if !compact}
	<PlayerEchoes
		{watching}
		{loop}
		{abLabel}
		{looping}
		{repeatPresses}
		{shufflePresses}
		{loopPresses}
		{keyed}
		{keyedPresses}
	/>
{/if}

<!-- The end of the row, outside the drawer. -->
{#snippet trailing()}
	<!-- The two smaller sizes: the Audio player (not on a phone) and the corner panel, on the bar
	     rather than in the drawer because they are reached mid-clip. -->
	{#if !phoneWidth.yes}
		<Separator vertical />
		<Tooltip label={ACTS.audioPlayer} shortcut={keyOf('audioPlayer', 'player')}>
			<Button
				tone="ghost"
				icon="cadence"
				aria-label={ACTS.audioPlayer}
				onclick={() => toMini(true)}
			/>
		</Tooltip>
	{/if}

	<Tooltip label={ACTS.miniPlayer} shortcut={keyOf('miniPlayer', 'player')}>
		<Button
			tone="ghost"
			icon="picture_in_picture"
			aria-label={ACTS.miniPlayer}
			onclick={() => toMini()}
		/>
	</Tooltip>

	<!-- Shared with the bar a photograph gets, so the two cannot end up disagreeing about which icon
	     means which direction. It draws nothing when there is no stage to take the screen. -->
	<FullscreenButton keyboard="player" />
{/snippet}

<style>
	/* The size and the fit are the stage's, not this component's. What is left here is the pointer,
	   which says the picture can be clicked. */
	video {
		display: block;
		cursor: pointer;
	}

	/* The line, with no track behind it, its ends meeting the frame's curve: inset by
	   `r - sqrt(r^2 - (r - h)^2)` for `--frame-corner` and the thickness, unitless for `sqrt()`. */
	.player-progress {
		block-size: var(--progress-line);
		--corner: calc(var(--frame-corner, 0px) / 1px);
		--thickness: calc(var(--progress-line) / 1px);
		inset-inline: calc(
			(
					var(--corner) -
						sqrt(
							max(
								0,
								var(--corner) * var(--corner) - (var(--corner) - var(--thickness)) *
									(var(--corner) - var(--thickness))
							)
						)
				) *
				1px
		);
	}

	.elapsed {
		display: block;
		block-size: 100%;
		inline-size: var(--level, 0%);
		background: var(--sift-accent);
	}

	.reason {
		margin: var(--space-3) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		text-align: center;
	}
</style>
