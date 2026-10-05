<script lang="ts" module>
	/*
	 * The tile the last press landed on, for the viewer to grow out of on a phone (`fromPlace`).
	 *
	 * Heard on the way down, for the whole window, because the press that opens a file happens
	 * before this panel exists: the wall opens it, and the wall does not say which tile. Kept only
	 * for a moment (`PRESS_OPENS_MS`), so an address landed on or a file stepped to later never grows
	 * out of a tile pressed a minute ago.
	 */
	let pressed: { tile: Element; at: number } | null = null;

	/** How long after a press on a tile the viewer it opened may still grow out of it. */
	const PRESS_OPENS_MS = 1000;

	if (typeof window !== 'undefined') {
		window.addEventListener(
			'pointerdown',
			(event) => {
				const tile = event.target instanceof Element ? event.target.closest('.tile-frame') : null;
				pressed = tile === null ? null : { tile, at: performance.now() };
			},
			{ capture: true, passive: true }
		);
	}

	/** The tile just pressed, if it was pressed a moment ago. Taken, so it is used once. */
	function takePressedTile(): Element | null {
		const was = pressed;
		pressed = null;
		return was !== null && performance.now() - was.at < PRESS_OPENS_MS ? was.tile : null;
	}
</script>

<script lang="ts">
	import Scroller from '$lib/components/common/Scroller.svelte';
	/*
	 * An asset opened over the grid it was clicked in.
	 *
	 * Deliberately thin. What the asset looks like is `AssetView`'s, and it is shared with the page
	 * at the same address; what this owns is the frame: the veil, keeping focus inside while it is
	 * open, closing on Escape, and the fact that closing goes back in history rather than to a fixed
	 * address, so the grid reappears exactly where it was.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { keystrokeIsUnanswered } from '$lib/shell/layers';
	import { fromPlace } from '$lib/shell/motion.svelte';
	import { stageTransition, takePlace } from '$lib/components/player/motion';
	import { stepArrival, type SwipeWay } from '$lib/components/player/swipe';
	import Veil from '$lib/components/common/Veil.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Tooltip } from '$lib/components/common';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import AssetView, { type AssetDetail } from '$lib/components/AssetView.svelte';
	import {
		canStepBack,
		canStepForward,
		dismissingAsset,
		dropAhead,
		lookAhead,
		playOn as playOnFrom,
		runGoesOn,
		showAsset,
		showStranger,
		stepAwayFrom,
		stepBack,
		stepForward
	} from '$lib/player/asset-view';
	import { dwell } from '$lib/player/dwell.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { run } from '$lib/player/run.svelte';
	import { ACTS } from '$lib/player/acts';

	interface Props {
		id: string;
		onclose: () => void;
		/** A moment to open at, when this was opened from something that knows one. */
		startAt?: number | null;
		/** Where the stretch being opened ENDS, for a saved loop. See `AssetView.playUntil`. */
		playUntil?: number | null;
	}

	let { id, onclose, startAt = null, playUntil = null }: Props = $props();

	let dialog = $state<HTMLElement | null>(null);

	/** The file this panel was last told to leave from. See `dismiss`. */
	let leftFrom: string | null = null;

	/*
	 * FINISHED WITH, rather than left behind for another page.
	 *
	 * The two are indistinguishable from the navigation that follows (the address loses its asset
	 * state either way) and one behaviour turns on the difference: a clip carries on in the mini
	 * player when somebody follows a chip out of here, and stops when they have finished with it.
	 * Every way OUT of this panel that means "finished" comes through here: Escape, the veil, and
	 * the last file in the list being deleted. Everything else that leaves is somebody going
	 * somewhere, which is exactly what is not said here.
	 *
	 * Said rather than worked out: see `dismissingAsset` for why reading the navigation's own type
	 * is right most of the time and silently wrong for a panel that was opened cold.
	 */
	function dismiss(): void {
		/* Once per file shown. Leaving is a step back in history, and the panel stays drawn until
		   that step lands, so a second press on the veil (a double click, an impatient one) would
		   step back AGAIN, off the screen the panel was opened over. Keyed on the file rather than a
		   flag, so a Back that lands on another file's panel can still be closed. */
		if (leftFrom === id) return;
		leftFrom = id;
		dismissingAsset();
		onclose();
	}

	/*
	 * Where Next and Back go, and whether they are drawn at all.
	 *
	 * Asked, not worked out here: `asset-view` holds the one answer (the list's own neighbour in
	 * order, or one fixed shuffled order, `Walk`, with Shuffle on) and the corner player reads the
	 * same one. So Back is offered whenever the run can step back, which with Shuffle on is the
	 * file just watched.
	 *
	 * Empty when the modal was not opened from a list; the buttons dim.
	 */
	const forward = $derived(canStepForward(id));
	const back = $derived(canStepBack(id));
	/*
	 * Whether a run goes on from here at all, a different question from where it goes.
	 *
	 * Whether has to be answerable without a request, because it decides whether the advance
	 * handler is passed to the player. Where may be a page away, and is `nextInRun` below. As one
	 * question answered from the loaded page, "Play through" would stop at the bottom of every
	 * page.
	 */
	const playOn = $derived(runGoesOn(id, { pictures: dwell.pictures }));

	/** Where the run goes at the end, by the walk Next moves; the same file when nowhere else. */
	async function nextInRun(): Promise<string> {
		return (await playOnFrom(id, endRule())) ?? id;
	}

	function endRule() {
		return { pictures: dwell.pictures, wraps: !dwell.stopsAtTheEnd };
	}

	/* The next file is found while this one plays, so its end waits only on the media. */
	function started(): void {
		if (playOn && run.movesOnAfter(dwell.mode)) lookAhead(id, endRule());
	}
	onDestroy(() => {
		if (!toCorner) dropAhead();
	});

	/* The file the run itself brought up, the one a picture rests on; a press clears it. */
	let reachedByRun = $state<string | null>(null);

	function playedThrough(): void {
		void nextInRun().then((to) => {
			reachedByRun = to;
			showAsset(to);
		});
	}

	/* Only for the dialog's name. A screen reader announcing "dialog" and nothing else leaves
	 * somebody who cannot see the picture with no idea what opened. */
	let named = $state<string | null>(null);

	/*
	 * On a phone the viewer is the whole screen rather than a dialog over the page: nothing is left
	 * beside it to press, so the way out is a control of its own at the top, beside the file's name.
	 * The width is the shell's one reading of it, `phoneWidth`.
	 */

	/*
	 * ON A PHONE THE VIEWER GROWS OUT OF THE TILE and shrinks back into it.
	 *
	 * The tile is the one pressed as this opened. Closing goes back to the same tile while the viewer
	 * still shows the file it opened on and the tile is still on the screen; after a step to another
	 * file, or with the tile scrolled away, there is nowhere honest to shrink to, and the viewer
	 * shrinks a little where it is as it fades. On a desktop it moves as every player's screen does
	 * (`stageTransition`), shrinking back as it leaves except on its way down to the corner.
	 */
	const openedOn = untrack(() => id);
	const tile = takePressedTile();

	function tilePlace(): DOMRect | null {
		if (tile === null || !tile.isConnected) return null;
		const place = tile.getBoundingClientRect();
		const onScreen =
			place.width > 0 && place.bottom > 0 && place.top < window.innerHeight && place.right > 0;
		return onScreen ? place : null;
	}

	/* Noted as it happens: the corner clears its hand-over in the same moment this starts to leave. */
	let toCorner = false;
	$effect(() => {
		if (mini.handover) toCorner = true;
	});

	function comes(node: Element) {
		/* `takePlace` is asked only by an ARRIVAL: the viewer leaving as a file goes down to the corner
		   must not take the place that file is handed on with. */
		return phoneWidth.yes
			? fromPlace(node, { from: () => (id === openedOn ? tilePlace() : null) })
			: stageTransition(node, {
					from: takePlace,
					leave: () => (toCorner ? 'fade' : 'shrink')
				});
	}

	/*
	 * A step through the run, and which way it went, until the file it went to is on the screen: the
	 * new file arrives from the side the step came from (`stepArrival`). A phone only; the keys and
	 * the buttons on a desktop keep the picture where it is.
	 */
	let stepping: { to: string; way: SwipeWay } | null = null;

	function stepped(to: string | null, way: SwipeWay): void {
		if (to === null) return;
		stepping = phoneWidth.yes ? { to, way } : null;
		reachedByRun = null;
		showAsset(to);
	}

	function loaded(asset: AssetDetail): void {
		named = asset.filename ?? asset.original_filename ?? null;
		if (stepping === null || stepping.to !== asset.id) return;
		const { way } = stepping;
		stepping = null;
		void stepArrival(dialog, way);
	}

	onMount(() => {
		// Whether a run stops on photographs is an account preference, and the answer is wanted
		// before the first clip in a run reaches its end rather than after it.
		void dwell.load();
		// Focus moves into the dialog, or a keyboard user is left operating the grid behind it.
		const returnTo = document.activeElement as HTMLElement | null;
		// Was the tile reached by keyboard? Only then does putting a focus ring back on it help. A
		// tile clicked with a mouse does not match `:focus-visible`, and lighting its ring on close
		// would leave it looking like a stuck selection.
		const openedByKeyboard = returnTo?.matches?.(':focus-visible') ?? false;
		dialog?.focus();
		// Back to the tile for a keyboard user, so they are not dropped at the top of the page
		// every time they close something. Only for somebody who arrived by keyboard: closing with
		// Escape is not a statement that a mouse user is now on the keyboard, and focusing the tile
		// then would light it up as though pointed at.
		return () => {
			if (openedByKeyboard) returnTo?.focus?.();
		};
	});

	/** Keep Tab inside the dialog while it is open. */
	function trap(event: KeyboardEvent) {
		if (event.key !== 'Tab' || !dialog) return;
		const focusable = dialog.querySelectorAll<HTMLElement>(
			'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'
		);
		if (focusable.length === 0) {
			// Nothing inside to land on, so the dialog itself holds it rather than letting Tab walk
			// out into the grid underneath.
			event.preventDefault();
			dialog.focus();
			return;
		}
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		const active = document.activeElement;

		if (event.shiftKey && (active === first || active === dialog)) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && active === last) {
			event.preventDefault();
			first.focus();
		}
	}

	/**
	 * The open file has been deleted: step on, or leave.
	 *
	 * Delete is on the popout's own menu, so the file this dialog is built around can go while
	 * somebody is looking at it. The frame answers the way a wall does when a row is taken off it:
	 * carry on with what is next to it.
	 *
	 * `stepAwayFrom` reads where to go before the row is dropped, or the list would answer about a
	 * gap; with Shuffle on it steps through the same walk. Forward first, then backwards, then out
	 * when this was the only file. Forgetting the file stops Back walking onto it; the wall behind
	 * is told separately, since the delete rings `libraryChanges`.
	 */
	async function gone(deleted: string) {
		const to = await stepAwayFrom(deleted);
		if (to === null) {
			dismiss();
			return;
		}
		reachedByRun = null;
		showAsset(to);
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			// Only when nothing above has answered it. A sheet drawn over the viewer (the confirm
			// before a file is deleted, the share sheet, a menu) answers Escape itself, and this
			// would otherwise close the viewer underneath it too. See `keystrokeIsUnanswered`.
			if (!keystrokeIsUnanswered(event)) return;
			dismiss();
			return;
		}
		trap(event);
	}
