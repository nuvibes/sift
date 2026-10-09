<script lang="ts">
	/* DRESSED BY: .player-bar. MediaStage positions and fades the bar it renders. Where the bar SITS
	   on the picture, and whether it is visible, are the stage's business rather than the bar's. */

	/*
	 * The bar along the bottom of a picture, for the player and every cell: scrub line on top, then
	 * the row; every press always drawn, dimmed when it cannot act.
	 */
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import type { Snippet } from 'svelte';
	import { onDestroy } from 'svelte';
	import { getStage } from './stage.svelte';
	import { Button, Slider } from '$lib/components/common';
	import Panel from '$lib/components/common/Panel.svelte';
	import { keepFocusOnPress } from '$lib/components/common/menu-touch';
	import ScrubLine from './ScrubLine.svelte';
	import Transport, { type RepeatControl, type ShuffleControl } from './Transport.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { finger } from './finger.svelte';
	import type { SpriteSheet } from '$lib/player/trickplay';
	import { ACTS, keyOf, type Keyboard } from '$lib/player/acts';

	interface Props {
		/* --- the scrubber */
		position: number;
		duration: number;
		sheet?: SpriteSheet | null;
		replays?: readonly number[] | null;
		sheetUrl?: string | null;
		pointA?: number | null;
		pointB?: number | null;
		onseek: (seconds: number) => void;
		onmark?: (which: 'a' | 'b', seconds: number) => void;
		scrubberLabel?: string;
		/**
		 * False for a picture; DIMMED, never removed, so the bar keeps its shape whatever is
		 * playing.
		 */
		seekable?: boolean;
		sound?: boolean;
		soundWhy?: string;

		/* --- the transport */
		playing: boolean;
		onplay: () => void;
		playable?: boolean;
		playWhy?: string;
		/**
		 * False for a photograph; apart from `seekable` and `playable`, which a cell's still and a
		 * held GIF split.
		 */
		timed?: boolean;
		onback?: () => void;
		onforward?: () => void;
		backLabel?: string;
		forwardLabel?: string;
		/** Absent on Theater's bar, whose drawer holds them. */
		shuffle?: ShuffleControl;
		repeat?: RepeatControl;
		/** For each tooltip's key (`keyOf`). */
		keyboard?: Keyboard;
		/** The theater's keeps the clocks' room, so the bar never changes width. */
		variant?: 'player' | 'theater';
		/** The transport is being POINTED AT; the caller draws what that means (a wall's wash). */
		onaim?: (aiming: boolean) => void;

		/* --- the sound */
		muted: boolean;
		volume: number;
		onmute: () => void;
		onvolume: (level: number) => void;

		compact?: boolean;

		/* --- what this caller has that the other does not */
		/** Drawn at the start of the transport row, told what the rest of it takes, so it folds first. */
		lead?: Snippet<[number]>;
		tray?: Snippet;
		trayLabel?: string;
		trayWhy?: string;
		trailing?: Snippet;
		below?: Snippet;

		/** The frame must be told, or it fades from under the hand. */
		onhold?: (event?: PointerEvent) => void;
		onrelease?: () => void;
	}

	let {
		position,
		duration,
		sheet = null,
		replays = null,
		sheetUrl = null,
		pointA = null,
		pointB = null,
		onseek,
		onmark,
		scrubberLabel = 'Position',
		seekable = true,
		sound = true,
		soundWhy = 'No sound in this',
		playing,
		onplay,
		playable = true,
		playWhy,
		timed = true,
		onback,
		onforward,
		backLabel = ACTS.previous,
		forwardLabel = ACTS.next,
		shuffle,
		repeat,
		keyboard = 'player',
		variant = 'player',
		onaim,
		muted,
		volume,
		onmute,
		onvolume,
		compact = false,
		lead,
		tray,
		trayLabel = 'This file',
		trayWhy = 'No more controls for this',
		trailing,
		below,
		onhold,
		onrelease
	}: Props = $props();

	/*
	 * The drawer opens under the pointer and on a press; a press shuts only what a press opened. A
	 * finger only presses.
	 */
	let trayOpen = $state(false);
	let trayPressed = false;
	let trayTimer: ReturnType<typeof setTimeout> | null = null;

	function pressTray() {
		if (trayOpen && trayPressed) {
			releaseTray();
			return;
		}
		holdTray();
		trayPressed = true;
	}

	function pointAtTray(event: PointerEvent) {
		if (event.pointerType !== 'touch') holdTray();
	}

	function pointAwayFromTray(event: PointerEvent) {
		if (event.pointerType !== 'touch') releaseTray();
	}

	function holdTray() {
		if (trayTimer) clearTimeout(trayTimer);
		trayTimer = null;
		trayOpen = true;
	}

	/* A transparent strip reaches down to the button, so the path never leaves the drawer. */
	function releaseTray() {
		// Not while a menu opened from inside it is up (`menuOpenIn`), asked of the page.
		if (menuOpenIn(root)) return;
		if (trayTimer) clearTimeout(trayTimer);
		trayTimer = null;
		trayOpen = false;
		trayPressed = false;
	}

	/* Every menu trigger says `aria-expanded`, so any menu in the drawer holds it. */
	function menuOpenIn(bar: HTMLElement | null): boolean {
		return bar?.querySelector('[aria-expanded="true"]:not([data-drawer-toggle])') != null;
	}

	let root = $state<HTMLElement | null>(null);
	let menuUp = $state(false);
	const stage = getStage();

	$effect(() => {
		const bar = root;
		if (!bar) return;
		const read = () => {
			menuUp = menuOpenIn(bar);
		};
		const watcher = new MutationObserver(read);
		watcher.observe(bar, {
			subtree: true,
			childList: true,
			attributes: true,
			attributeFilter: ['aria-expanded']
		});
		read();
		return () => watcher.disconnect();
	});

	/* Not released once the bar has gone, or the stage wakes it again. */
	let heldByMenu = false;
	$effect(() => {
		if (menuUp === heldByMenu) return;
		heldByMenu = menuUp;
		if (menuUp) onhold?.();
		else if (stage?.showing !== false) onrelease?.();
	});

	/* The pointer on a menu the bar opened is the pointer on the bar. */
	$effect(() => {
		if (!menuUp) return;
		let onMenu = false;
		const follow = (event: PointerEvent) => {
			const target = event.target instanceof Element ? event.target : null;
			if (target && root?.contains(target)) {
				onMenu = false;
				return;
			}
			if (target?.closest('[data-bits-floating-content-wrapper]')) {
				onMenu = true;
				onhold?.();
			} else if (onMenu) {
				onMenu = false;
				onrelease?.();
			}
		};
		document.addEventListener('pointermove', follow);
		return () => document.removeEventListener('pointermove', follow);
	});

	/* No focus on a mouse press, so no ring; Tab still shows it. Buttons only, never a finger's. */
	function keepFocusOff(event: PointerEvent) {
		if ((event.target as HTMLElement | null)?.closest('button')) keepFocusOnPress(event);
	}

	/* One object for the aim handlers, empty when the caller wants none. */
	const aims = $derived(
		onaim
			? {
					onmouseenter: () => onaim(true),
					onmouseleave: () => onaim(false),
					onfocus: () => onaim(true),
					onblur: () => onaim(false)
				}
			: {}
	);

	/* Escape shuts the drawer ONLY while it is open, on the window. */
	function shutOnEscape(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !trayOpen) return;
		event.preventDefault();
		releaseTray();
	}

	$effect(() => {
		window.addEventListener('keydown', shutOnEscape);
		return () => window.removeEventListener('keydown', shutOnEscape);
	});

	let tooWide = $state(false);
	let beside = $state(0);

	function measure(bar: HTMLElement): void {
		const row = bar.querySelector(':scope > .row') as HTMLElement;
		const parts = [...row.children] as HTMLElement[];
		const gaps = (parseFloat(getComputedStyle(row).columnGap) || 0) * Math.max(0, parts.length - 1);
		const wanted = parts.reduce((sum, part) => sum + part.scrollWidth, gaps);
		beside = wanted - (row.querySelector<HTMLElement>(':scope > .start')?.scrollWidth ?? 0);
		const style = getComputedStyle(bar);
		const room =
			bar.clientWidth -
			(parseFloat(style.paddingLeft) || 0) -
			(parseFloat(style.paddingRight) || 0);
		tooWide = wanted > room + 0.5;
	}

	$effect(() => {
		const bar = root;
		if (bar === null) return;
		const watch = new ResizeObserver(() => measure(bar));
		watch.observe(bar);
		for (const part of bar.querySelectorAll(':scope > .row, :scope > .row > *'))
			watch.observe(part);
		return () => watch.disconnect();
	});

	onDestroy(() => {
		if (trayTimer) clearTimeout(trayTimer);
	});
