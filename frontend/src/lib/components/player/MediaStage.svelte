<script lang="ts">
	/*
	 * The box an asset is shown in, whatever the asset turns out to be.
	 *
	 * One frame for a video, a GIF and a photograph, and three things follow from that:
	 *
	 * The window does not resize itself. A modal sized to its contents is a different size for a
	 * portrait clip, a landscape one and a screenshot, so clicking through a grid would make it
	 * jump under the pointer. The stage is a fixed shape and the picture fits inside it.
	 *
	 * Fullscreen survives moving on. The browser draws the fullscreened element and nothing else,
	 * so fullscreen ends when that element is replaced. The stage is the element, the same one for
	 * every media type, and only what is drawn inside changes, so Next onto a photograph stays
	 * fullscreen.
	 *
	 * The way forward is always there. Next and previous live here rather than on the player's bar,
	 * so a photograph has them too.
	 *
	 * It does not own the media or the controls. Those come in as snippets, and anything needing to
	 * know about fullscreen asks the handle in context rather than being handed a prop.
	 */
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/common';
	import { dragOut } from '$lib/capture/copy-out';
	import { setStage } from './stage.svelte';
	import { enterFullscreen } from './fullscreen';
	import { screenChanges } from './motion';

	interface Props {
		/** What is being shown, and its controls if it has any. A photograph has none. */
		media: Snippet;
		/**
		 * Something to say about this file, over its top corner.
		 *
		 * On the same wake as the bar below (moving the pointer brings both back) but it rests
		 * for longer, because a sentence takes longer to read than a scrub bar takes to aim at.
		 */
		notice?: Snippet;
		/**
		 * What is on the stage, so it can be dragged out of the window into another application.
		 *
		 * HERE rather than on a grid tile, and that is a decision rather than convenience. A tile
		 * already owns a drag (dropping it on a tag or a person is how things get organised) and
		 * an operating-system drag replaces the browser's one entirely, so arming it there would take
		 * that gesture away in the desktop client and leave it working in a browser. Taking a file
		 * out is also a decision about ONE file, which is exactly what this view is.
		 *
		 * Absent means no drag-out: the theatre and the small panel pass nothing.
		 */
		asset?: { id: string; filename: string };
	}

	let { media, notice, asset }: Props = $props();

	/** How long the controls stay up in fullscreen after the last thing anybody did. */
	const IDLE_MS = 2500;

	/** And how long a notice stays up. Longer: this one is read rather than aimed at. */
	const NOTICE_IDLE_MS = 5000;

	let stage = $state<HTMLElement | null>(null);
	let isFullscreen = $state(false);
	/**
	 * Whether the stage fills the WINDOW, for a browser that lets nothing fill the screen with it.
	 *
	 * An iPhone lets a video take the screen through its own player and nothing else: a picture or
	 * a GIF has no full screen there at all. The viewer already owns the phone's screen, so the
	 * nearest honest thing is the picture edge to edge over the viewer's own chrome, left by the
	 * same press. Counted as full screen by everything that asks the stage.
	 */
	let filling = $state(false);
	/** Whether the controls are showing. False once the pointer has gone idle: always in fullscreen,
	 * and in a window too where there is a pointer to go idle. See `wake`. */
	let controlsUp = $state(true);
	let idleTimer: ReturnType<typeof setTimeout> | null = null;
	/** The notice above, on its own clock. See `NOTICE_IDLE_MS`. */
	let noticeUp = $state(true);
	let noticeTimer: ReturnType<typeof setTimeout> | null = null;
	/* Whether this is a device with a pointer that can rest over the picture. A touch screen has none,
	 * so its bar never auto-hides: a bar that waits to be hovered is a bar that never appears. Read
	 * once on mount rather than watched: a machine does not grow or lose a mouse mid-session. */
	let hoverCapable = false;

	/*
	 * Something happened, so put the controls back and start the clock again.
	 *
	 * Fullscreen hides them on this clock, and a window does too: a pointer resting over the
	 * picture without moving should not keep a strip of chrome lit across the bottom of it. The one
	 * exception is a touch screen (no pointer to go idle, and no hover to bring the bar back), where
	 * the bar stays put rather than fading out of reach.
	 */
	function wake() {
		if (leftTimer) clearTimeout(leftTimer);
		leftTimer = null;
		pointed = true;
		controlsUp = true;
		noticeUp = true;
		if (idleTimer) clearTimeout(idleTimer);
		if (noticeTimer) clearTimeout(noticeTimer);
		if (!(isFullscreen || filling) && !hoverCapable) return;
		idleTimer = setTimeout(() => (controlsUp = false), IDLE_MS);
		noticeTimer = setTimeout(() => (noticeUp = false), NOTICE_IDLE_MS);
	}

	/* The pointer is on the controls. Whatever it is doing there, it is not idle, and a bar that
	 * fades out from under the hand reaching for it is the specific failure this avoids.
	 * Pair it with `wake` on the way out, or the clock it stopped is never restarted. */
	/*
	 * The pointer has left the picture entirely.
	 *
	 * Different from going idle on it: there is nothing left to move, so the idle clock is counting
	 * down from whenever the last movement happened rather than from now. A second after leaving is
	 * both quicker and more predictable: the controls go because you looked away, not because a
	 * timer that started somewhere else happened to run out.
	 */
	const LEFT_MS = 1000;

	let leftTimer: ReturnType<typeof setTimeout> | null = null;

	/*
	 * Whether the pointer counts as being on the picture.
	 *
	 * A class rather than `:hover`, because `:hover` stops matching the instant the pointer crosses
	 * the edge, so the bar would vanish the moment somebody looked away. This stays true for a
	 * second after leaving, a decision something can hold rather than a fact about where the
	 * pointer is right now.
	 */
	let pointed = $state(false);

	function left(event?: PointerEvent) {
		/* A finger lifting off a touch screen leaves as well, and it is not somebody looking away: on
		   a phone the bar stays put (see `wake`), or every tap on the picture would take the controls
		   with it a second later and the next tap would be spent bringing them back. */
		if (event?.pointerType === 'touch' && !(isFullscreen || filling)) return;
		if (idleTimer) clearTimeout(idleTimer);
		if (leftTimer) clearTimeout(leftTimer);
		leftTimer = setTimeout(() => {
			pointed = false;
			controlsUp = false;
			noticeUp = false;
		}, LEFT_MS);
	}

	function hold(event?: PointerEvent) {
		/*
		 * The move stops here.
		 *
		 * The stage wakes on `pointermove`, and a move over the bar or the drawer bubbles up to it,
		 * which restarts the very idle clock this is meant to stop. Resting the pointer inside the
		 * drawer for a couple of seconds would then take the bar, the drawer and the way through the
		 * list away together, while the pointer was still on them.
		 */
		event?.stopPropagation();
		if (leftTimer) clearTimeout(leftTimer);
		leftTimer = null;
		pointed = true;
		controlsUp = true;
		noticeUp = true;
		if (idleTimer) clearTimeout(idleTimer);
		if (noticeTimer) clearTimeout(noticeTimer);
	}

	/*
	 * In, or back out.
	 *
	 * `document.fullscreenElement` is asked rather than the flag, because fullscreen can also be left
	 * by Escape, by the F11 key and by the browser itself, none of which pass through here. The flag
	 * follows the browser; it never leads it.
	 */
	function toggleFullscreen() {
		if (filling) {
			filling = false;
			wake();
			return;
		}
		if (document.fullscreenElement) {
			void document.exitFullscreen?.();
			return;
		}
		// The stage where an element may fill the screen, its video where only a video may (an
		// iPhone), and the window where neither may (a picture on an iPhone). See `fullscreen.ts`.
		if (stage && !enterFullscreen(stage)) {
			filling = true;
			wake();
		}
	}

	setStage({
		get element() {
			return stage;
		},
		get isFullscreen() {
			return isFullscreen || filling;
		},
		/* The one clock, offered to whatever the bar opened. `controlsUp` alone is the honest answer:
		   `pointed` only ever goes false alongside it, in `left`. See `StageHandle.showing`. */
		get showing() {
			return controlsUp;
		},
		toggleFullscreen,
		wake,
		hold
	});

	onMount(() => {
		// A pointer that can hover is the difference between a bar that fades and one that must stay.
		hoverCapable = window.matchMedia?.('(hover: hover)')?.matches ?? false;
		const onFullscreen = () => {
			isFullscreen = document.fullscreenElement === stage;
			// Entering starts the idle clock; leaving cancels it and puts the bar back, so a page
			// never ends up with controls that faded out and no way to bring them back.
			wake();
		};
		document.addEventListener('fullscreenchange', onFullscreen);
		return () => {
			document.removeEventListener('fullscreenchange', onFullscreen);
			if (idleTimer) clearTimeout(idleTimer);
		};
	});
