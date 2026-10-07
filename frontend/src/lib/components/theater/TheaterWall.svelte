<script module lang="ts">
	/** The translate and two scales that draw `to` `k` of the way back from `from`, about `to`'s top left. */
	export function between(from: DOMRect, to: DOMRect, k: number) {
		return {
			x: (from.left - to.left) * (1 - k),
			y: (from.top - to.top) * (1 - k),
			sx: (from.width + (to.width - from.width) * k) / to.width,
			sy: (from.height + (to.height - from.height) * k) / to.height
		};
	}
</script>

<script lang="ts">
	/*
	 * The wall: the grid, and a cell in each of its places, on the Theater screen and in the corner
	 * panel, which takes the same cell objects.
	 */
	import { flushSync } from 'svelte';
	import CellView from './CellView.svelte';
	import StallBar, { LEAST_FOR_THE_BAR } from './StallBar.svelte';
	import type { CellEcho } from '$lib/theater/echoes';
	import { area, template } from '$lib/theater/layouts';
	import { feedHeight, stripHeight } from '$lib/theater/fit';
	import { FILTERS_PANEL, screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { EDGE_BAND, stage } from '$lib/components/shell/stage.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { AUDIBLE_MARK_MS, showing, type Wall } from '$lib/theater/wall.svelte';
	import { wallChrome } from '$lib/theater/chrome.svelte';
	import { screenChanges } from '$lib/components/player/motion';
	import { bezier, durationToken, easingToken, motion } from '$lib/shell/motion.svelte';
	import { matches } from '$lib/shell/shortcuts';
	import { api, isMissing } from '$lib/api/client';
	import { arrivals, libraryChanges, whenChanged } from '$lib/library/changes.svelte';

	interface Props {
		wall: Wall;
		/** Fill the screen, or stop. Absent in the corner, where there is nothing to fill it with. */
		onfullscreen?: () => void;
		/**
		 * What a key last did at each cell, by that cell's own key: handed in, since the keyboard is the
		 * SCREEN's and the corner panel takes none (`$lib/theater/echoes`).
		 */
		echoes?: Record<string, CellEcho>;
	}

	let { wall, onfullscreen, echoes = {} }: Props = $props();

	const grid = $derived(template(wall.shape));

	/* A file deleted elsewhere leaves every cell showing it at once, not at its next turn. */
	whenChanged(libraryChanges, () => {
		for (const cell of wall.cells) {
			const id = cell.playing?.id;
			if (id === undefined) continue;
			void api.get(`/assets/${id}`).catch((error) => {
				if (isMissing(error) && cell.playing?.id === id) void cell.advance();
			});
		}
	});
	/* And a cell that found nothing to play looks again when files arrive. */
	whenChanged(arrivals, () => {
		for (const cell of wall.cells) if (cell.state === 'nothing_here') void cell.restart();
	});

	/*
	 * Whether the wall's chrome is up, answered once for the bar and the cell numbers. It answers the
	 * edges, not every movement, or the bar would lie across videos being watched.
	 */

	/** How near the TOP of the wall the pointer has to be: the shell's own band, since its bar lives there. */
	const EDGE = EDGE_BAND;

	/** And the bottom: the bar's measured height, its float and the same step again (`BOTTOM`). */
	const STEP = 16;

	/** How long the pointer may hold still before the chrome goes. It goes wherever it is. */
	const IDLE = 1500;

	/** The wall's box, for the two edges. */
	let frame = $state<HTMLElement | null>(null);

	/** Whether something has happened here recently: the clock every surface now runs. */
	let stirred = $state(true);

	/** Whether the pointer is at the top or the bottom of the wall. Only Center stage reads it. */
	let atAnEdge = $state(false);

	/** Whether the pointer is on the bar itself, or in something the bar has opened. */
	let overTheBar = $state(false);

	/** Whether the keyboard is in the bars, or in something they opened. */
	let focusHeld = $state(false);

	/** Sent away by `B`, which beats everything until the next movement, Tab or `B`. */
	let sentAway = $state(false);

	/** The three rows of chrome, which come and go together, so resting on any holds all. */
	const BARS = '.stage-bar, .bar-row, .topbar';

	/** What a bar opens, portalled outside it. */
	const OPENED = "[role='menu'], [role='listbox'], [role='dialog']";

	let quietAt: ReturnType<typeof setTimeout> | null = null;
	let edgeAt: ReturnType<typeof setTimeout> | null = null;

	/* On the document, since a hand going to the top bar leaves the wall. */
	function stir(event: PointerEvent | MouseEvent) {
		const box = frame?.getBoundingClientRect();
		/* A portalled menu is `screenBar.open`'s. */
		overTheBar = event.target instanceof Element && event.target.closest(BARS) !== null;
		/* Anything ABOVE the wall is inside the top band: a pointer there is using the chrome. */
		const near =
			overTheBar ||
			(box !== undefined &&
				(event.clientY - box.top <= EDGE || box.bottom - event.clientY <= BOTTOM));
		/* LEAVING THE BAND IS NOT LEAVING: the band gets the same clock as everything else. */
		if (near) {
			atAnEdge = true;
			if (edgeAt !== null) clearTimeout(edgeAt);
			edgeAt = null;
		} else if (atAnEdge && edgeAt === null) {
			edgeAt = setTimeout(() => {
				atAnEdge = false;
				edgeAt = null;
			}, IDLE);
		}
		sentAway = false;
		stirred = true;
		if (quietAt !== null) clearTimeout(quietAt);
		quietAt = setTimeout(() => (stirred = false), IDLE);
	}

	/** Both clocks started again: the chrome goes once they run out, wherever it is. */
	function runDown() {
		if (edgeAt !== null) clearTimeout(edgeAt);
		edgeAt = setTimeout(() => {
			atAnEdge = false;
			edgeAt = null;
		}, IDLE);
		if (quietAt !== null) clearTimeout(quietAt);
		quietAt = setTimeout(() => (stirred = false), IDLE);
	}

	/** The pointer has left the window. Nothing is being pointed at, so the clock runs out. */
	function settle() {
		overTheBar = false;
		runDown();
	}

	/** Somebody at the keyboard, as a pointer reaching the edge would be. */
	function raise() {
		sentAway = false;
		atAnEdge = true;
		stirred = true;
		runDown();
	}

	/* Any key is somebody there, Tab included; `B` also sends them away, and `F` raises them as it lands. */
	function keyed(event: KeyboardEvent) {
		if (matches(event, 'theater.fill')) return;
		if (!matches(event, 'stage.toggleBar')) return raise();
		event.preventDefault();
		if (chromeUp) sentAway = true;
		else raise();
	}

	/* Held while the keyboard is in the bars or in what they opened; let go onto the wall or nothing. */
	function focusMoved(event: FocusEvent) {
		const at = event.type === 'focusin' ? event.target : event.relatedTarget;
		const into = at instanceof Element ? at : null;
		// After the update: a focused control taken out of the page fires `focusout` inside one.
		queueMicrotask(() => {
			if (into?.closest(BARS)) focusHeld = true;
			else if (focusHeld && into?.closest(OPENED) && !frame?.contains(into)) return;
			else if (focusHeld && (event.type === 'focusin' || into === null)) {
				focusHeld = false;
				runDown();
			}
		});
	}

	$effect(() => {
		if (mini.wall) return;
		document.addEventListener('pointermove', stir);
		document.addEventListener('pointerleave', settle);
		document.addEventListener('keydown', keyed);
		document.addEventListener('focusin', focusMoved);
		document.addEventListener('focusout', focusMoved);
		return () => {
			document.removeEventListener('pointermove', stir);
			document.removeEventListener('pointerleave', settle);
			document.removeEventListener('keydown', keyed);
			document.removeEventListener('focusin', focusMoved);
			document.removeEventListener('focusout', focusMoved);
			if (quietAt !== null) clearTimeout(quietAt);
			if (edgeAt !== null) clearTimeout(edgeAt);
		};
	});

	/* The shell's bar follows this one, which is the one reached for; given back on the way out. */
	$effect(() => {
		if (mini.wall) return;
		stage.driven = true;
		return () => {
			stage.driven = false;
			stage.barHidden = false;
		};
	});

	$effect(() => {
		if (mini.wall || !stage.filling) return;
		stage.barHidden = !chromeUp;
	});

	/* And the screen's own row, put back up on the way out for the next screen (`wallChrome`). */
	$effect(() => {
		if (mini.wall) return;
		wallChrome.up = chromeUp;
		return () => {
			wallChrome.up = true;
		};
	});

	/* The panel keeps its own answer; everywhere else this clock. */
	const chromeUp = $derived.by(() => {
		if (mini.wall) return mini.chromeUp;
		if (sentAway) return false;
		// On the bar, by pointer or keyboard, or in anything it opened: somebody is using it.
		if (overTheBar || focusHeld || screenBar.open !== null) return true;
		/* `stirred` too, so a parked pointer still lets the chrome go. */
		return stirred && atAnEdge;
	});

	/* Not in the corner panel, which no keyboard addresses. */
	const numbered = $derived(!mini.wall);

	/* The row arithmetic is `$lib/theater/fit`'s. */
	let barRoom = $state(0);

	const BOTTOM = $derived(barRoom > 0 ? barRoom + STEP * 2 : EDGE_BAND);

	let boxWide = $state(0);
	let boxTall = $state(0);

	/** The gap between feeds, which the sum has to account for. Kept beside the rule that sets it. */
	const GAP = 8;

	/* Unmeasured at the first paint, the bar is simply there rather than flickering in. */
	const roomForTheBar = $derived(boxWide === 0 || boxWide >= LEAST_FOR_THE_BAR);

	const hasStrip = $derived(wall.previews.length > 0 && !mini.wall);

	/** The band kept under the pictures for the bar: its height, its float, and the top's gap again. */
	const band = $derived(roomForTheBar ? barRoom + STEP + GAP : 0);

	/* Without a strip the grid holds the band itself while the bar is up. */
	const held = $derived(!hasStrip && !mini.wall && roomForTheBar && chromeUp);

	let wallWide = $state(0);
	let wallTall = $state(0);
	let windowTall = $state(0);

	const stripTall = $derived(
		stripHeight(
			wall.shape,
			{ width: wallWide, height: wallTall },
			wall.inFocus.map((one) => one.shape),
			wall.previews.map((one) => one.shape),
			{ viewport: windowTall, under: band, gap: GAP }
		)
	);

	/*
	 * Filling the screen and letting go: the cells are drawn from where they stood to where the page
	 * puts them on each frame, so a row folding on the way is followed rather than overshot.
	 */
	let pictures = $state<HTMLElement | null>(null);
	let stood: DOMRect | null = null;
	let following = $state(false);
	let nextFrame = 0;
	let wasFilled = false;

	/** As styled now: `document.fullscreenElement` can lag a frame. */
	function filled(): boolean {
		return frame?.closest(':fullscreen') != null;
	}

	/** The box around the cells in focus, as drawn. */
	function cellsBox(): DOMRect | null {
		let box: DOMRect | null = null;
		for (const cell of pictures?.children ?? []) {
			const at = cell.getBoundingClientRect();
			if (at.width <= 0) continue;
			const left = Math.min(at.left, box?.left ?? at.left);
			const top = Math.min(at.top, box?.top ?? at.top);
			const right = Math.max(at.right, box?.right ?? at.right);
			const bottom = Math.max(at.bottom, box?.bottom ?? at.bottom);
			box = new DOMRect(left, top, right - left, bottom - top);
		}
		return box;
	}

	/* Never once the screen has changed: that is the box it is going to. */
	function measure() {
		if (!following && filled() === wasFilled) stood = cellsBox();
	}

	/** Draws the cells `k` of the way from `from` to where the page puts them now. */
	function place(from: DOMRect, k: number): boolean {
		if (frame === null || pictures === null) return false;
		frame.style.translate = frame.style.scale = frame.style.transformOrigin = '';
		// This frame's room, not the last one's: the binding hears of it only once it is drawn.
		boxWide = pictures.clientWidth;
		boxTall = pictures.clientHeight;
		wallWide = frame.clientWidth;
		wallTall = frame.clientHeight;
		flushSync();
		const to = cellsBox();
		if (to === null || k >= 1) return false;
		const wallAt = frame.getBoundingClientRect();
		const { x, y, sx, sy } = between(from, to, k);
		frame.style.transformOrigin = `${to.left - wallAt.left}px ${to.top - wallAt.top}px`;
		frame.style.translate = `${x}px ${y}px`;
		frame.style.scale = `${sx} ${sy}`;
		return true;
	}

	function stop() {
		cancelAnimationFrame(nextFrame);
		following = false;
		if (frame) frame.style.translate = frame.style.scale = frame.style.transformOrigin = '';
	}

	/* Up before the shell hears of the change, so its bar is never sent away for a frame. */
	function upFirst() {
		if (!mini.wall && filled() !== wasFilled) raise();
	}

	/* Heard from a resize too, which can be drawn a frame before the browser's event. */
	function changed() {
		if (mini.wall || frame === null || filled() === wasFilled) return;
		wasFilled = !wasFilled;
		follow(wasFilled);
	}

	function follow(entering: boolean) {
		const from = following ? cellsBox() : stood;
		stop();
		// Somebody changed the screen, so the chrome comes up, and holds still while the cells move.
		raise();
		if (from === null || motion.reduced) return measure();
		const duration = durationToken('--dur-slow', 320);
		const curve = bezier(
			entering ? easingToken('--ease', [0.2, 0, 0, 1]) : easingToken('--ease-in', [0.4, 0, 1, 1])
		);
		const start = performance.now();
		following = true;
		const step = () => {
			const k = Math.min(1, (performance.now() - start) / duration);
			if (place(from, curve(k))) nextFrame = requestAnimationFrame(step);
			else stop();
		};
		step();
	}

	/* Taken before anything a press or a key does, and whenever the room changes. */
	$effect(() => {
		void boxWide;
		void boxTall;
		measure();
	});

	/* The event on the window, after the shell has heard it and the page has redrawn for it. */
	$effect(() => {
		if (frame === null) return;
		wasFilled = filled();
		const resized = new ResizeObserver(changed);
		resized.observe(frame);
		document.addEventListener('pointerdown', measure, { capture: true, passive: true });
		document.addEventListener('keydown', measure, { capture: true, passive: true });
		window.addEventListener('fullscreenchange', upFirst, { capture: true });
		window.addEventListener('fullscreenchange', changed);
		return () => {
			resized.disconnect();
			window.removeEventListener('fullscreenchange', upFirst, { capture: true });
			document.removeEventListener('pointerdown', measure, { capture: true });
			document.removeEventListener('keydown', measure, { capture: true });
			window.removeEventListener('fullscreenchange', changed);
			stop();
		};
	});

	/* Unseen while the opening draw has cells with no file yet, so none is drawn at a stand-in shape
	   and then moves its neighbours when its file arrives. */
	const settling = $derived(wall.opening && wall.inFocus.some((cell) => cell.shape === null));

	/* Every cell's shape comes from the CELL (`Cell.shape`), which this arithmetic and the cell's own
	   `aspect-ratio` must share exactly. */
	const tall = $derived(
		feedHeight(
			wall.shape,
			{ width: boxWide, height: boxTall },
			wall.cells.map((one) => one.shape),
			GAP
		)
	);

	/* The audible cell's marks fade once the sound is still, and come back when it moves. */
	let marksUp = $state(true);

	/* The Filter panel, pointed at one cell; it falls shut when the pointer leaves it. */
	function openFilters(index: number) {
		wall.focus(index);
		screenBar.showByHover(FILTERS_PANEL);
	}

	$effect(() => {
		wall.soundMovedAt;
		marksUp = true;
		const at = setTimeout(() => (marksUp = false), AUDIBLE_MARK_MS);
		return () => clearTimeout(at);
	});
</script>

<svelte:window bind:innerHeight={windowTall} />

<!-- `--wall` carries the shape as a custom property: the served policy refuses style attributes. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="wall"
	class:following
	data-theater-wall
	bind:this={frame}
	bind:clientWidth={wallWide}
	bind:clientHeight={wallTall}
	style:--wall={grid}
	style:--feed-height={tall === null ? null : `${tall}px`}
	style:--band="{band}px"
	onpointerdowncapture={() => wall.unkey()}
>
	<!-- The one stage for every picture inside, so none moves on its own: the wall moves them (`follow`). -->
	<div class="stages" use:screenChanges>
		<div
			class="grid"
			class:held
			class:settling
			bind:this={pictures}
			bind:clientWidth={boxWide}
			bind:clientHeight={boxTall}
		>
			{#each wall.inFocus as cell, index (cell.key)}
				<CellView
					{wall}
					{cell}
					{index}
					{marksUp}
					{chromeUp}
					{numbered}
					echo={echoes[cell.key] ?? null}
					place={area(wall.shape.slots[index])}
					onpick={() => openFilters(index)}
					onfullscreen={onfullscreen ?? (() => {})}
				/>
			{/each}
		</div>

		<!-- The strip: the feeds not watched yet, outside the grid; not in the corner panel. -->
		{#if hasStrip}
			<div
				class="strip"
				class:clear={roomForTheBar}
				class:spread={!chromeUp}
				style:--strip-tall="{stripTall}px"
				role="group"
				aria-label="Previews"
			>
				{#each wall.previews as cell, at (cell.key)}
					{@const index = wall.inFocus.length + at}
					<CellView
						{wall}
						{cell}
						{index}
						{marksUp}
						{chromeUp}
						{numbered}
						echo={echoes[cell.key] ?? null}
						preview
						place={undefined}
						onpress={() => wall.sendToFocus(index)}
						onpick={() => openFilters(index)}
						onfullscreen={onfullscreen ?? (() => {})}
					/>
				{/each}
			</div>
		{/if}

		<!-- One bar for the whole wall, positioned against it. -->
		<StallBar {wall} up={roomForTheBar} quiet={!chromeUp} bind:tall={barRoom} />
	</div>
</div>

<style>
	/* The frame: the grid of feeds, the strip under it, and the one bar over both, positioned on it. */
	.wall {
		position: relative;
		flex: 1;
		min-block-size: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
		background: var(--sift-bg);
	}

	/* While the cells are drawn by `follow`, the room the page gives them lands at once. */
	.wall.following .grid,
	.wall.following .strip {
		transition: none;
	}

	/* Draws no box, so the screen change finds nothing to move here. */
	.stages {
		display: contents;
	}

	.grid {
		flex: 1;
		min-block-size: 0;
		display: grid;
		/* Rows as tall as the feeds worked out to be, centred in the box in every case. */
		align-content: center;
		/* The shape the wall is in; the fallback is the shipped wall, two cells side by side. */
		grid-template: var(--wall, '. .' 1fr / 1fr 1fr);
		/* The leftover width is the wall's, centred, so it falls evenly at both outside edges. */
		justify-content: center;
		/* Written as a number rather than a token because the sum above has to know it, and two
		   places holding one gap is how they come to disagree by four pixels. */
		gap: 8px;
		transition: margin-block-end var(--dur-slow) var(--ease-in);
	}

	.grid.settling {
		visibility: hidden;
	}

	/* The pictures give the bar its band while it is up, on the bars' curves: in eased out, away eased in. */
	.grid.held {
		margin-block-end: var(--band);
		transition: margin-block-end var(--dur-slow) var(--ease);
	}

	/* With the chrome up the strip sits above the bar's band; hidden, it grows down into it. One box
	   handed between the two, so the strip's top edge and the grid never move. */
	.strip.clear {
		--strip-room: var(--band);
	}

	.strip.clear.spread {
		block-size: calc(var(--strip-tall) + var(--strip-room));
		margin-block-end: 0px;
	}

	.strip {
		/* `--strip-tall` is written by `stripHeight`. */
		--strip-room: 0px;
		block-size: var(--strip-tall);
		margin-block-end: var(--strip-room);
		flex: none;
		/* One token and curve on both, which keeps their sum still. */
		transition:
			margin-block-end var(--dur-slow) var(--ease),
			block-size var(--dur-slow) var(--ease);
		display: flex;
		/*
		 * `safe`, a guard: an overflowing centred row cannot scroll back past its start. It does not
		 * bind while cells keep `min-inline-size: 0`; `theater.spec.ts` checks every preview is inside.
		 */
		justify-content: safe center;
		align-items: flex-end;
		gap: 8px;
		/* Its own scrolling: in `Scroller` a real video would size the row screens wide. */
		overflow-x: auto;
		overflow-y: hidden;
	}

	/* The same corner on every feed, in the wall and in the strip, since an 8px gap shows every corner. */
	.grid :global(.cell),
	.strip :global(.cell) {
		border-radius: var(--radius-sm);
		overflow: hidden;
	}

	/* The strip's height; `min-inline-size: 0`, since a flex item will not shrink below its picture. */
	.strip :global(.cell) {
		block-size: 100%;
		min-inline-size: 0;
	}
</style>
