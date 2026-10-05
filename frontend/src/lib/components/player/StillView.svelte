<script lang="ts">
	/* LIVE: nothing moves it (one random file asked for on a press and opened at once; nothing is kept) */
	/*
	 * A photograph or a GIF in the stage, with the one control it needs.
	 *
	 * The file itself rather than its thumbnail: the still was made for a tile, and blown up to fill
	 * this space it is a soft, blocky version of a picture the person already has at full size. A
	 * GIF is here rather than in the player for a harder reason: no browser demuxes GIF through
	 * the media stack, so a `<video>` pointed at a perfectly good one shows a blank frame forever
	 * and reports nothing wrong. An `<img>` animates it and loops it without being asked.
	 *
	 * What this adds is a still's chrome. The player draws a bar with a fullscreen button on it and
	 * a double-click that toggles; without the same on a photograph:
	 *
	 *   - opening one from the grid would give no way to fill the screen with it at all, and
	 *   - stepping from a video onto one WHILE fullscreen would leave the screen taken by a picture
	 *     with no visible way back out except Escape: the button would go with the player.
	 *
	 * The stage keeps fullscreen across that step. This is the half that makes it usable: the same
	 * bar in the same place with the same button, so moving through a mixed run does not change what
	 * the controls are.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { handover } from '$lib/player/mini.svelte';
	import FullscreenButton from './FullscreenButton.svelte';
	import ScreenshotButton from './ScreenshotButton.svelte';
	import PlayerBar from './PlayerBar.svelte';
	import Separator from '$lib/components/common/Separator.svelte';
	import { Button, Empty, KeyEcho, Zoomable } from '$lib/components/common';
	import FileUnreachable from './FileUnreachable.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { getStage } from './stage.svelte';
	import { dwell, restFor } from '$lib/player/dwell.svelte';
	import {
		loopModeIcon,
		loopModeLabel,
		loopRepeats,
		nextLoopMode,
		PICTURES_LEFT_OUT,
		PLAYS_THROUGH_ONLY
	} from '$lib/player/loop-modes';
	import { run } from '$lib/player/run.svelte';
	import { toggleShuffle } from '$lib/player/asset-view';
	import { api } from '$lib/api/client';
	import StatsPanel from './StatsPanel.svelte';
	import { type FileFacts } from '$lib/player/facts';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { handOffSitting, noteMagnified } from '$lib/player/sitting.svelte';
	import { pressed, type Actions, type PlayerAction } from '$lib/shell/shortcuts';
	import type { Offerable } from '$lib/remote/offer.svelte';
	import { loudness } from '$lib/player/loudness.svelte';
	import { canDriveAnimations, driveAnimation, type Animation } from '$lib/player/animation';
	import type { components } from '$lib/api/schema';
	import { ACTS, keyOf } from '$lib/player/acts';
	import { drawsHeic, drawsHeicNow } from './heic';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';

	interface Props {
		id: string;
		/** What this is: a photograph, or a GIF with a length of its own. */
		mediaType?: string;
		/** How long a GIF runs for, in milliseconds. A photograph has none. */
		durationMs?: number | null;
		/**
		 * Where a run goes when this one has been on screen long enough.
		 *
		 * A picture has no end to reach, so without this a run of clips would stop dead on the first
		 * photograph in it and wait for somebody to come back to the screen. How long "long
		 * enough" is depends on what this is. See `restFor`.
		 */
		onplayedthrough?: () => void;
		/** Whether the run brought this picture up; one opened by a press waits for Play. */
		reachedByRun?: boolean;
		/** Open some other file instead of this one. Absent where there is no way to open what it
		 *  finds, and then the control that needs it is not drawn. */
		/** Open another file, one this list may not hold; `runs` says whether it plays. */
		onopen?: (id: string, runs: boolean) => void;
		/**
		 * The browser has decoded enough of the file to know its shape: the picture's counterpart
		 * to the player's `ondurationknown`, for the same caller. The corner panel takes the shape
		 * of what is put in it, and asking too early answers nothing, so it is told.
		 */
		onpicturesize?: (size: { width: number; height: number }) => void;
		/**
		 * Whether this is the small panel in the corner rather than the full-size view.
		 *
		 * The panel draws its own chrome: it is a few hundred pixels across, and a bar over that is
		 * most of what there is to look at, so the picture arrives with nothing on it. The same
		 * word the player uses for the same request.
		 */
		compact?: boolean;
		/** The file before and after this one in the list it was opened from: the bar's outer pair. */
		onprevious?: () => void;
		onnext?: () => void;
		/** The file's own facts, for the panel somebody opens deliberately.
		 *
		 *  The clip player's shape, not a second one written out here: this panel answers the same
		 *  questions that one does, and a still describing its own facts would come to word them
		 *  differently. */
		file?: FileFacts | null;
		/** A picture only some browsers draw (a phone's HEIC), as the file's detail says. */
		mayNotDraw?: boolean;
	}

	let {
		id,
		mediaType = 'image',
		durationMs = null,
		compact = false,
		onprevious,
		onnext,
		onplayedthrough,
		reachedByRun = false,
		onopen,
		onpicturesize,
		file = null,
		mayNotDraw = false
	}: Props = $props();

	onMount(() => void dwell.load());

	/*
	 * The run moving on from a picture, by a timer: a picture has no end to report. It rests where
	 * a clip's end would move the run on (`run.movesOnAfter`) for as long as `restFor` says, and
	 * only once the run brought it up or Play was pressed on it.
	 */
	const restLength = $derived(
		onplayedthrough && dwell.known && run.movesOnAfter(dwell.mode)
			? restFor(mediaType, durationMs, { pictures: dwell.pictures })
			: null
	);
	/** Play pressed on a picture waiting under Play through: its rest starts. */
	let begun = $state(false);
	const rest = $derived(restLength !== null && (reachedByRun || begun) ? restLength : null);
	const waits = $derived(restLength !== null && rest === null);
	$effect(() => {
		void id;
		// Held (Space, or the phone's pause): the run waits on this picture until it is let go, and
		// then gives it its whole rest again.
		if (rest === null || held) return;
		const timer = setTimeout(() => onplayedthrough?.(), rest);
		return () => clearTimeout(timer);
	});

	const frame = getStage();

	/*
	 * Looking closer, while fullscreen.
	 *
	 * Fullscreen is where a picture is finally big enough that the detail in it is worth reaching,
	 * and it is also the one place there is nothing else on screen for a wheel or a drag to mean. In
	 * the window they already mean something: the wheel scrolls the page behind the viewer, and a drag
	 * is how a picture gets dropped onto a tag. So this is fullscreen only, rather than a mode with a
	 * button, and outside it every gesture keeps the meaning it had.
	 *
	 * Held here rather than as a transform on the stage, because it belongs to THIS picture: stepping
	 * to the next one starts again at fit-to-screen, which is what somebody moving through a run
	 * expects. Coming back out of fullscreen resets it for the same reason.
	 */
	/* The arithmetic lives in `Zoomable`, which the full-screen picture viewer uses as well. Keeping
	   the point under the pointer and clamping the pan to the overhang are the same rules on both
	   surfaces, and two copies of them would drift into two pictures that magnify slightly
	   differently, which is not something anybody would ever report. */
	const view = new Zoomable();
	/* A picture shown magnified says so to the sitting looking at it, which the frame around this
	   owns (`noteMagnified`): whether it was magnified is a fact of the sitting, kept with it. */
	$effect(() => {
		if (view.magnified) untrack(() => noteMagnified(id));
	});

	let img = $state<HTMLImageElement | null>(null);

	/*
	 * A GIF is driven (decoded frame by frame onto a canvas) wherever the browser can decode
	 * one, which makes it pausable: an `<img>` has no playhead, no pause and no way to be asked.
	 * The same as Theater's cell.
	 *
	 * Where the browser cannot (Firefox, and any page over plain http, where the decoder is
	 * withheld with the rest of the secure-context features), the `<img>` stays, the GIF
	 * plays, and the pause control is not offered rather than offered and ignored.
	 * `$lib/player/animation` knows which, and measures it.
	 */
	const driven = $derived(mediaType === 'gif' && canDriveAnimations());
	/* In a run, Play is a thing this picture has on one answer and not the others, so the bar keeps
	   it, dimmed with the reason, and pressing Repeat this takes nothing off the bar. */
	const playWhy = $derived(
		!onplayedthrough || driven || rest !== null || waits
			? undefined
			: run.movesOnAfter(dwell.mode)
				? PICTURES_LEFT_OUT
				: PLAYS_THROUGH_ONLY
	);
	/* Where it is not driven, the pause control is withheld and nothing says so. */
	let frozen = $state<HTMLCanvasElement | null>(null);
	/** The GIF being driven, while one is. Not state: nothing draws from it. */
	let running: Animation | null = null;
	/** Which file the GIF was started for, so a re-run does not start it twice. */
	let animating: string | null = null;
	/** Whether the person has held the GIF on a frame. A new file starts playing. */
	let held = $state(false);
	/* The presses of R and S at the keyboard, for their corner badges (`KeyEcho`), the Player's. */
	let repeatPresses = $state(0);
	let shufflePresses = $state(0);
	$effect(() => {
		void id;
		held = false;
		begun = false;
	});
	$effect(() => {
		const canvas = frozen;
		const wanted = driven ? id : null;
		if (wanted === animating) return;
		running?.close();
		running = null;
		animating = wanted;
		if (wanted === null || canvas === null) return;
		void driveAnimation(`/api/assets/${wanted}/stream`, canvas, {
			held,
			onsize: (size) => onpicturesize?.(size),
			onfail: () => (unreachable = true)
		}).then((made) => {
			// The picture may have moved on while the file was being fetched, and then this
			// belongs to nothing: closed rather than left decoding into a canvas somebody else has.
			if (made === null) return;
			if (animating !== wanted) made.close();
			else running = made;
		});
	});
	$effect(() => {
		if (held) running?.pause();
		else running?.play();
	});
	onDestroy(() => {
		running?.close();
		running = null;
	});
	function toggleHeld(): void {
		held = !held;
	}

	/* Play on a waiting picture starts its rest; otherwise it holds and lets go. */
	function pressPlay(): void {
		if (waits) {
			begun = true;
			held = false;
		} else toggleHeld();
	}
	const looksPlaying = $derived(!waits && (driven || rest !== null) && !held);

	$effect(() => {
		view.watch(driven ? frozen : img);
	});

	/* Whether the full-size fetch failed. Set by the element's own `error`, which is the only way
	   to learn it: asking the server first would be a request per picture to find out something
	   the browser is about to say. Reset when the id changes, or stepping from a file that is gone
	   to one that is there would leave the message up over a perfectly good picture. */
	let unreachable = $state(false);
	/* A picture whose bytes ARRIVED and which this browser could not DRAW: a phone's HEIC in
	   anything but Safari. The element's `error` says the same for both, so after one the server
	   is asked (a HEAD of the same address, so nothing is downloaded twice) whether the file is
	   there. There, the photograph is drawn from the copy Sift made of it when it was read, a JPEG
	   every browser draws (`copy`); with no copy either, the screen says the browser cannot show
	   it (`undrawable`), and never that the file cannot be reached, because it can. Save to device
	   still hands over the original: this changes what is drawn, never what is saved. */
	let copy = $state(false);
	let undrawable = $state(false);
	/* Whether this browser draws HEIC (`./heic`). A picture only some browsers draw is drawn from
	   its copy from the start where the answer is no, and held until the answer is in. */
	let heic = $state(drawsHeicNow());
	$effect(() => {
		if (mayNotDraw && heic === undefined) void drawsHeic().then((answer) => (heic = answer));
	});
	const fromCopy = $derived(copy || (mayNotDraw && heic === false));
	const holding = $derived(mayNotDraw && heic === undefined && !copy);
	$effect(() => {
		void id;
		unreachable = false;
		copy = false;
		undrawable = false;
	});

	/** The element could not draw what it was given: work out which of the three answers it is. */
	async function drawFailed(): Promise<void> {
		if (copy) {
			undrawable = true;
			return;
		}
		// One refusal is enough for a picture the server says only some browsers draw.
		if (mayNotDraw && !fromCopy) {
			copy = true;
			return;
		}
		const asked = id;
		let there = false;
		try {
			there = (await fetch(`/api/assets/${asked}/stream`, { method: 'HEAD' })).ok;
		} catch {
			there = false;
		}
		// The viewer may have moved on while the server answered; this belongs to nothing then.
		if (asked !== id) return;
		// Drawn from the copy from the start and the copy is missing: the original is there and
		// this browser cannot draw it.
		if (fromCopy) {
			if (there) undrawable = true;
			else unreachable = true;
			return;
		}
		if (there) copy = true;
		else unreachable = true;
	}

	/* A new picture, or the end of fullscreen, is a fresh start. Reading both so either does it. */
	$effect(() => {
		void id;
		void frame?.isFullscreen;
		view.reset();
	});

	/* And the pointer says so while it is available. The same condition the gestures below are
	   gated on, read from the same place: a cursor offering to magnify where the wheel does
	   nothing is worse than no cursor at all. */
	$effect(() => {
		view.canMagnify = frame?.isFullscreen === true;
	});

	/* Fullscreen only, rather than a mode with a button. In the window the wheel scrolls the page
	   behind the viewer and a drag is how a picture gets dropped onto a tag, so magnifying there
	   would take two gestures away from what they already mean. */
	function onWheel(event: WheelEvent): void {
		if (!frame?.isFullscreen) return;
		if (view.wheel(event)) frame.wake();
	}

	function onPointerDown(event: PointerEvent): void {
		if (!frame?.isFullscreen) return;
		view.grab(event);
	}

	function onPointerMove(event: PointerEvent): void {
		view.drag(event);
	}

	function onPointerUp(event: PointerEvent): void {
		view.release(event);
	}

	/*
	 * A press steps the magnification, while the screen is filled.
	 *
	 * Fullscreen only, like every other gesture here: in the window a press on a picture starts a
	 * drag onto a tag, which already means something. `panned` separates two presses that begin
	 * identically: a press that moved the picture was a pan, and letting go at its end must not
	 * also zoom.
	 *
	 * A press waits to find out whether it was a press. A click is known to be single only once the
	 * double-click window has passed (the browser says so afterwards, with `dblclick`), and acting
	 * at once then taking it back is not enough: the step is animated over a third of a second, so
	 * part of it would be on screen before the second click, exactly what somebody leaving the
	 * filled screen sees.
	 *
	 * So the step is held for `A_SECOND_CLICK_MAY_STILL_COME` and cancelled if a second click
	 * comes. That costs a quarter of a second of lag on the zoom, the right way round: the coarse
	 * zoom is a press somebody makes and watches, while leaving is a gesture they expect to just
	 * work. The wheel is untouched and immediate.
	 *
	 * `undoStep` is not redundant: Windows allows a double-click as slow as half a second, and a
	 * press held that long would read as broken, so the timer is set for the fast case and the undo
	 * catches a slow double-click that fires after the step has run. Quick double-clicks never
	 * zoom; slow ones zoom and are put straight back.
	 *
	 * A judgement rather than a guard (raising it makes the zoom laggier and catches slower
	 * double-clicks, and the undo covers what it misses), and it is pinned:
	 * `StillView.svelte.test.ts` advances the clock by less than the wait, which separates zero
	 * (the step is due at once) from 260 (nothing is due yet), alongside the test that a
	 * double-press cancels the held one.
	 */
	const A_SECOND_CLICK_MAY_STILL_COME = 260;

	/** The held press, or null when none is waiting. */
	let waiting: ReturnType<typeof setTimeout> | null = null;
	/* Whether a press that ALREADY RAN belongs to the sequence in progress, so a double-press knows
	   whether there is anything to take back. Written on every first click, so it can never
	   describe an older one: a stale yes would restore a magnification somebody had already left. */
	let stepped = false;

	function forgetTheHeldPress(): void {
		if (waiting !== null) clearTimeout(waiting);
		waiting = null;
	}

	function onPress(event: MouseEvent): void {
		/* The second click of a double-click is not a press. Letting it through would step the
		   zoom twice on the way to a gesture that means "leave the filled screen", so the picture
		   would jump in and back out under somebody trying to get out of it. */
		if (event.detail > 1) return;
		stepped = false;
		forgetTheHeldPress();
		if (!frame?.isFullscreen || view.panned) return;
		// The event is not kept: only the point it happened at. An event object is pooled and
		// reused by the browser, and reading `clientX` off it a quarter of a second later is
		// reading whatever it has been recycled for since.
		const at = { clientX: event.clientX, clientY: event.clientY };
		waiting = setTimeout(() => {
			waiting = null;
			stepped = view.step(at);
			if (stepped) frame?.wake();
		}, A_SECOND_CLICK_MAY_STILL_COME);
	}

	/*
	 * Double-press fills the screen, or empties it, and nothing else: one gesture, one meaning,
	 * including on a magnified picture. The accidental step the two clicks would take is taken
	 * straight back rather than animated back.
	 */
	function onDoubleClick(): void {
		// A press still waiting is a press that never happened. Nothing to undo, and nothing to see.
		forgetTheHeldPress();
		// One that already ran (a slow double-click) is put straight back, without animating.
		if (stepped) view.undoStep();
		stepped = false;
		frame?.toggleFullscreen();
	}

	/*
	 * Arriving to fill the screen: F pressed on the corner panel, which has no stage of its own (see
	 * `handover.fillsTheScreen`), answered here the way the video player answers it, once the stage
	 * is drawn and still inside the moment the browser counts the press as the person's own.
	 */
	$effect(() => {
		if (compact || !frame?.element) return;
		untrack(() => {
			if (handover.takeFill(id) && !document.fullscreenElement) frame.toggleFullscreen();
		});
	});
	/*
	 * No delay on this end: the stage waits a second after the pointer leaves the picture before
	 * putting the control away, and a second wait here would fire after that and wake it again. One
	 * thing owns the waiting.
	 *
	 * A press held when the picture goes away is aimed at something that is not there: stepping the
	 * zoom of whatever replaced it a quarter of a second later would be somebody else's
	 * magnification arriving on screen.
	 */
	onDestroy(forgetTheHeldPress);

	function holdControl(event?: PointerEvent) {
		frame?.hold(event);
	}

	function releaseControl() {
		frame?.wake();
	}

	/*
	 * The same drawer the player carries, with what a picture can answer.
	 *
	 * What happens at the end, Shuffle and "play something else" are about the RUN rather than about
	 * the file, so they apply to a photograph exactly as they do to a clip, and having them
	 * disappear on every still in a mixed run would make a run of mixed media feel like two
	 * different applications. What is dimmed here has no meaning on a picture: there is no playhead
	 * to loop between, no seconds to clip and one size.
	 */
	let statsOpen = $state(false);
	let finding = $state(false);

	async function randomize() {
		if (finding) return;
		finding = true;
		try {
			const answer = await api.get<components['schemas']['AssetSummary']>('/assets/random', {
				query: { avoiding: id }
			});
			if (answer?.id)
				onopen?.(answer.id, answer.media_type === 'video' || answer.media_type === 'gif');
			else toasts.show("There's nothing else to show");
		} catch {
			toasts.show("There's nothing else to show");
		} finally {
			finding = false;
		}
	}

	/*
	 * Carry on looking at it in the corner.
	 *
	 * A photograph goes to the panel as a clip does, although the player does not draw it: a run of
	 * mixed media that lost the panel every time it reached a picture would lose it at exactly the
	 * moment somebody goes looking for the next thing.
	 *
	 * Nothing to hand over but the id, what this is and the panel's sitting with it. There is no
	 * playhead to carry across and nothing to pause, so the two facts the player has to send with a
	 * clip do not arise. The sitting goes along so the corner carries on the view rather than
	 * counting a second one.
	 */
	function toMini() {
		mini.open(
			{ id, mediaType, sitting: handOffSitting(id, 'panel') },
			{ width: window.innerWidth, height: window.innerHeight },
			{
				handover: true
			}
		);
	}

	/*
	 * The shape of the picture, in its own pixels, for the panel, which takes the shape of what is
	 * put in it.
	 *
	 * Null until the browser has decoded enough of the file to know, and null rather than a guess,
	 * because a guess is a panel resized to the wrong shape, which reads as the panel being broken.
	 */
	export function pictureSize(): { width: number; height: number } | null {
		if (driven) {
			// The driver sizes the canvas to the first frame it decodes. See `driveAnimation`.
			if (!frozen || !frozen.width || !frozen.height) return null;
			return { width: frozen.width, height: frozen.height };
		}
		if (!img || !img.naturalWidth || !img.naturalHeight) return null;
		return { width: img.naturalWidth, height: img.naturalHeight };
	}

	/*
	 * The same keys the player takes, for the same requests.
	 *
	 * The one the corner is opened with belongs to whatever is on screen at FULL SIZE. In the panel
	 * that key means the opposite
	 * (come back out of the corner) and the panel binds it, so this is the one place the two are
	 * told apart. The guard is here rather than in `toMini` because what has to be avoided is not
	 * only opening a panel that is already open: it is TAKING the press, which happens on the line
	 * below and cannot be undone further in.
	 */
	const actions: Actions<PlayerAction> = {
		// The same key that pauses a clip pauses a GIF, where one can be paused at all.
		'player.playPause': ({ value }) => {
			// A GIF pauses; a photograph resting in a run is held there. A photograph that is not
			// in a run has nothing to pause.
			if (!driven && rest === null && !waits) return false;
			// The phone sends the state it wants; a key flips it.
			const wantsPlaying = value === null ? !looksPlaying : value === 1;
			if (wantsPlaying !== looksPlaying) pressPlay();
			return true;
		},
		/*
		 * F fills the screen with the picture, through the stage's own toggle.
		 *
		 * The bar's fullscreen button and the double-click already do it, and the key is declared
		 * once for "what you are watching", which includes a picture the player does not draw. The
		 * same call the player and the button make; there is no second idea of what filling the
		 * screen means. The player resets its magnification on the way through, and this does not
		 * need to: the effect above does it on every change of `isFullscreen`, so entering or
		 * leaving always starts at fit-to-screen.
		 */
		'player.fill': () => {
			// A still drawn with no stage around it (the panel, a harness) has no screen to
			// fill, and correctly does nothing rather than reaching for one.
			if (!frame) return false;
			frame.toggleFullscreen();
			return true;
		},
		'player.corner': () => {
			toMini();
			return true;
		},
		/* R and S, as on a clip: what happens at the end and the order are the run's, so a key's
		   outcome is the same on every player, and the corner badge says it. The phone names the
		   answer it wants by its place in the order the key cycles, and Shuffle by its state. */
		'player.repeat': ({ key, value }) => {
			if (key === null && value !== null) return dwell.chooseAt(value);
			void dwell.choose(nextLoopMode(dwell.mode));
			repeatPresses += 1;
			return true;
		},
		'player.shuffle': ({ key, value }) => {
			if (key === null && value !== null && run.shuffle === (value === 1)) return true;
			toggleShuffle(id);
			if (key !== null) shufflePresses += 1;
			return true;
		}
	};

	/*
	 * What this picture hands up to the viewer it is drawn in, which offers it to the phone
	 * (`offerViewer`). Not offering it ("a picture has nothing playing") would leave a popout showing a
	 * photograph no screen at all to the phone. A picture has presses a phone can make: the
	 * file before and after, holding a GIF or a photograph's rest in a run, filling the screen; the
	 * viewer adds its own (a favourite, the O counter).
	 *
	 * The step pair is the phone's only, and so is not in the key table: on a still the bare arrows
	 * are the viewer's own keys, and a second answer to them here would step twice.
	 */
	export function remote(): Offerable {
		/* Only what a press here would do is offered, so the phone draws no control that answers
		   nothing: a photograph outside a run has nothing to pause, and a picture opened with no
		   list around it has no file before or after. Read afresh at every report, so a run
		   reaching this picture offers the pause the moment it can hold. */
		const table: Actions<PlayerAction> = { ...actions };
		if (!driven && rest === null && !waits) delete table['player.playPause'];
		/* What happens at the end and the order are the run's, and the drawer offers both here, so
		   the phone does too, through the keys' own answers above. */
		if (onprevious)
			table['player.previous'] = ({ key }) => {
				if (key !== null || !onprevious) return false;
				onprevious();
				return true;
			};
		if (onnext)
			table['player.next'] = ({ key }) => {
				if (key !== null || !onnext) return false;
				onnext();
				return true;
			};
		return {
			actions: table,
			state: () => ({
				playing: looksPlaying,
				position: 0,
				length: mediaType === 'gif' && durationMs ? durationMs / 1000 : null,
				file: id,
				volume: loudness.level,
				muted: true,
				repeat: dwell.mode,
				shuffle: run.shuffle
			})
		};
	}

	/* A still answers its keys from a table like the player's (see `$lib/shell/shortcuts`). */
	function onKeydown(event: KeyboardEvent) {
		if (compact) return;
		if (event.target instanceof HTMLElement && event.target.closest('input, textarea')) return;
		pressed(event, actions);
	}

	onMount(() => {
		window.addEventListener('keydown', onKeydown);
		return () => window.removeEventListener('keydown', onKeydown);
	});

	/** A button on the bar is pressed, not moved to. See the player's bar for why. */
