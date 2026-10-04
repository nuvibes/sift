<script lang="ts">
	/*
	 * The wall itself: the grid, and a cell in each of its places, drawn on the Theater screen and in
	 * the corner panel. The pop-out takes the WHOLE wall, with the same cell objects, so nothing is
	 * rebuilt when it moves.
	 */
	import CellView from './CellView.svelte';
	import StallBar, { LEAST_FOR_THE_BAR } from './StallBar.svelte';
	import type { CellEcho } from '$lib/theater/echoes';
	import { area, template } from '$lib/theater/layouts';
	import { feedHeight } from '$lib/theater/fit';
	import { FILTERS_PANEL, screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { EDGE_BAND, stage } from '$lib/components/shell/stage.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { AUDIBLE_MARK_MS, showing, type Wall } from '$lib/theater/wall.svelte';
	import { wallChrome } from '$lib/theater/chrome.svelte';
	import { screenChanges } from '$lib/components/player/motion';

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

	/*
	 * Whether the wall's chrome is up: the one bar, and the number in each cell's corner, answered
	 * once for both. The corner panel uses its own clock; everywhere else, this one. It answers the
	 * edges, not every movement, or the bar would lie across videos being watched; the bottom band
	 * is measured from the bar (`BOTTOM`), and the screen's handle says the bar is there.
	 */

	/** How near the TOP of the wall the pointer has to be: the shell's own band, since its bar lives there. */
	const EDGE = EDGE_BAND;

	/**
	 * And how near the bottom: the bar's own measured height (it varies with the wall), plus the gap
	 * it floats above the foot and the same step again, as the strip spaces it; a shallower band
	 * fades the control as the pointer arrives.
	 */
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

	let quietAt: ReturnType<typeof setTimeout> | null = null;
	let edgeAt: ReturnType<typeof setTimeout> | null = null;

	/*
	 * Watched on the document, since a hand travelling to the top bar leaves the wall; distances are
	 * still measured against the wall.
	 */
	function stir(event: PointerEvent | MouseEvent) {
		const box = frame?.getBoundingClientRect();
		/*
		 * ON ANY OF THE CHROME, which is three rows that come and go together, so resting on any holds
		 * all. Anything a bar opens counts too; a portalled menu is `screenBar.open`'s.
		 */
		overTheBar =
			event.target instanceof Element &&
			event.target.closest('.stage-bar, .bar-row, .topbar') !== null;
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
		/* ANY MOVEMENT UNDOES A DISMISSAL (`B`): `stage.dismissed` is raised by the key, lowered here. */
		stage.dismissed = false;
		stirred = true;
		if (quietAt !== null) clearTimeout(quietAt);
		quietAt = setTimeout(() => (stirred = false), IDLE);
	}

	/** The pointer has left the window. Nothing is being pointed at, so the clock runs out. */
	function settle() {
		overTheBar = false;
		if (edgeAt !== null) clearTimeout(edgeAt);
		edgeAt = setTimeout(() => {
			atAnEdge = false;
			edgeAt = null;
		}, IDLE);
		if (quietAt !== null) clearTimeout(quietAt);
		quietAt = setTimeout(() => (stirred = false), IDLE);
	}

	$effect(() => {
		document.addEventListener('pointermove', stir);
		document.addEventListener('pointerleave', settle);
		return () => {
			document.removeEventListener('pointermove', stir);
			document.removeEventListener('pointerleave', settle);
			if (quietAt !== null) clearTimeout(quietAt);
			if (edgeAt !== null) clearTimeout(edgeAt);
		};
	});

	/*
	 * THE SHELL'S BAR IS THIS ONE'S SHADOW: the bottom bar, the one reached for, decides for both.
	 * Not in the corner panel; given back on the way out.
	 */
	$effect(() => {
		if (mini.wall) return;
		stage.driven = true;
		return () => {
			stage.driven = false;
			stage.barHidden = false;
			// Given back with the rest of it: a dismissal is about THIS screen, and one left behind
			// would take the bar off whatever screen came next.
			stage.dismissed = false;
		};
	});

	$effect(() => {
		if (mini.wall || !stage.filling) return;
		stage.barHidden = !chromeUp;
	});

	/* And the screen's own row under the top bar, which cannot see this component either. Put back
	   up on the way out, or the next screen inherits a row nobody is driving. See `wallChrome`. */
	$effect(() => {
		if (mini.wall) return;
		wallChrome.up = chromeUp;
		return () => {
			wallChrome.up = true;
		};
	});

	/* The panel keeps its own answer; everywhere else this clock, with `stage.barHidden` counting
	   while filled, so the bar moves with the rest of the chrome. */
	const chromeUp = $derived.by(() => {
		if (mini.wall) return mini.chromeUp;
		/* SENT AWAY BY HAND (`B`), which beats everything below, until the next movement (`stir`). */
		if (stage.dismissed) return false;
		// On the bar, it stays up whatever the clock says: somebody is using it.
		if (overTheBar) return true;
		/* And while anything the bars opened is OPEN: a portalled menu is outside both bands. */
		if (screenBar.open !== null) return true;
		/*
		 * The bands, on every wall; `stirred` too, so a parked pointer still lets the chrome go.
		 */
		return stirred && atAnEdge;
	});

	/*
	 * Whether a cell says which number it is: not in the corner panel, which no keyboard addresses
	 * and has no room to spare.
	 */
	const numbered = $derived(!mini.wall);

	/*
	 * The wall's own box and the bar's measured room; the row arithmetic is `$lib/theater/fit`'s,
	 * tested against shapes.
	 */
	let barRoom = $state(0);

	/*
	 * The bottom band, from the bar's own measured height (`STEP`), declared after `barRoom` for
	 * reading order; the shell's band stands in before the first measure.
	 */
	const BOTTOM = $derived(barRoom > 0 ? barRoom + STEP * 2 : EDGE_BAND);

	let boxWide = $state(0);
	let boxTall = $state(0);

	/** The gap between feeds, which the sum has to account for. Kept beside the rule that sets it. */
	const GAP = 8;

	/*
	 * Nothing is measured at the first paint, and a bar flickering in a frame later is worse than one
	 * simply there (`LEAST_FOR_THE_BAR`).
	 */
	const roomForTheBar = $derived(boxWide === 0 || boxWide >= LEAST_FOR_THE_BAR);

	/*
	 * Whether the strip is drawn: the one thing on the wall that keeps clear of the bar, since it is
	 * pressed at the edge the bar rises from (`.grid` does not).
	 */
	const hasStrip = $derived(wall.previews.length > 0 && !mini.wall);

	/*
	 * The wall settles when the screen fills and when it lets go, on the one movement every player's
	 * screen change makes (`screenChanges`). The first paint is not a change.
	 */

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

	/*
	 * The marks on the audible cell, faded once the sound has been still a while, back the moment it
	 * moves, when "which one am I hearing" is asked.
	 */
	let marksUp = $state(true);

	/*
	 * The app's own Filter panel, pointed at one cell, for a cell's bar and the filled wall's bar.
	 */
	function openFilters(index: number) {
		/* The cell comes from the FOCUS, the same act as a press on it or its number. */
		wall.focus(index);
		/*
		 * Opened so that it falls shut when the pointer leaves it: opened mid-errand from the foot of
		 * the screen, wandering off ends the errand. The same vetoes (typing, an open menu) hold.
		 */
		screenBar.showByHover(FILTERS_PANEL);
	}

	$effect(() => {
		wall.soundMovedAt;
		marksUp = true;
		const at = setTimeout(() => (marksUp = false), AUDIBLE_MARK_MS);
		return () => clearTimeout(at);
	});
</script>

<!-- `--wall` carries the shape as a custom property: the served policy refuses style attributes. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="wall"
	data-theater-wall
	bind:this={frame}
	use:screenChanges
	style:--wall={grid}
	style:--feed-height={tall === null ? null : `${tall}px`}
	style:--bar-room="{barRoom}px"
	onpointerdowncapture={() => wall.unkey()}
>
	<div class="grid" bind:clientWidth={boxWide} bind:clientHeight={boxTall}>
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

	<!--
		THE STRIP: the feeds that are not being watched yet, outside the grid, whose columns are sized
		by their pictures while previews want equal boxes; hence Center Stage is a wall plus a number.
		Not in the corner panel, where previews would be too small to recognise.
	-->
	{#if hasStrip}
		<div
			class="strip"
			class:clear={roomForTheBar}
			class:spread={!chromeUp}
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

	<!--
		ONE BAR FOR THE WHOLE WALL, wherever there is a wall, since per-cell scrubbers are copies of one
		control at any size. Inside the wall, the box fullscreen paints, positioned against it
		(`StageBar` is absolute for that reason).
	-->
	<StallBar {wall} up={roomForTheBar} quiet={!chromeUp} bind:tall={barRoom} />
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
	}

	/*
	 * The pictures take the whole height, and the bar rises over them as a translucent absolute pane,
	 * so its coming and going moves no layout.
	 *
	 * The strip: small feeds of a fixed height, each as wide as its picture, scrolling sideways if it
	 * must. With the chrome up it sits above the bar (`--bar-room`, the bar's measured height); with
	 * it hidden it grows down into that band. The band is one box handed between the two, so the
	 * strip's top edge and the grid never move. A spread preview's lower part lies in the bottom
	 * band, where a pointer brings the bar back.
	 */
	.strip.clear {
		/* The bar's height, the gap it floats above the foot, and the SAME gap above it, one token twice. */
		--strip-room: calc(var(--bar-room, 0px) + var(--space-4) * 2);
	}

	.strip.clear.spread {
		block-size: calc(var(--strip-tall) + var(--strip-room));
		margin-block-end: 0px;
	}

	.strip {
		/* The strip's own height, and the band it holds open under it. Two properties rather than
		   two copies of the sums, so the two states above cannot come to disagree about either. */
		--strip-tall: clamp(72px, 16vh, 148px);
		--strip-room: 0px;
		block-size: var(--strip-tall);
		margin-block-end: var(--strip-room);
		flex: none;
		/* The same token and the same curve on both, which is what keeps their sum, and so the
		   strip's top edge and the grid above it, still through the change. See `.spread`. */
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
		/*
		 * Its own scrolling, deliberately: in the shared `Scroller` the row would be `max-content`, and
		 * a real video would size the strip screens wide. The one screen painting its own bar, counted
		 * by `gate:one-scrollbar`'s baseline.
		 */
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