</script>

<!-- `player-bar` is how the stage finds it; held up under the pointer. -->
<div
	bind:this={root}
	class="bar player-bar"
	class:under-line={!compact && !phoneWidth.yes && !tooWide && (timed || variant === 'theater')}
	role="group"
	aria-label="Playback controls"
	onpointerenter={(event) => onhold?.(event)}
	onpointermove={(event) => onhold?.(event)}
	onpointerleave={() => onrelease?.()}
	onpointerdown={keepFocusOff}
>
	<!--
	The scrub line on its own row, the whole width, with the clock on it except on a narrow bar or a
	photograph.
	-->
	<ScrubLine
		{position}
		{duration}
		{sheet}
		{sheetUrl}
		{replays}
		{pointA}
		{pointB}
		{onseek}
		{onmark}
		disabled={!seekable}
		label={scrubberLabel}
		timed={!compact && timed}
		keepRoom={!compact && variant === 'theater'}
		keepHeight={!compact}
	/>

	<div
		class="row"
		class:phone={phoneWidth.yes}
		class:led={lead !== undefined}
		class:follows={lead !== undefined && variant === 'theater'}
	>
		{#if lead && variant !== 'theater'}
			<div class="side start">
				{@render lead(beside)}
			</div>
		{/if}

		<!-- Each control's key comes from the act table (`Transport`). -->
		<div class="middle">
			<Transport
				{playing}
				{onplay}
				{playable}
				{playWhy}
				{onback}
				{onforward}
				{backLabel}
				{forwardLabel}
				{shuffle}
				{repeat}
				steps={!(phoneWidth.yes && finger.yes)}
				{keyboard}
				{aims}
			/>
		</div>

		{#if lead && variant === 'theater'}
			<div class="side start">
				{@render lead(beside)}
			</div>
		{/if}

		<div class="side end">
			<!--
			Mute and level, one control: the level pops out of the speaker; dropped in a small
			panel.
			-->
			{#if !compact}
				<!-- `off` with no sound, every control refused. -->
				<div
					class="volume"
					class:off={!sound}
					role="group"
					aria-label="Volume"
					onpointerenter={(event) => onhold?.(event)}
					onpointermove={(event) => onhold?.(event)}
				>
					<!-- The key only while the control can act. -->
					<Tooltip
						label={sound ? (muted ? ACTS.unmute : ACTS.mute) : soundWhy}
						shortcut={sound ? keyOf(muted ? 'unmute' : 'mute', keyboard) : undefined}
					>
						<Button
							tone="ghost"
							icon={muted || volume === 0 || !sound ? 'volume_off' : 'volume_up'}
							aria-label={sound ? (muted ? ACTS.unmute : ACTS.mute) : soundWhy}
							disabled={!sound}
							onclick={onmute}
						/>
					</Tooltip>
					<!-- Hover and focus both. -->
					<div class="level-pop">
						<span class="reading" aria-hidden="true">{volume}%</span>
						<Slider
							class="level"
							vertical
							label="Volume"
							valueText="{volume}%"
							value={volume}
							disabled={!sound}
							oninput={onvolume}
						/>
					</div>
				</div>
			{/if}

			{#if tray}
				<div
					class="tray"
					role="group"
					aria-label={trayLabel}
					onpointerenter={(event) => {
						onhold?.(event);
						pointAtTray(event);
					}}
					onpointerleave={(event) => {
						onrelease?.();
						pointAwayFromTray(event);
					}}
				>
					<Button
						tone="ghost"
						icon="home_storage"
						aria-label="More controls"
						pressed={trayOpen}
						aria-expanded={trayOpen}
						data-drawer-toggle
						onclick={pressTray}
					/>

					{#if trayOpen}
						<!-- The bridge reaches down to the button. -->
						<div class="bridge" class:wide={variant === 'theater'}>
							<Panel inset="sm" corner="lg" elevated>{@render tray()}</Panel>
						</div>
					{/if}
				</div>
			{:else}
				<Tooltip label={trayWhy}>
					<Button tone="ghost" icon="home_storage" aria-label={trayWhy} disabled />
				</Tooltip>
			{/if}

			{@render trailing?.()}
		</div>
	</div>

	{@render below?.()}
</div>

<style>
	.bar {
		display: grid;
		gap: var(--space-1);
		padding: var(--space-2) var(--space-3);
		/* The one place backdrop-blur is allowed. */
		backdrop-filter: blur(var(--blur-glass));
		/* Faded in at the top: a hard scrim edge is a line across the picture. */
		background: linear-gradient(
			to top,
			var(--sift-scrim-strong) 55%,
			var(--sift-scrim) 80%,
			var(--sift-scrim-none)
		);
	}

	.bar.under-line {
		grid-template-columns: auto minmax(0, 1fr) auto;
		column-gap: var(--space-3);
	}

	.bar.under-line > :global(*) {
		grid-column: 1 / -1;
	}

	.bar.under-line > :global(.scrub-line) {
		grid-template-columns: subgrid;
	}

	.bar.under-line > .row {
		grid-column: 2;
		justify-self: center;
		inline-size: 100%;
		min-inline-size: max-content;
	}

	/* `minmax(0, 1fr)` lets the row shrink; a caller may set the columns. */
	.row {
		position: relative;
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		grid-template-areas: 'middle end';
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.row.led {
		grid-template-columns: auto auto minmax(0, 1fr);
		grid-template-areas: 'start middle end';
	}

	.row.led.follows {
		grid-template-areas: 'middle start end';
	}

	.row.phone {
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		grid-template-areas:
			'middle middle'
			'start end';
	}

	.side {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.side.start {
		grid-area: start;
	}

	.side.end {
		grid-area: end;
		justify-content: flex-end;
	}

	.middle {
		grid-area: middle;
		display: flex;
	}

	.row.phone .middle {
		justify-content: center;
	}

	.volume {
		position: relative;
		display: flex;
		align-items: center;
		flex: none;
	}

	.volume.off {
		opacity: var(--disabled-opacity);
	}

	.level-pop {
		position: absolute;
		inset-block-end: calc(100% + var(--space-1));
		inset-inline-start: 50%;
		translate: -50% 0;
		z-index: 4;
		display: grid;
		justify-items: center;
		gap: var(--space-2);
		padding: var(--space-3) var(--space-2);
		border-radius: var(--radius-md);
		background: var(--popover);
		box-shadow: var(--elev-2);
		opacity: 0;
		visibility: hidden;
		transition:
			opacity var(--dur-fast) var(--ease),
			visibility var(--dur-fast) var(--ease);
	}

	/* `:has(:focus-visible)`: `:focus-within` matches a click and pins the level open. */
	.volume:hover .level-pop,
	.volume:has(:focus-visible) .level-pop {
		opacity: 1;
		visibility: visible;
	}

	/* DRESSED BY: .level (`Slider` draws the track and the thumb; this file only says how tall the
	   column is, which is a fact about the popup it stands in). */
	.level-pop :global(.level) {
		/*
		 * Physical: a vertical writing mode swaps the axes, and `inline-size` would be the height.
		 */
		width: 22px;
		height: 104px;
	}

	.reading {
		font: var(--text-label);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	.tray {
		position: relative;
		display: flex;
		align-items: center;
		flex: none;
	}

	/* On the button's TRAILING edge, or a cell's frame clips the last icon. */
	.bridge {
		position: absolute;
		inset-block-end: 100%;
		inset-inline-end: 0;
		z-index: 6;
		padding-block-end: var(--space-2);
	}

	/* Three columns; a direct child of `.bridge`, so it reaches only this drawer. */
	.bridge > :global(.panel) {
		grid-template-columns: repeat(3, auto);
		gap: var(--space-1);
	}

	.bridge.wide > :global(.panel) {
		grid-template-columns: repeat(7, auto);
	}

	:global(:root[data-motion='reduce']) .level-pop {
		transition: none;
	}
</style>
