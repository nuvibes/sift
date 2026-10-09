<script lang="ts" module>
	/* The tile the last press landed on, for the phone viewer to grow out of, kept for a moment. */
	let pressed: { tile: Element; at: number } | null = null;

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

	function takePressedTile(): Element | null {
		const was = pressed;
		pressed = null;
		return was !== null && performance.now() - was.at < PRESS_OPENS_MS ? was.tile : null;
	}
</script>

<script lang="ts">
	import Scroller from '$lib/components/common/Scroller.svelte';
	/* An asset opened over the grid: the veil, the focus, Escape, and closing back in history. */
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
		startAt?: number | null;
		playUntil?: number | null;
	}

	let { id, onclose, startAt = null, playUntil = null }: Props = $props();

	let dialog = $state<HTMLElement | null>(null);

	let leftFrom: string | null = null;

	/* FINISHED WITH: Escape, the veil, the last file deleted (`dismissingAsset`). */
	function dismiss(): void {
		/* Once per file: a double press on the veil would step back twice. */
		if (leftFrom === id) return;
		leftFrom = id;
		dismissingAsset();
		onclose();
	}

	/*
	 * Asked of `asset-view`, which the corner player reads too; empty when not opened from a list.
	 */
	const forward = $derived(canStepForward(id));
	const back = $derived(canStepBack(id));
	/* Answerable without a request, since it decides whether the player gets an advance handler. */
	const playOn = $derived(runGoesOn(id, { pictures: dwell.pictures }));

	async function nextInRun(): Promise<string> {
		return (await playOnFrom(id, endRule())) ?? id;
	}

	function endRule() {
		return { pictures: dwell.pictures, wraps: !dwell.stopsAtTheEnd };
	}

	function started(): void {
		if (playOn && run.movesOnAfter(dwell.mode)) lookAhead(id, endRule());
	}
	onDestroy(() => {
		if (!toCorner) dropAhead();
	});

	let reachedByRun = $state<string | null>(null);

	function playedThrough(): void {
		void nextInRun().then((to) => {
			reachedByRun = to;
			showAsset(to);
		});
	}

	/* For the dialog's name. */
	let named = $state<string | null>(null);

	/*
	 * A phone's viewer grows out of the pressed tile and back into it while that tile is on screen.
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

	let toCorner = false;
	$effect(() => {
		if (mini.handover) toCorner = true;
	});

	function comes(node: Element) {
		return phoneWidth.yes
			? fromPlace(node, { from: () => (id === openedOn ? tilePlace() : null) })
			: stageTransition(node, {
					from: takePlace,
					leave: () => (toCorner ? 'fade' : 'shrink')
				});
	}

	/* A phone's step arrives from the side it came from (`stepArrival`). */
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
		// Wanted before the first clip in a run ends.
		void dwell.load();
		const returnTo = document.activeElement as HTMLElement | null;
		// Only a tile reached by keyboard gets its focus ring back.
		const openedByKeyboard = returnTo?.matches?.(':focus-visible') ?? false;
		dialog?.focus();
		return () => {
			if (openedByKeyboard) returnTo?.focus?.();
		};
	});

	function trap(event: KeyboardEvent) {
		if (event.key !== 'Tab' || !dialog) return;
		const focusable = dialog.querySelectorAll<HTMLElement>(
			'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'
		);
		if (focusable.length === 0) {
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
	 * The open file was deleted: forward, back, or out (`stepAwayFrom`, read before the row drops).
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
			// A sheet over the viewer answers Escape itself (`keystrokeIsUnanswered`).
			if (!keystrokeIsUnanswered(event)) return;
			dismiss();
			return;
		}
		trap(event);
	}
</script>

<svelte:window onkeydown={onKeydown} />

<!-- The veil is a button, so clicking away closes and it is announced as dismissable. -->
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
	<!-- NOT keyed on the id: replacing the element would end fullscreen on Next. -->
	{#if phoneWidth.yes}
		<div class="head">
			<Tooltip label={ACTS.close}>
				<Button tone="ghost" icon="arrow_back" aria-label={ACTS.close} onclick={dismiss} />
			</Tooltip>
			<p class="title">{named ?? ''}</p>
		</div>
	{/if}
	<Scroller>
		<!-- A gutter, since `Scroller`'s bar floats over the content. -->
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
	.body {
		padding-inline-end: var(--sheet-gutter);
	}

	.sheet {
		position: fixed;
		z-index: var(--z-asset-sheet);
		/*
		 * Level with the line under the top bar, below the desktop title strip (`--window-chrome`).
		 */
		--sheet-top: calc(var(--window-chrome) + var(--space-2) + var(--topbar-height) + 1px);
		inset: var(--sheet-top) 50% auto auto;
		translate: 50% 0;
		--sheet-width: min(1125px, 94vw);
		width: var(--sheet-width);
		max-height: calc(100dvh - var(--sheet-top) - var(--space-2));
		/*
		 * The stage is 16:9 of the width it gets, derived, or every video letterboxes; capped on a
		 * short window.
		 */
		--sheet-gutter: 10px;
		/* A whole pixel, so the rounded clip does not end inside one. */
		--stage-height: round(
			down,
			min(75vh, calc((var(--sheet-width) - 2 * var(--space-4) - var(--sheet-gutter)) * 9 / 16)),
			1px
		);
		/* One `1fr` row, so the region inside has a definite height. */
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		padding: var(--space-4);
		border-radius: var(--radius-xl);
		background: var(--sheet-ground, var(--sift-surface-2));
		border: 1px solid var(--sift-line);
		box-shadow: var(--elev-3);
	}

	/* No ring on the sheet itself: any key would light one around the whole dialog. */
	.sheet:focus-visible {
		box-shadow: var(--elev-3);
	}

	/* A phone: the whole screen, inside the safe areas; the stage sized from the window. */
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

	/* On the arc: i = r * (1 - 1/sqrt(2)), six pixels at r = 20. */
</style>