</script>

<svelte:window onkeydown={onKeydown} />

<!--
	The viewer arrives and leaves, rather than being there and then not: it is the one surface that
	takes the whole screen.

	It grows rather than rising: a slightly smaller version of itself expanding into place, and the
	reverse on the way out, saying it came out of what was pressed rather than from off the edge of
	the screen. On a phone it grows out of the tile itself (`comes`).

	Both are declared on the elements, because a transition is the only mechanism that can animate
	something leaving: by the time a script could, the element is gone.

	The veil is a button so that clicking away closes, and so it is announced as something that can
	be dismissed rather than as decoration.
-->
<Veil label={ACTS.close} layer="asset" onclose={dismiss} />

<div
	class="sheet"
	class:fills={phoneWidth.yes}
	role="dialog"
	aria-modal="true"
	aria-label={named ?? 'Asset'}
	tabindex="-1"
	bind:this={dialog}
	transition:comes
>
	<!--
		Deliberately NOT keyed on the id.

		Keying it looks right (moving to the next clip rebuilds the view, so nothing of the last one
		can linger) and it breaks the one case it matters in. A browser draws the fullscreened
		ELEMENT and nothing else, so replacing that element ends fullscreen: pressing Next while
		watching full screen would drop straight back to a window, every time. The view follows the id
		instead, and the player swaps its source without being torn down.
	-->
	{#if phoneWidth.yes}
		<!-- The way out first, where a phone's back control sits, then the name of what is showing. -->
		<div class="head">
			<Tooltip label={ACTS.close}>
				<Button tone="ghost" icon="arrow_back" aria-label={ACTS.close} onclick={dismiss} />
			</Tooltip>
			<p class="title">{named ?? ''}</p>
		</div>
	{/if}
	<Scroller>
		<!--
			A gutter for the scrollbar, so it is not drawn on top of the file.

			`Scroller`'s bar floats over the content rather than taking part in layout, so a
			scrolling box is not narrower inside; other screens have the frame's padding under it.
			This stage runs to both edges of the content box, so without a gutter the bar sits on
			the picture's edge and over the end of the control row. The width is the bar's own,
			named rather than typed twice, because it must agree with `Scroller`.
		-->
		<div class="body">
			<AssetView
				{id}
				{startAt}
				{playUntil}
				onloaded={loaded}
				onnext={forward ? () => void stepForward(id).then((to) => stepped(to, 'next')) : undefined}
				onprevious={back ? () => stepped(stepBack(id), 'previous') : undefined}
				onplayedthrough={playOn ? playedThrough : undefined}
				reachedByRun={reachedByRun === id}
				onstarted={started}
				onopen={(other, runs) => {
					reachedByRun = null;
					showStranger(other, id, runs);
				}}
				ongone={(deleted) => void gone(deleted)}
				onclose={dismiss}
			/>
		</div>
	</Scroller>
</div>

<style>
	/* The scrollbar's gutter. See the note beside the box. */
	.body {
		padding-inline-end: var(--sheet-gutter);
	}

	.sheet {
		position: fixed;
		z-index: var(--z-asset-sheet);
		/* Level with the line UNDER the search bar (the topbar's own bottom border) rather than
		   the content card's outer edge above it. That line is where the page itself starts; the
		   card's outer edge sits a whole topbar's height higher.

		   `--window-chrome` because this is `position: fixed` and the window is what it is fixed to.
		   Everything it is lining up with starts BELOW the desktop window's title strip, so without
		   it the packaged app would draw the dialog over the top bar it is meant to sit under.
		   Zero in a browser, so one line is right in both shells. */
		--sheet-top: calc(var(--window-chrome) + var(--space-2) + var(--topbar-height) + 1px);
		inset: var(--sheet-top) 50% auto auto;
		translate: 50% 0;
		/*
		 * 1125px wide, with the stage's own default height 650px below, grown together: widening
		 * the frame alone does nothing for a picture sitting inside it at `object-fit: contain`,
		 * only adding letterbox space beside it.
		 */
		--sheet-width: min(1125px, 94vw);
		width: var(--sheet-width);
		/* As tall as the window less a matching gap at the bottom, since the top is a fixed
		   distance from the viewport rather than a fraction of it. */
		max-height: calc(100dvh - var(--sheet-top) - var(--space-2));
		/*
		 * The stage is sixteen by nine of the width it will actually get, not a round number.
		 *
		 * A fixed height beside this width would give a box slightly squarer than 16:9, and
		 * `object-fit: contain` would then letterbox every video in the library, at every
		 * resolution, since 4K and 1080p are the same shape. Derived rather than a corrected
		 * constant, because the right height is a function of the width and the padding, and a
		 * constant matching them today comes apart silently when either moves. `--sheet-width`
		 * exists so the two cannot be changed apart.
		 *
		 * `9 / 16` rather than `/ (16 / 9)`: the same number, with no nested division to rely on.
		 *
		 * The cap still binds on a short window: a stage taller than the window is worse than a
		 * letterbox. A file that is not 16:9 still letterboxes, deliberately: the frame is a fixed
		 * shape so clicking through a grid does not resize the dialog under the pointer. See the
		 * note at the top of `MediaStage`.
		 */
		--sheet-gutter: 10px;
		/* Down to a whole pixel, so the stage's rounded clip does not end inside one. */
		--stage-height: round(
			down,
			min(75vh, calc((var(--sheet-width) - 2 * var(--space-4) - var(--sheet-gutter)) * 9 / 16)),
			1px
		);
		/* A grid with one `1fr` row, so the scrolling region inside resolves to a definite height.
		   With the cap alone the region would lay out at its content height and overflow the box that
		   said it may not grow. */
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		/*
		 * Even on all sides, with no band for a close button: the ways out are Escape and clicking
		 * beside it, as everything else in the app closes, which does not cost the picture forty
		 * pixels of height.
		 */
		padding: var(--space-4);
		border-radius: var(--radius-xl);
		background: var(--sheet-ground, var(--sift-surface-2));
		border: 1px solid var(--sift-line);
		box-shadow: var(--elev-3);
	}

	/*
	 * The sheet holds the focus and never draws a ring for it.
	 *
	 * Focus is put here when the dialog opens, so that the keyboard is inside it rather than on the
	 * grid behind. That is not somebody arriving at a control, and the browser cannot tell the
	 * difference: pressing any key marks whatever holds the focus as keyboard-reached, so muting,
	 * seeking or stepping to the next file would light an accent ring around the entire dialog. The ring
	 * belongs on the control somebody has walked to, and every control in here still draws its own.
	 *
	 * Its own shadow is restated because the app-wide focus rule replaces it.
	 */
	.sheet:focus-visible {
		box-shadow: var(--elev-3);
	}

	/*
	 * A phone: the whole screen, edge to edge, inside the areas the device keeps for itself.
	 *
	 * A strip at the top for the way out and the name, the picture filling what is left above the
	 * file's bar, which is the same height as the strip. The stage is square-cornered because it
	 * meets the edges of the screen, and it is sized from the window because it is the picture
	 * (the one case a component may measure against the window).
	 */
	.sheet.fills {
		inset: 0;
		translate: none;
		width: auto;
		max-height: none;
		grid-template-rows: var(--topbar-height) minmax(0, 1fr);
		padding: var(--safe-top) var(--safe-right) var(--safe-bottom) var(--safe-left);
		border: 0;
		border-radius: 0;
		box-shadow: none;
		/* The page's own ground, so the strip, the picture and the bar are one surface. */
		--sheet-ground: var(--sift-bg);
		--stage-radius: 0;
		--stage-height: var(--phone-stage-height);
	}

	.sheet.fills:focus-visible {
		box-shadow: none;
	}

	.sheet.fills .body {
		padding-inline-end: 0;
	}

	.head :global(button) {
		min-inline-size: var(--touch-target);
		min-block-size: var(--touch-target);
	}

	.head {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
		padding-inline: var(--space-2);
	}

	/* One line: a long name ends in an ellipsis rather than pushing the strip taller. */
	.title {
		flex: 1 1 0;
		min-inline-size: 0;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font: var(--text-body);
		color: var(--sift-ink);
	}

	/*
	 * Sat on the corner, rather than merely inset equally from both edges.
	 *
	 * The corner is a 20px arc, and a square with equal margins from two edges leaves an uneven
	 * wedge of curve beside it. For the button's outer corner to ride on the arc its distance from
	 * the arc's centre must equal the radius: with the centre at (r, r), sqrt(2) * (r - i) = r, so
	 * i = r * (1 - 1/sqrt(2)) = 5.86 at r = 20. Six pixels whatever the button's size, which only
	 * decides how far its middle sits from the corner.
	 */
</style>
