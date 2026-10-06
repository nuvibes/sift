<script lang="ts">
	/* DRESSED BY: .player-bar. MediaStage positions and fades the bar it renders. Where the bar SITS
	   on the picture, and whether it is visible, are the stage's business rather than the bar's. */

	/*
	 * The bar along the bottom of a picture, one for the player and every cell of a wall: the scrub
	 * line on top, and under it what the caller leads with, the transport, then the sound, the
	 * drawer and the ways out at the end. Every press is always drawn, dimmed when it cannot act.
	 * The drawer's contents are the caller's snippet, so every control in it is `Button`, which
	 * carries its own styles wherever it is rendered.
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
		/* --- the scrubber ---------------------------------------------------------------------- */
		position: number;
		duration: number;
		sheet?: SpriteSheet | null;
		/** This account's replay curve for the file, passed through to the timeline. */
		replays?: readonly number[] | null;
		sheetUrl?: string | null;
		pointA?: number | null;
		pointB?: number | null;
		onseek: (seconds: number) => void;
		onmark?: (which: 'a' | 'b', seconds: number) => void;
		/** What the scrubber is called, where more than one is on screen at a time. */
		scrubberLabel?: string;
		/**
		 * Whether the playhead can be moved at all.
		 *
		 * False for a picture, which has no timeline to move along: a GIF included, because
		 * it is drawn in an `<img>` and an `<img>` cannot say where it is or be sent anywhere.
		 *
		 * DIMMED rather than removed, which is the rule the tile-size slider on the top bar already
		 * follows: a control that vanishes between one file and the next is one people stop reaching
		 * for, because they cannot tell "not here" from "not there yet". It also keeps the bar exactly
		 * the same height and shape whatever is playing, which matters most on a wall where the file
		 * changes under it every few seconds.
		 */
		seekable?: boolean;
		/** Whether there is any sound to control, and why not: a photograph and a GIF have none. */
		sound?: boolean;
		soundWhy?: string;

		/* --- the transport --------------------------------------------------------------------- */
		playing: boolean;
		onplay: () => void;
		/** Whether there is anything to play: false dims Play with `playWhy` (see `Transport`). */
		playable?: boolean;
		playWhy?: string;
		/**
		 * Whether there is a clock worth reading.
		 *
		 * False for a photograph, which has no playhead and no length: the clock would read
		 * `0:00 / 0:00` under every picture, which is a readout saying nothing twice. Separate from `seekable`
		 * because a theater cell's still is not seekable and still has a countdown to show, and
		 * separate from `playable` because a driven GIF can be held on a frame while having no
		 * time to report.
		 */
		timed?: boolean;
		/** The file before this one and the one after; undefined dims the press. */
		onback?: () => void;
		onforward?: () => void;
		backLabel?: string;
		forwardLabel?: string;
		/** Repeat and Shuffle, either side of the step pair, because they decide what the steps do. */
		shuffle: ShuffleControl;
		repeat: RepeatControl;
		/** Whose keys this bar's player answers, so every tooltip on it shows the key the act table
		 *  gives that keyboard (`keyOf`), and none where it has none. */
		keyboard?: Keyboard;
		/**
		 * Whose bar this is. The theater's keeps the clocks' room with no clock in it: one bar drives
		 * every cell, and it must not change width as the chosen cell goes from a clip to a picture.
		 */
		variant?: 'player' | 'theater';
		/**
		 * The transport is being POINTED AT, so the caller can say what it is about to act on.
		 *
		 * A player's bar drives the one picture above it and needs none of this. A wall's bar drives
		 * whichever cell is chosen, out of up to nine on screen, and which one that is is a thing
		 * the bar cannot show, because the answer is somewhere else entirely. Theater already draws
		 * the mark: hovering the filter button washes the cell it would change. These five controls
		 * ask exactly the same question, so they report the same way and the caller draws the same
		 * answer.
		 *
		 * Reported rather than drawn here, and the distinction is the whole design: this component
		 * knows nothing about cells, walls or what a mark looks like. It says *the transport is being
		 * pointed at* and stops. What that means on screen is the caller's, which is why the ordinary
		 * player can leave it out and lose nothing.
		 *
		 * `true` on the way in (pointer or keyboard, because a control reached by tab is being
		 * pointed at just as much as one under a cursor) and `false` on the way out.
		 */
		onaim?: (aiming: boolean) => void;

		/* --- the sound ------------------------------------------------------------------------- */
		muted: boolean;
		volume: number;
		onmute: () => void;
		onvolume: (level: number) => void;

		/** A bar in a small panel: it drops the volume, and the caller the rest by not handing it over. */
		compact?: boolean;

		/* --- what this caller has that the other does not -------------------------------------- */
		/** Drawn at the start of the transport row, told what the rest of it takes, so it folds first. */
		lead?: Snippet<[number]>;
		/** Drawn inside the drawer. Dressed by this file. See the note above. */
		tray?: Snippet;
		/** What the drawer is called. A player's holds things about the FILE; a cell's about the cell. */
		trayLabel?: string;
		/** Why there is no drawer, said on its dimmed button. */
		trayWhy?: string;
		/** Drawn at the end of the row, outside the drawer. */
		trailing?: Snippet;
		/** A panel the caller opens from its own drawer item, drawn under the bar. */
		below?: Snippet;

		/** Hold the bar up: the frame has to be told, or it fades from under the hand reaching for it. */
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

	/* Whether the grid of extra controls is showing.
	 *
	 * Opens under the pointer and closes when it leaves, which is what makes it worth having: it
	 * costs nothing to look in. It also opens on a press, so it is reachable without a pointer.
	 *
	 * A press shuts only a drawer a press opened. Pointing at the button opens the drawer on the way
	 * to pressing it, so a press on a drawer the pointer opened keeps it open; the next press shuts
	 * it.
	 *
	 * A finger does not point. It is "over" the drawer only while it touches, and the browser says it
	 * left the moment it lifts, before the click that finishes the tap: read as pointing, every tap
	 * on an icon in the drawer would shut the drawer under the finger and the click would go to the
	 * picture behind it. A finger opens and shuts the drawer by pressing its button, and nothing else. */
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

	/* The pointer arriving at, and leaving, the drawer and its button: never a finger's. See above. */
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

	/* Closed the moment the pointer leaves.
	 *
	 * What would make it unreachable is not the speed but the GAP: the grid floats clear of its
	 * button, so the pointer travelling between them would be over neither. The grid reaches down to
	 * the button with a transparent strip, so that path never leaves the drawer at all. */
	function releaseTray() {
		// Not while a menu opened from inside it is up. See `menuOpenIn`. The drawer goes on the NEXT
		// time the pointer leaves it, which by then is a pointer that has really left.
		// Asked of the page rather than of `menuUp`, which the watcher sets a moment later.
		if (menuOpenIn(root)) return;
		if (trayTimer) clearTimeout(trayTimer);
		trayTimer = null;
		trayOpen = false;
		trayPressed = false;
	}

	/*
	 * Whether a menu or panel opened from a control on this bar is up.
	 *
	 * Such a panel is portalled out of the bar, so the pointer moving onto it reads here as the
	 * pointer having gone, and the drawer would take itself away and the panel with it. The bar
	 * answers this itself rather than being told per menu: every menu trigger says it is open
	 * with `aria-expanded`, so a menu added to any drawer holds it with nothing to register. The
	 * drawer's own button says the same about the drawer and is left out by its mark.
	 */
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

	/*
	 * An open menu holds the bar up; closing it gives the bar back its clock, unless the bar has
	 * already gone. The stage closes what the bar opened when the bar fades, and a release then
	 * would wake the stage and bring the bar straight back.
	 */
	let heldByMenu = false;
	$effect(() => {
		if (menuUp === heldByMenu) return;
		heldByMenu = menuUp;
		if (menuUp) onhold?.();
		else if (stage?.showing !== false) onrelease?.();
	});

	/*
	 * The pointer on a menu the bar opened is the pointer on the bar.
	 *
	 * The menu is portalled out of the bar and out of the stage, so the pointer moving onto it
	 * leaves both, and each starts the bar's clock: the bar, its drawer and the menu would fade
	 * out under a pointer resting on the menu. Any floating layer counts while a menu is up, since the
	 * menu is the one that opened; leaving it for anywhere but the bar gives the clock back.
	 * No event is passed on: the hold stops propagation, and the menu needs its own moves.
	 */
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

	/*
	 * A button on the bar is pressed, not moved to: refusing the default on the way down stops
	 * focus moving without stopping the click, so no accent ring lights after a mouse press. Tab
	 * still reaches every button, and one reached that way shows its ring.
	 *
	 * Only buttons: the scrubber and the volume slider are dragged, and a slider that cannot take
	 * the pointer cannot be dragged. Never a finger's press: see `keepFocusOnPress`.
	 */
	function keepFocusOff(event: PointerEvent) {
		if ((event.target as HTMLElement | null)?.closest('button')) keepFocusOnPress(event);
	}

	/*
	 * The four handlers that say a verb is being pointed at, written once and spread on each of them.
	 *
	 * Five controls times four handlers is twenty lines that have to stay identical, and the one that
	 * drifts is the control that lights a cell and never puts it out again. One object cannot drift.
	 *
	 * Empty when the caller wants none of it, so the ordinary player's bar carries no listeners, and
	 * a control added to this row is silent until somebody spreads this on it.
	 */
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

	/*
	 * Escape shuts the drawer, and ONLY while it is open.
	 *
	 * Guarded rather than swallowed: Escape also closes whatever this bar is drawn inside (the
	 * dialog around the player, the filled screen a wall is on) and a bar that took the key
	 * unconditionally would leave somebody pressing it and nothing happening.
	 *
	 * On the window rather than on the bar, because the drawer opens on hover and nothing in it has
	 * the focus at the moment somebody reaches for Escape.
	 */
	function shutOnEscape(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !trayOpen) return;
		event.preventDefault();
		releaseTray();
	}

	$effect(() => {
		window.addEventListener('keydown', shutOnEscape);
		return () => window.removeEventListener('keydown', shutOnEscape);
	});

	/* A row wider than the whole bar cannot stand under the timeline: it keeps the bar's own edges
	   and gives way inside them. Read from the parts' own widths, which the squeeze does not move. */
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

