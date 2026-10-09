<script lang="ts">
	/* LIVE: nothing moves it (one random file asked for on a press and opened immediately; nothing is kept) */
	/*
	 * A photograph or a GIF in the stage: the file itself, not its thumbnail; a GIF in an `<img>`,
	 * since no browser demuxes one as video. The same bar and fullscreen button as a clip, so
	 * stepping through a mixed run, fullscreen included, changes nothing under the hand.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { handover } from '$lib/player/mini.svelte';
	import FullscreenButton from './FullscreenButton.svelte';
	import RunControls from './RunControls.svelte';
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
		mediaType?: string;
		durationMs?: number | null;
		/** Where a run goes once this has been on screen long enough (`restFor`). */
		onplayedthrough?: () => void;
		reachedByRun?: boolean;
		/** Open another file, one this list may not hold; `runs` says whether it plays. */
		onopen?: (id: string, runs: boolean) => void;
		/** The decoded shape, for the corner panel, which takes the shape of what is in it. */
		onpicturesize?: (size: { width: number; height: number }) => void;
		/** The corner panel, which draws its own chrome. */
		compact?: boolean;
		onprevious?: () => void;
		onnext?: () => void;
		/** The clip player's shape, so both panels word the facts alike. */
		file?: FileFacts | null;
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

	/* A picture rests by a timer where a clip's end would move the run on (`run.movesOnAfter`). */
	const restLength = $derived(
		onplayedthrough && dwell.known && run.movesOnAfter(dwell.mode)
			? restFor(mediaType, durationMs, { pictures: dwell.pictures })
			: null
	);
	let begun = $state(false);
	const rest = $derived(restLength !== null && (reachedByRun || begun) ? restLength : null);
	const waits = $derived(restLength !== null && rest === null);
	$effect(() => {
		void id;
		// Held: the run waits here, and gets its whole rest again when let go.
		if (rest === null || held) return;
		const timer = setTimeout(() => onplayedthrough?.(), rest);
		return () => clearTimeout(timer);
	});

	const frame = getStage();

	/*
	 * Looking closer, in FULLSCREEN only: in the window the wheel scrolls and a drag files the
	 * picture. Belongs to this picture, so the next starts at fit-to-screen. The arithmetic is
	 * `Zoomable`'s.
	 */
	const view = new Zoomable();
	/* Magnified is a fact of the sitting (`noteMagnified`). */
	$effect(() => {
		if (view.magnified) untrack(() => noteMagnified(id));
	});

	let img = $state<HTMLImageElement | null>(null);

	/*
	 * A GIF is driven onto a canvas where it can be, so it can pause; elsewhere the pause is
	 * withheld.
	 */
	const driven = $derived(mediaType === 'gif' && canDriveAnimations());
	/* In a run, Play stays dimmed with its reason. */
	const playWhy = $derived(
		!onplayedthrough || driven || rest !== null || waits
			? undefined
			: run.movesOnAfter(dwell.mode)
				? PICTURES_LEFT_OUT
				: PLAYS_THROUGH_ONLY
	);
	let frozen = $state<HTMLCanvasElement | null>(null);
	let running: Animation | null = null;
	let animating: string | null = null;
	let held = $state(false);
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
			// The picture moved on during the fetch: closed, not left decoding.
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

	/* From the element's own `error`; reset when the id changes. */
	let unreachable = $state(false);
	/*
	 * Arrived but undrawable (a phone's HEIC): a HEAD asks whether the file is there, then Sift's
	 * JPEG copy is drawn, else the browser is said to be unable. Save still hands over the
	 * original.
	 */
	let copy = $state(false);
	let undrawable = $state(false);
	/* Whether this browser draws HEIC (`./heic`). */
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

	async function drawFailed(): Promise<void> {
		if (copy) {
			undrawable = true;
			return;
		}
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
		if (asked !== id) return;
		if (fromCopy) {
			if (there) undrawable = true;
			else unreachable = true;
			return;
		}
		if (there) copy = true;
		else unreachable = true;
	}

	$effect(() => {
		void id;
		void frame?.isFullscreen;
		view.reset();
	});

	/* The cursor offers magnifying only where the wheel does it. */
	$effect(() => {
		view.canMagnify = frame?.isFullscreen === true;
	});

	/* Fullscreen only. */
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
	 * A press steps the magnification in fullscreen, held for `A_SECOND_CLICK_MAY_STILL_COME` in
	 * case it was a double-click (which leaves fullscreen); `undoStep` catches a slow one. Pinned
	 * by `StillView.svelte.test.ts`.
	 */
	const A_SECOND_CLICK_MAY_STILL_COME = 260;

	let waiting: ReturnType<typeof setTimeout> | null = null;
	/* Written on every first click, so it never describes an older one. */
	let stepped = false;

	function forgetTheHeldPress(): void {
		if (waiting !== null) clearTimeout(waiting);
		waiting = null;
	}

	function onPress(event: MouseEvent): void {
		/* The second click of a double-click is not a press. */
		if (event.detail > 1) return;
		stepped = false;
		forgetTheHeldPress();
		if (!frame?.isFullscreen || view.panned) return;
		// The point, not the event: the browser recycles event objects.
		const at = { clientX: event.clientX, clientY: event.clientY };
		waiting = setTimeout(() => {
			waiting = null;
			stepped = view.step(at);
			if (stepped) frame?.wake();
		}, A_SECOND_CLICK_MAY_STILL_COME);
	}

	/** One gesture, one meaning, magnified or not; a step already taken is undone. */
	function onDoubleClick(): void {
		forgetTheHeldPress();
		if (stepped) view.undoStep();
		stepped = false;
		frame?.toggleFullscreen();
	}

	/* F pressed on the corner panel arrives here (`handover.fillsTheScreen`). */
	$effect(() => {
		if (compact || !frame?.element) return;
		untrack(() => {
			if (handover.takeFill(id) && !document.fullscreenElement) frame.toggleFullscreen();
		});
	});
	/* No delay here: the stage owns the waiting. A held press dies with the picture. */
	onDestroy(forgetTheHeldPress);

	function holdControl(event?: PointerEvent) {
		frame?.hold(event);
	}

	function releaseControl() {
		frame?.wake();
	}

	/* The player's drawer; the run's controls apply to a picture too, the rest dim. */
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

	/* To the corner panel as a clip goes, with the id, its kind and the sitting. */
	function toMini() {
		mini.open(
			{ id, mediaType, sitting: handOffSitting(id, 'panel') },
			{ width: window.innerWidth, height: window.innerHeight },
			{
				handover: true
			}
		);
	}

	/** Null until decoded, never a guess. */
	export function pictureSize(): { width: number; height: number } | null {
		if (driven) {
			if (!frozen || !frozen.width || !frozen.height) return null;
			return { width: frozen.width, height: frozen.height };
		}
		if (!img || !img.naturalWidth || !img.naturalHeight) return null;
		return { width: img.naturalWidth, height: img.naturalHeight };
	}

	/* The player's keys; the corner key is told apart here, before the press is taken. */
	const actions: Actions<PlayerAction> = {
		'player.playPause': ({ value }) => {
			// A GIF pauses; a photograph rests in a run; otherwise nothing.
			if (!driven && rest === null && !waits) return false;
			const wantsPlaying = value === null ? !looksPlaying : value === 1;
			if (wantsPlaying !== looksPlaying) pressPlay();
			return true;
		},
		/* F, through the stage's own toggle; the effect above resets magnification. */
		'player.fill': () => {
			if (!frame) return false;
			frame.toggleFullscreen();
			return true;
		},
		'player.corner': () => {
			toMini();
			return true;
		},
		/* R and S, as on a clip, with the corner badge. */
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

	/* What the phone may press here (`offerViewer`); the step pair is the phone's alone. */
	export function remote(): Offerable {
		/* Only what a press here would do, read afresh at every report. */
		const table: Actions<PlayerAction> = { ...actions };
		if (!driven && rest === null && !waits) delete table['player.playPause'];
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

	function onKeydown(event: KeyboardEvent) {
		if (compact) return;
		if (event.target instanceof HTMLElement && event.target.closest('input, textarea')) return;
		pressed(event, actions);
	}

	onMount(() => {
		window.addEventListener('keydown', onKeydown);
		return () => window.removeEventListener('keydown', onKeydown);
	});
</script>

<!--
	svelte-ignore a11y_no_noninteractive_element_interactions: the double-click is an addition to
	the button below rather than the only way to reach fullscreen, which is what makes it a shortcut
	and not the sole affordance. Same gesture the video takes, on purpose.
-->
{#if undrawable}
	<!-- Undrawable here and no copy: a fact about the browser, never a missing file. -->
	<div class="gone">
		<Empty scope="page" icon="hide_image" title="This browser can't show this kind of picture">
			The file is here and nothing is wrong with it. Save to device gives you the original, to open
			in an app that can show it.
		</Empty>
	</div>
{:else if unreachable}
	<!--
	The bytes are not there: the tile's torn-page glyph and one plain sentence, not a broken frame.
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
		<!-- Frames drawn by Sift, so they stop; the same gestures as a photograph. -->
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

<!-- The drawer: THE SAME NINE as a clip's, what a picture cannot do dimmed with its reason. -->
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
	<RunControls
		repeat={{ mode: dwell.mode, onpress: () => void dwell.choose(nextLoopMode(dwell.mode)) }}
		shuffle={{ on: run.shuffle, onpress: () => toggleShuffle(id) }}
		keyboard="picture"
	/>
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
	The SAME panel as the player's, with `still` and `kind`, inside the stage for fullscreen.
	-->
	<StatsPanel {file} still kind={mediaType === 'gif' ? 'GIF' : 'Photo'} position={0} duration={0} />
{/if}

<style>
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

	/* Only the box: the words are `FileUnreachable`'s; the stage keeps its height. */
	.gone {
		display: grid;
		place-items: center;
		block-size: 100%;
		min-block-size: 16rem;
		padding: var(--space-6);
	}

	/* `:global`: rendered inside the bar. */
	img,
	.picture {
		display: block;
		cursor: pointer;
	}

	/* No transition on scale or translate: the wheel is a stream of steps. */
</style>