</script>

<!--
	`onpointermove` on the stage rather than on the media, so moving over the controls counts as
	activity too: the bar has to stay up for the hand that is reaching for it.

	`ondragstart` is here and the stage is NOT itself draggable: the picture inside it is. So the
	handler only ever runs for a drag that began on the media, never for one that began on the
	scrub bar, which would otherwise turn a scrub into a drag.
-->
<div
	class="stage"
	class:fullscreen={isFullscreen || filling}
	class:filling
	class:resting={!controlsUp}
	class:pointed
	bind:this={stage}
	use:screenChanges={{ key: filling }}
	onpointermove={wake}
	onpointerenter={wake}
	onpointerleave={left}
	ondragstart={(event) => {
		if (asset) dragOut(event, asset);
	}}
	role="group"
	aria-label="Viewer"
>
	{@render media()}

	{#if notice}
		<!-- Over the top corner of the picture, on the same wake as the bar along the bottom. Held up
		     while the pointer is on it, so it cannot fade out from under somebody reading it. -->
		<div
			class="notice"
			class:up={noticeUp}
			onpointerenter={hold}
			onpointermove={hold}
			onpointerleave={wake}
			role="status"
		>
			{@render notice()}
		</div>
	{/if}
</div>

<style>
	/*
	 * A fixed shape, so the dialog around it is one size for everything.
	 *
	 * The height is what is fixed and the width comes from the dialog, because the dialog's width is
	 * already a constant. A tall clip letterboxes inside it rather than making the window taller,
	 * which is the trade being made on purpose: a window that stays put is worth more than a few
	 * unused pixels beside a portrait video.
	 */
	.stage {
		position: relative;
		/* Overridable by whatever the stage is placed in: the theatre page wants most of the
		   window, the dialog wants a size that keeps the dialog from moving. The default is the
		   dialog's, because that is where an asset is opened from nearly every time. */
		block-size: var(--stage-height, min(60vh, 520px));
		background: var(--sift-bg);
		/*
		 * Overridable, like the height above. A frame in a dialog rounds itself so it does not read
		 * as a hole cut in the page; a wall of frames that meet each other wants square.
		 *
		 * The frame's corner, stated once and published: the rounding reads it, and so does the
		 * progress line (`player-progress` in `Player.svelte`), which works out where its ends meet
		 * the two bottom curves. A square frame (a theater wall sets `--stage-radius: 0`) gets a
		 * full-width line.
		 */
		--frame-corner: var(--stage-radius, var(--radius-xl));
		border-radius: var(--frame-corner);
		overflow: hidden;
	}

	/* The window, where the browser lets nothing fill the screen (see `filling`): edge to edge and
	   square, over the viewer's own chrome, in the sheet's stacking order. */
	.stage.filling {
		position: fixed;
		inset: 0;
		z-index: var(--z-bar);
		block-size: auto;
		border-radius: 0;
	}

	/*
	 * Whatever is inside fits the box rather than setting it. `contain` and not `cover`: filling the
	 * frame is not permission to crop somebody's picture.
	 *
	 * ONE rule for everything a stage can hold: a video, a picture, and a GIF drawn frame by frame
	 * onto a `<canvas>` (`$lib/player/animation`, wherever the browser can decode one: the desktop
	 * app, a localhost tab). The canvas keeps its own pixel size, the GIF's, which is what keeps
	 * it sharp; this rule scales that to the frame and centres it, exactly as it does an `<img>`.
	 *
	 * Without the canvas in this list, a GIF in the desktop app would be drawn at its own size from
	 * the top left corner: a tall one cut off at the foot, a wide one small in a corner of an empty
	 * frame. Over plain http the same GIF is an `<img>` and fitted, so the fault shows only where
	 * the canvas is used. Theater's cell sits in a stage and takes this rule too;
	 * the corner panel is not a stage and names the same three in its own (`MiniPlayer`).
	 */
	.stage :global(:is(video, img, canvas)) {
		inline-size: 100%;
		block-size: 100%;
		object-fit: contain;
		background: var(--sift-bg);
	}

	/*
	 * The player's bar, along the bottom of the frame.
	 *
	 * Reached by a global class because the bar belongs to whatever was put inside: the stage does
	 * not know what its media is, which is the whole point of it. What the stage does know is where
	 * a bar goes and when it should fade, and both are properties of the frame rather than of the
	 * player. The class is on one element in one component; the reach is as narrow as it looks.
	 */
	.stage :global(.player-bar) {
		position: absolute;
		inset: auto 0 0 0;
		z-index: 3;
	}

	/*
	 * Chrome that belongs to the WHOLE picture rather than to the strip along its bottom.
	 *
	 * The stage owns where a thing sits and when it fades. With "along the bottom" as its only
	 * answer, anything wanting an edge of the frame would anchor itself to a bar twenty pixels tall
	 * and land in the middle of it. It takes no pointer of its own; whatever is drawn
	 * inside it asks for one back.
	 */
	.stage :global(.player-edge) {
		position: absolute;
		inset: 0;
		z-index: 3;
		/*
		 * NEVER interactive, at any size, in any state, and no rule below may say otherwise.
		 *
		 * This layer is the whole picture. It is drawn AFTER the bar and shares its stacking level,
		 * so the instant it takes a pointer it is on top of every control on the bar and the bar
		 * stops answering at all: the scrubber, play, the volume and the drawer all still highlight
		 * on hover, because hover is decided by the same hit test that has already been won by a
		 * transparent sheet above them. It reads as the bar having been switched off.
		 *
		 * Reveal rules that toggled `pointer-events` here alongside `opacity` would do exactly that,
		 * being more specific than this. So the fade below moves the CHILDREN's pointers and never
		 * this one.
		 */
		pointer-events: none;
	}

	/* The layer itself is transparent to the pointer: it covers the whole picture, and a picture
	   that cannot be clicked is not one anybody can play. What is drawn IN it takes one back. */
	.stage :global(.player-edge) > :global(*) {
		pointer-events: auto;
	}

	/*
	 * The line along the bottom that says how far through the clip is.
	 *
	 * The other half of the bar's fade, and it belongs here for the same reason the bar's position
	 * does: the frame is what knows whether the controls are showing. It is drawn whenever they are
	 * not, so there is never a moment with neither, and never a moment with both, which would put
	 * two progress indicators two pixels apart.
	 *
	 * Underneath the bar in the stacking order, so the two cross over cleanly during the fade.
	 */
	.stage :global(.player-progress) {
		position: absolute;
		/* ON the bottom edge. Its ends are its own: the line reads `--frame-corner` above and stops
		   where it meets each curve (see its rule in `Player.svelte`). The corner panel places it
		   the same way. */
		inset-block-end: 0;
		z-index: 2;
		opacity: 1;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* Fullscreen: the bar is up until the pointer goes idle, so the line waits for that. */
	.stage:fullscreen:not(.resting) :global(.player-progress),
	.stage.fullscreen:not(.resting) :global(.player-progress) {
		opacity: 0;
	}

	@media (hover: hover) {
		/* In a window the bar comes up when somebody looks at the picture, and the line gives way
		   to it: the same two conditions the bar itself is shown on, negated. */
		.stage:not(.fullscreen):not(.resting).pointed :global(.player-progress),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-progress) {
			opacity: 0;
		}
	}

	@media (hover: none) {
		/* No pointer to go idle, so the bar never hides and the line is never the only thing left. */
		.stage:not(.fullscreen) :global(.player-progress) {
			opacity: 0;
		}
	}

	/*
	 * A still's one control, in the corner rather than on a bar.
	 *
	 * The other half of the same contract, and it exists because a photograph is not a video with
	 * fewer buttons. The player's bar is a full-width strip because it holds a scrubber, a volume
	 * slider and a clock; a picture has a single button, and reusing the strip for it would lay a
	 * permanent translucent band across the bottom of every image in the library.
	 *
	 * Hidden until somebody is looking at the picture, which is the difference from the bar. A video
	 * needs its controls the moment it is on screen; a photograph does not need anything at all
	 * until you reach for it, and a picture with nothing on top of it is the whole point of a viewer.
	 */
	.stage :global(.still-control) {
		position: absolute;
		inset: auto var(--space-3) var(--space-3) auto;
		z-index: 3;
		opacity: 0;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* Inset by more than the frame's corner radius, or the round corner clips it, and a clipped
	   region cannot be hovered, so the thing it exists to explain becomes unreadable. */
	.notice {
		position: absolute;
		inset-block-start: var(--space-5);
		inset-inline-end: var(--space-5);
		z-index: 2;
		opacity: 0;
		pointer-events: none;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* Up on the same wake as the bar, and interactive only while it is up: an invisible thing that
	   still swallows a pointer is the same fault the bar below documents. */
	.notice.up {
		opacity: 1;
		pointer-events: auto;
	}

	/* Keyboard focus as well as hover: reaching it with a keyboard has to make it appear, or the focus
	   ring is drawn on something invisible. `:has(:focus-visible)` rather than `:focus-within` for the
	   reason given at the player bar below: the latter matches a mouse click and pins it open. */
	.stage.pointed :global(.still-control),
	.stage:has(:focus-visible) :global(.still-control) {
		opacity: 1;
	}

	/*
	 * The player's bar, in a window: there when somebody is looking at the picture, gone when not,
	 * and gone as well once the pointer has rested on the picture without moving, on the same idle
	 * clock fullscreen uses. Moving the pointer or resting it on the bar brings it back. A
	 * permanent strip of chrome would cover the bottom of every video in the one view whose job is
	 * showing the frame; hover answers "is somebody looking", and the idle clock "are they still
	 * doing anything".
	 *
	 * `:not(.resting)` on the hover line lets the clock win: while the pointer sits still on the
	 * stage `:hover` stays true, and would otherwise hold the bar up against the timer. The
	 * focus-visible line is un-guarded on purpose: a keyboard user is not moving a pointer, so the
	 * clock must never take the bar from them.
	 *
	 * `pointer-events` goes with the opacity: an invisible bar that is still clickable would
	 * swallow the double-click meant for the video, which fills the screen. It cannot lock anybody
	 * out: the bar is inside the stage, so a pointer over it is over the stage and already
	 * hovering.
	 *
	 * Only where hovering is possible. On a touch screen a bar waiting to be hovered would never
	 * appear and the controls would be unreachable, so there it stays up (see `wake`).
	 */
	@media (hover: hover) {
		.stage:not(.fullscreen) :global(.player-bar) {
			opacity: 0;
			pointer-events: none;
		}

		/* The edge fades as one and its pointers are taken from the CHILDREN, never from the layer.
		   See the note where the layer is declared. Anything else here puts a transparent sheet over
		   the bar. */
		.stage:not(.fullscreen) :global(.player-edge) {
			opacity: 0;
		}

		.stage:not(.fullscreen) :global(.player-edge) > :global(*) {
			pointer-events: none;
		}

		/*
		 * `:has(:focus-visible)`, not `:focus-within`. `:focus-within` matches a plain mouse click,
		 * so pressing pause would keep the bar up after the pointer left the picture, until
		 * something else was clicked. `:focus-visible` is the browser's answer to "is this person
		 * navigating by keyboard", false for a click, so the bar follows the pointer for a mouse
		 * and stays for a keyboard.
		 */
		.stage:not(.fullscreen):not(.resting).pointed :global(.player-bar),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-bar) {
			opacity: 1;
			pointer-events: auto;
		}

		.stage:not(.fullscreen):not(.resting).pointed :global(.player-edge),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-edge) {
			opacity: 1;
		}

		.stage:not(.fullscreen):not(.resting).pointed :global(.player-edge) > :global(*),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-edge) > :global(*) {
			pointer-events: auto;
		}
	}

	/* Fullscreen: the same element, taking the screen. */
	.stage:fullscreen,
	.stage.fullscreen {
		block-size: 100%;
		inline-size: 100%;
		border-radius: 0;
	}

	/*
	 * The bar rounds its own bottom corners to match the frame: its backdrop-filter escapes the
	 * frame's rounded clip, so it has to clip itself (see Player). Fullscreen has no rounded frame,
	 * so the bar goes square.
	 *
	 * Both selectors, the same pair the frame carries above. `:fullscreen` is the browser's own
	 * answer and cannot lag; the class is this component's, and applies while a test or a nested
	 * element stands in for it. With only one, the frame could be square while the bar stayed
	 * rounded, leaving a line of the frame's ground under the timeline.
	 */
	.stage:fullscreen :global(.player-bar),
	.stage.fullscreen :global(.player-bar) {
		border-radius: 0;
	}

	/* Out of the way, but still there.
	 *
	 * Faded rather than removed, and `pointer-events: none` with it: a bar that is invisible and
	 * still clickable swallows the click meant for the picture underneath, and one that is removed
	 * from the layout makes the video jump every time it hides. */
	/* After the hover rule above, deliberately: in fullscreen the pointer is over the stage whether
	   or not it has moved, so `:hover` is true the whole time and resting has to be the one that
	   wins. Same specificity, so it is source order that settles it. */
	.stage.resting :global(.player-bar),
	.stage.resting :global(.still-control) {
		opacity: 0;
		pointer-events: none;
	}

	/* Same split as above: the layer fades, the children give up their pointers, and the layer's own
	   `pointer-events: none` is never contradicted. */
	.stage.resting :global(.player-edge) {
		opacity: 0;
	}

	.stage.resting :global(.player-edge) > :global(*) {
		pointer-events: none;
	}

	.stage.resting {
		cursor: none;
	}

	/* The picture carries its own `cursor: pointer`, which overrides the stage's `none` by being the
	   more specific rule, so without this the pointer would stay lit over a resting frame while the
	   bar and the cursor everywhere else had gone. Hide it with them. */
	.stage.resting :global(:is(video, img, canvas)) {
		cursor: none;
	}

	.stage :global(.player-bar),
	.stage :global(.player-edge) {
		transition: opacity var(--dur-fast) var(--ease);
	}

	.stage :global(.player-bar),
	.stage :global(.player-edge),
	.stage :global(.player-progress),
	:global(:root[data-motion='reduce']) .stage :global(.still-control) {
		transition: none;
	}
</style>