</script>

<!--
	svelte-ignore a11y_no_noninteractive_element_interactions: the double-click is an addition to
	the button below rather than the only way to reach fullscreen, which is what makes it a shortcut
	and not the sole affordance. Same gesture the video takes, on purpose.
-->
{#if undrawable}
	<!--
		The file is there and this browser cannot draw it, and there is no copy to draw instead.
		Said as what it is, a fact about the browser, never as a missing file: the facts below are
		the file's, and Save to device hands over the original.
	-->
	<div class="gone">
		<Empty scope="page" icon="hide_image" title="This browser can't show this kind of picture">
			The file is here and nothing is wrong with it. Save to device gives you the original, to open
			in an app that can show it.
		</Empty>
	</div>
{:else if unreachable}
	<!--
		The bytes are not there, and this says so rather than drawing a broken frame.

		A picture whose file has gone still has a thumbnail (derivatives are kept beside the
		library, not inside it), so it appears on the wall, opens, and reports its size and
		dimensions from the row. Only the full-size fetch fails, and the browser's answer is a
		torn-page glyph in a black rectangle, which reads as Sift being broken.

		A file can be gone for ordinary reasons (a drive not mounted, a folder moved, an archive
		deleted after its pictures were taken in), and the screen cannot tell which, so it says the
		one thing it knows.

		The same glyph the tile's gone mark wears, distinct from the hidden mark's crossed-out eye:
		one fact, one glyph, on the wall and on the file alike.
	-->
	<div class="gone">
		<FileUnreachable />
	</div>
{:else}
	<!-- svelte-ignore a11y_click_events_have_key_events: magnifying is a POINTER affordance and has
	     no keyboard meaning: a press steps the zoom about the point pressed, and there is no point
	     without a pointer. Nothing is reachable only this way, and the wheel and the drag beside it
	     are exempt for the same reason. -->
	{#if driven}
		<!-- The frames, drawn by Sift rather than by the element, which is what makes them stoppable.
		     Every gesture the picture takes is the same one, so magnifying and dragging a GIF
		     is magnifying and dragging a photograph. -->
		<canvas
			bind:this={frozen}
			class="picture"
			aria-label="GIF"
			style:cursor={view.cursor}
			style:scale={view.scale}
			style:translate={view.offset}
			style:transition={view.transition}
			onclick={onPress}
			ondblclick={onDoubleClick}
			onwheel={onWheel}
			onpointerdown={onPointerDown}
			onpointermove={onPointerMove}
			onpointerup={onPointerUp}
			onpointercancel={onPointerUp}
		></canvas>
	{:else}
		<img
			bind:this={img}
			src={holding
				? undefined
				: fromCopy
					? `/api/assets/${id}/rendition`
					: `/api/assets/${id}/stream`}
			onload={() => {
				const size = pictureSize();
				if (size) onpicturesize?.(size);
			}}
			onerror={() => void drawFailed()}
			alt=""
			style:cursor={view.cursor}
			style:scale={view.scale}
			style:translate={view.offset}
			style:transition={view.transition}
			onclick={onPress}
			ondblclick={onDoubleClick}
			onwheel={onWheel}
			onpointerdown={onPointerDown}
			onpointermove={onPointerMove}
			onpointerup={onPointerUp}
			onpointercancel={onPointerUp}
		/>
	{/if}
{/if}

<!-- The bar a clip wears, every control in its place; nothing to seek or hear. -->

<!-- What R and S just did, in the corner of the picture, as the Player says it for a clip. -->
{#if !compact}
	<div class="key-echoes">
		{#key id}
			<KeyEcho
				icon={loopModeIcon(dwell.mode)}
				label={loopModeLabel(dwell.mode)}
				muted={!loopRepeats(dwell.mode)}
				press={repeatPresses}
			/>
			<KeyEcho
				icon="shuffle"
				label={run.shuffle ? ACTS.shuffle : 'In order'}
				muted={!run.shuffle}
				press={shufflePresses}
			/>
		{/key}
	</div>
{/if}
{#if !compact}
	<PlayerBar
		position={0}
		duration={0}
		onseek={() => {}}
		seekable={false}
		sound={false}
		playing={looksPlaying}
		playable={driven || rest !== null || waits}
		{playWhy}
		timed={false}
		onplay={pressPlay}
		onback={onprevious}
		onforward={onnext}
		backLabel={ACTS.previous}
		forwardLabel={ACTS.next}
		shuffle={{ on: run.shuffle, onpress: () => toggleShuffle(id) }}
		repeat={{ mode: dwell.mode, onpress: () => void dwell.choose(nextLoopMode(dwell.mode)) }}
		keyboard="picture"
		muted={true}
		volume={0}
		onmute={() => {}}
		onvolume={() => {}}
		tray={pictureTray}
		trayLabel="This picture"
		trailing={pictureTrailing}
		onhold={holdControl}
		onrelease={releaseControl}
	/>
{/if}

<!--
	The drawer: THE SAME SEVEN a clip's drawer holds, in the same places, so stepping from a clip to
	a picture moves nothing under the hand. What a picture cannot do is drawn dimmed with the reason
	as its label, the rule a Theater cell's drawer follows. Order: Clip, Screenshot, Quality;
	Randomize, the loop's two marks, Save as Loop; Stats for nerds. What happens at the end and
	Shuffle are on the bar, live here too: the end's answer is what moves a run off this picture.
-->
{#snippet pictureTray()}
	<Tooltip label="A picture has no seconds to clip" placement="top">
		<Button
			tone="ghost"
			icon="content_cut"
			aria-label="A picture has no seconds to clip"
			disabled
		/>
	</Tooltip>
	<ScreenshotButton
		of={id}
		still={driven ? frozen : img}
		stage={frame?.element ?? null}
		portalTo={frame?.isFullscreen ? frame.element : null}
	/>
	<Tooltip label="A picture has one size" placement="top">
		<Button tone="ghost" icon="video_settings" aria-label="A picture has one size" disabled />
	</Tooltip>
	<Tooltip label={onopen ? ACTS.randomize : 'Nothing here can open a random file'} placement="top">
		<Button
			tone="ghost"
			icon="casino"
			aria-label={onopen ? ACTS.randomize : 'Nothing here can open a random file'}
			disabled={!onopen}
			onclick={randomize}
		/>
	</Tooltip>
	<Tooltip label="A picture has no stretch to loop" placement="top">
		<Button
			tone="ghost"
			icon="all_inclusive"
			aria-label="A picture has no stretch to loop"
			disabled
		/>
	</Tooltip>
	<Tooltip label="A picture has no stretch to save" placement="top">
		<Button
			tone="ghost"
			icon="bookmark_add"
			aria-label="A picture has no stretch to save"
			disabled
		/>
	</Tooltip>
	<Tooltip label={ACTS.stats} placement="top">
		<Button
			tone="ghost"
			icon="cognition_2"
			aria-label={ACTS.stats}
			pressed={statsOpen}
			onclick={() => (statsOpen = !statsOpen)}
		/>
	</Tooltip>
{/snippet}

<!-- The end of the row where a clip's bar keeps it: the Audio player dimmed, the corner, the screen. -->
{#snippet pictureTrailing()}
	<Separator vertical />
	{#if !phoneWidth.yes}
		<Tooltip label="No sound in this">
			<Button tone="ghost" icon="cadence" aria-label="No sound in this" disabled />
		</Tooltip>
	{/if}
	<Tooltip label={ACTS.miniPlayer} shortcut={keyOf('miniPlayer', 'picture')}>
		<Button tone="ghost" icon="picture_in_picture" aria-label={ACTS.miniPlayer} onclick={toMini} />
	</Tooltip>
	<FullscreenButton keyboard="picture" />
{/snippet}

{#if statsOpen && !compact}
	<!--
		The SAME panel the player and a Theater cell draw, told that nothing is playing.

		Not a second copy (its own `<aside>`, its own `<dl>`, rows written out by hand, a different
		corner of the picture): nothing would keep the two in step, and a line added to the panel a
		clip carries would never appear on a photograph with nothing to say so. `still` takes out
		the eight rows about playback (a picture has no bitrate and no playhead) and `kind` is
		the one line a still has that a clip does not.

		Inside the stage rather than portalled to the page, for the reason the player's panel
		documents: a browser draws the fullscreened element and what is inside it.
	-->
	<StatsPanel {file} still kind={mediaType === 'gif' ? 'GIF' : 'Photo'} position={0} duration={0} />
{/if}

<style>
	/* The Player's corner: clear of the top edge's strip and inside the frame's rounding. */
	.key-echoes {
		position: absolute;
		inset-block-start: var(--space-5);
		inset-inline-start: var(--space-5);
		z-index: 2;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		pointer-events: none;
	}

	/* A file Sift cannot reach. Centred in the frame the picture would have filled, and quiet: it
	   is a statement of fact rather than an error, and everything known about the file is still on
	   the page below it. */
	/* Only the BOX is this view's: the words are `FileUnreachable`'s, which the video's player says
	   too, so one missing file reads the same in both. This keeps the stage's height so a
	   file that cannot be reached does not collapse the viewer to a line of text. */
	.gone {
		display: grid;
		place-items: center;
		block-size: 100%;
		min-block-size: 16rem;
		padding: var(--space-6);
	}

	/* Rendered inside the bar rather than here, so the rule has to be `:global`: a snippet is
	   styled where it is drawn, not where it is written. Namespaced for the same reason, and dressed
	   the way the wall dresses its copy: the sentence is shared, the dressing belongs to each bar,
	   which is the rule every component in this app follows about its own furniture. */
	img,
	.picture {
		display: block;
		/* Says the picture can be double-clicked, the same way the video's does. */
		cursor: pointer;
	}

	/* Magnified, the picture is something to move rather than something to press, and what the
	   pointer looks like while it is being moved is `Zoomable`'s answer, shared with the two other
	   surfaces that magnify a picture. See the getter. There is deliberately no transition on the
	   scale or the translate: a wheel arrives as a stream of small steps, and a duration on each one
	   is a magnifier that lags behind the hand. */
</style>