<!-- `player-bar` is how the stage finds this to place it and to fade it. Held up while the pointer
     is on it: a bar that fades out from under the hand reaching for it is the one way this can be
     worse than not hiding at all. -->
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
		The scrub line, on a row of its own at the top of the bar.

		On a row shared with the buttons, the controls would leave the timeline about a hundred and fifty
		pixels wide on a nine-hundred-pixel stage. Its own row gives it the whole width, and at the TOP
		the frame under the pointer rises clear of everything else on the bar.
		The clock sits on it: the time so far at its start, the length at its end. Gone on a narrow
		bar, where the timeline alone says how far through the clip is and sixty-odd pixels is the
		difference between a drawer that fits on the cell and one that does not; gone under a
		photograph, which has no playhead and no length (see `timed`).
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

	<!-- What the caller leads with, the transport, and at the end the sound, the drawer and the ways
	     out. At a phone's width the transport takes a centred row of its own above the two ends. -->
	<div class="row" class:phone={phoneWidth.yes} class:led={lead !== undefined}>
		{#if lead}
			<div class="side start">
				{@render lead(beside)}
			</div>
		{/if}

		<!--
			Every control here says what it is and which key does it: an id is looked up in the act
			table, so a key cannot drift out of step with the button that names it (`Transport`).
		-->
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

		<div class="side end">
			<!--
			Mute and level are one control because they are one question, and the level comes out of
			the speaker rather than sitting beside it permanently.

			Volume is set once and then left alone for an evening, so a slider always on show is a
			control taking room from the scrubber (the one thing on this bar reached for constantly)
			in exchange for something touched twice a night.

			The slider is a real range input, stood on its end with `writing-mode`: it is draggable,
			arrow-keyable and announced as a slider without any of that being written.
		-->
			<!-- Dropped in a small panel: a slider standing on its end inside a few hundred pixels covers
		     the video it belongs to. Mute is on the keyboard there instead. -->
			{#if !compact}
				<!-- `off` when there is nothing to hear: dimmed, and every control in it refused. See
			     `sound`, and the tile-size slider on the top bar, which is the same answer to the same
			     question. -->
				<div
					class="volume"
					class:off={!sound}
					role="group"
					aria-label="Volume"
					onpointerenter={(event) => onhold?.(event)}
					onpointermove={(event) => onhold?.(event)}
				>
					<!-- The key is named only while the control can do something. A tooltip offering M over a
				     button that is refused is a shortcut that does nothing when it is pressed. -->
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
					<!-- Shown on hover and on focus both. Hover alone would make the level unreachable by
				     keyboard: the slider is still in the tab order whether or not anybody can see it. -->
					<div class="level-pop">
						<span class="reading" aria-hidden="true">{volume}%</span>
						<!-- Standing on its end, through the shared slider, so the volume and the
						     timeline are the same control rather than two drawn separately. -->
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
				<!-- Everything about the file rather than the playhead, opening under the pointer. -->
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
						<!-- The bridge is the outer box: transparent, and reaching down to the button so the
					     pointer travelling between the two is never over neither. -->
						<div class="bridge">
							<!-- The box is `Panel`'s: a raised ground, a hairline, a corner and an inset,
							     decided once for every panel in the app rather than restated here. What
							     stays this file's is the three columns the drawer's icons stand in. -->
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
		/* The one place backdrop-blur is allowed, per the design system: it keeps the controls
		   legible over whatever frame happens to be behind them. */
		backdrop-filter: blur(var(--blur-glass));
		/*
		 * Faded in at the top rather than starting abruptly: a flat scrim's hard upper edge is a
		 * line drawn across the picture, the frame's full width, with nothing explaining it.
		 */
		background: linear-gradient(
			to top,
			var(--sift-scrim-strong) 55%,
			var(--sift-scrim) 80%,
			var(--sift-scrim-none)
		);
	}

	/* Beside the clocks the row stands under the timeline, its outer buttons flush with the scrub
	   bar's ends: one grid, the scrub line sharing its columns. */
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

	/* Never narrower than its controls: short of room it spills evenly back under the clocks. */
	.bar.under-line > .row {
		grid-column: 2;
		justify-self: center;
		inline-size: 100%;
		min-inline-size: max-content;
	}

	/* The transport at the start, after any lead; the end takes the rest. `minmax(0, 1fr)` lets the
	   row shrink below its content. A caller may set these columns (Theater's bar does). */
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

	/* After `.led`, so a phone's rows win: the transport alone and centred, the two ends under it. */
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

	/* Nothing to hear: the shared button's dimmed look. The controls inside are refused on their own. */
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
		/* Snug around a 22px column. Generous padding on a control that is itself narrow is most of
		   what makes a popup read as a panel with something small in it rather than as a slider. */
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

	/* Hover, or a keyboard user reaching the slider, and `:has(:focus-visible)` rather than
	   `:focus-within`. `:focus-within` matches a plain MOUSE click: clicking mute focuses the button,
	   so the level would stay open after the pointer had left and only shut when something else
	   was clicked. `:focus-visible` is the browser's own answer to "is this the keyboard", false for a
	   click, so the level follows the pointer for a mouse and stays put for a keyboard. */
	.volume:hover .level-pop,
	.volume:has(:focus-visible) .level-pop {
		opacity: 1;
		visibility: visible;
	}

	/* DRESSED BY: .level (`Slider` draws the track and the thumb; this file only says how tall the
	   column is, which is a fact about the popup it stands in). */
	.level-pop :global(.level) {
		/* The pointer target, not the visible line: the track somebody aims at is a few pixels wide,
		   which is nothing to hit. The slider stays wide enough to grab and draws the thin part inside
		   itself.

		   Physical, because a vertical writing mode swaps the axes. See `Slider`. Written as
		   `inline-size` this would come out 104 pixels TALL and 22 wide, which is a horizontal bar. */
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

	/*
	 * Aligned to the button's TRAILING edge rather than centred on it.
	 *
	 * The drawer button sits near the right-hand end of the bar, so a panel centred on it hangs off
	 * that corner, and the frame clips what leaves it, silently. On the player there is room and
	 * it looks fine; on a cell of a wall the same panel would lose its last icon to the edge of the
	 * picture, with nothing to say a control was missing rather than absent.
	 */
	.bridge {
		position: absolute;
		inset-block-end: 100%;
		inset-inline-end: 0;
		z-index: 6;
		/* What makes the drawer reachable at all: the grid floats clear of its button, and without
		   this the pointer is over nothing for the width of that gap and the drawer closes on the
		   way to itself. Transparent, so only the panel inside it is seen. */
		padding-block-end: var(--space-2);
	}

	/*
	 * The drawer's contents, in four columns: a player's seven stand four and three with the three
	 * that open a menu in the top row, and a Theater cell's twelve stand three rows of four. Shuffle
	 * and Repeat stand on the bar, not in the drawer, and three columns would leave the
	 * player's seventh alone on a row. The ground, edge, corner, inset and shadow are
	 * `Panel`'s. A direct child, so a caller whose drawer holds its own panel is not laid out by
	 * this rule too; anchored on `.bridge`, this file's own element, so it reaches nothing but the
	 * drawer it draws.
	 *
	 * The gap is tighter than a panel's own: these are icon buttons in a grid, not blocks of words.
	 */
	.bridge > :global(.panel) {
		grid-template-columns: repeat(4, auto);
		gap: var(--space-1);
	}

	/* The line between groups of controls, in the drawer and on the row alike. Its own element rather
	   than a border on the button beside it: a border on a rounded button is an arc down one side of
	   that corner, which reads as a box half-drawn around the icon. */
	:global(:root[data-motion='reduce']) .level-pop {
		transition: none;
	}
</style>
