<script lang="ts">
	/* WHY NOT SHARED: button: the tile IS the control (a picture with nothing else in it) and the two badges in
	   its corner are marks on a photograph. `Pressable` lifts on hover and draws its ring as a
	   box-shadow on itself; this tile zooms the PICTURE inside its own clip and draws the ring as a
	   SIBLING (both explained below), and its 20px badges are smaller than a button's minimum. */

	/*
	 * One item in the grid, the most-looked-at object in the application, with two extension points
	 * and no opinions about what goes in them: `controls`, a snippet drawn on hover (a heart, stars, a
	 * checkbox), and `onopen`, what activating it does.
	 *
	 * The width is applied with `style:`, which compiles to `setProperty`: the served policy refuses
	 * inline style attributes, silently.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import PickMark from '$lib/components/common/PickMark.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Withheld from '$lib/components/common/Withheld.svelte';
	import { length } from '$lib/library/facts';
	import type { components } from '$lib/api/schema';
	import {
		CLICK_THROUGH,
		HIDDEN_REST,
		HIDDEN_VERB,
		HIDDEN_WORDS,
		markFor
	} from '$lib/library/sharing-marks';
	import {
		DURATION_MARK,
		GIF_MARK,
		HIDDEN_MARK,
		O_COUNT_MARK,
		PINNED_MARK,
		SHARING_MARK,
		tileMarks,
		VIEWS_MARK
	} from '$lib/grid/tile-marks.svelte';

	export type TileItem = Pick<components['schemas']['AssetSummary'], 'id' | 'media_type'> &
		Partial<
			Pick<
				components['schemas']['AssetSummary'],
				| 'duration_ms'
				| 'resume_ms'
				| 'concealed'
				| 'shared'
				| 'restricted'
				| 'shared_here'
				| 'restricted_here'
				| 'hidden'
				| 'hidden_here'
				| 'unreachable'
				| 'pinned'
				| 'views'
				| 'o_count'
			>
		>;

	interface Props {
		item: TileItem;
		/**
		 * Whether this wall honours the pin, which decides whether the MARK is drawn at all: off unless
		 * the wall offers the verb or orders by it, from the same prop, so no wall marks an order it
		 * lacks.
		 */
		pinnable?: boolean;
		/** Laid-out size, in pixels. Computed by the grid, not by the tile. */
		width: number;
		height: number;
		/** Where the still comes from. Absent while the file is still being imported. */
		thumbSrc?: string;
		/** The hover clip, once it has been fetched. */
		previewSrc?: string;
		/** Whether this tile currently holds one of the shared video slots. */
		playing?: boolean;
		/** Whether this one is part of a selection: drawn, since the bar's count says how many, not which. */
		picked?: boolean;
		/**
		 * Picked for a swap, in swap mode: the accent wash and the swap's arrows, as on an entity card,
		 * apart from the selection's ring and tick, which answer a different question.
		 */
		swapPicked?: boolean;
		/**
		 * In swap mode, a file no swap will send (Kept local or "Don't swap", on it or on what it is
		 * filed under): the swap pick's shape in the danger colour (`PickMark`'s `refused`).
		 */
		swapRefused?: boolean;
		/** True between the file being accepted and its thumbnail arriving. */
		importing?: boolean;
		/**
		 * What an empty tile means, when it does not mean "still importing": a still that will never
		 * arrive is drawn static and said in words, rather than as a shimmer that never resolves.
		 */
		placeholder?: string;
		/** Why the still is never going to arrive, in the feature's own words: the recorded verdict.
		 *  Drawn on hover over the placeholder, so a wall of grey boxes can say what it is. */
		reason?: string;
		/** Overlay content, drawn on hover. Filled by whatever owns the controls. */
		controls?: Snippet<[string]>;
		/** Activated, by pointer or keyboard. */
		onopen?: (id: string) => void;
		/**
		 * The sharing mark was pressed: it opens the sharing panel for this file, where who and where
		 * the decision was made both are.
		 */
		onmark?: (id: string) => void;
		/*
		 * The crossed-out eye was pressed: hiding, not sharing, so its own panel. Absent draws the eye
		 * as plain text.
		 */
		onhidden?: (id: string) => void;
		onhover?: (id: string, hovering: boolean) => void;
	}

	let {
		item,
		width,
		height,
		thumbSrc,
		previewSrc,
		playing = false,
		picked = false,
		swapPicked = false,
		swapRefused = false,
		importing = false,
		placeholder,
		reason,
		pinnable = false,
		controls,
		onopen,
		onhover,
		onmark,
		onhidden
	}: Props = $props();

	/*
	 * The chip in the corner: what this is, or how long it runs. A still gets nothing (a photograph
	 * probes as a one-frame video); a GIF says GIF, since its length answers nothing; a video is
	 * timed. The marks' preferences are read here, so every wall honours them.
	 */
	const badge = $derived(
		item.media_type === 'image'
			? null
			: item.media_type === 'gif'
				? 'GIF'
				: length(item.duration_ms)
	);

	/* One corner, two preferences (GIF and running time), since they are wanted by different people;
	   which governs follows what the file is. */
	const badgeMark = $derived(item.media_type === 'gif' ? GIF_MARK : DURATION_MARK);

	/*
	 * How far into this one you got, as a percentage of its length, or null for "nowhere": the thin
	 * bar along the bottom edge. Whether to draw it is the server's (`resume_ms` arrives judged, by
	 * the rule the player and `viewed:continue` share). The position, not a fraction, arrives;
	 * clamped, since a re-probed shorter duration could overflow the track.
	 */
	const resumeAt = $derived(
		item.resume_ms != null && item.duration_ms
			? Math.min(100, Math.max(0, (item.resume_ms / item.duration_ms) * 100))
			: null
	);

	/*
	 * The still was asked for and did not come (a 404 at load), so the tile says so rather than show
	 * the browser's torn-page glyph. Reset on a new address, or a recycled tile keeps the old failure.
	 */
	let broken = $state(false);
	$effect(() => {
		void thumbSrc;
		broken = false;
	});

	// As the thumbnail: no heart on a concealed tile or one still importing.
	const showControls = $derived(!item.concealed && !importing && Boolean(thumbSrc));

	/*
	 * Whether this tile may say anything about the file it stands for: marks are facts about the file
	 * and need no picture (the gone mark matters most where no still was made). Not on a concealed
	 * tile, which says nothing about what is behind the veil, nor while importing.
	 */
	const showMarks = $derived(!item.concealed && !importing);

	/*
	 * The sharing mark, and the sentence that says what it means: what the answer is and WHERE it was
	 * decided (a restricted folder is not undone from a file). Worked out once, so the tooltip and the
	 * accessible name are one string.
	 */
	const mark = $derived(markFor(tileMarks.shows(SHARING_MARK) ? item : {}, { file: true }));

	/* Which of the top-left marks this tile has anything to say with, after the preferences, named
	 * so the row's condition and its marks cannot disagree. `gone` answers to no preference: it says
	 * the library and the disk disagree (`tile_marks` on the server). */
	const showsGone = $derived(item.unreachable === true);
	const showsPinned = $derived(item.pinned === true && pinnable && tileMarks.shows(PINNED_MARK));
	const showsSeen = $derived((item.views ?? 0) > 0 && tileMarks.shows(VIEWS_MARK));
	/* The O counter, beside the watch count; only above nought, or zero would sit on every tile. */
	const showsOCount = $derived((item.o_count ?? 0) > 0 && tileMarks.shows(O_COUNT_MARK));
	const showsHidden = $derived(item.hidden === true && tileMarks.shows(HIDDEN_MARK));
	const anyMark = $derived(
		showsGone || showsPinned || showsSeen || showsOCount || showsHidden || Boolean(mark)
	);

	/* The top row's marks that answer to a preference, in their fixed order
	   (`tileMarks.restingFirst`). `gone` is always first. */
	const TOP_ROW = [PINNED_MARK, VIEWS_MARK, O_COUNT_MARK, SHARING_MARK, HIDDEN_MARK] as const;

	function open() {
		if (item.concealed) return;
		onopen?.(item.id);
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			open();
		}
	}
</script>

<!--
	The frame holds the controls BESIDE the button, since a button inside a button is invalid and
	would swallow presses meant for a heart. The reveal is therefore written against the frame, and
	the preview stops while the pointer is on a control.
-->
<div class="tile-frame" style:width="{width}px" style:height="{height}px">
	<!-- A button: focusable, announced, and Enter and Space work for free. -->
	<button
		type="button"
		class="tile"
		class:concealed={item.concealed}
		class:picked
		aria-pressed={picked || undefined}
		style:width="{width}px"
		style:height="{height}px"
		onclick={open}
		onkeydown={onKeydown}
		onpointerenter={() => onhover?.(item.id, true)}
		onpointerleave={() => onhover?.(item.id, false)}
		onfocus={() => onhover?.(item.id, true)}
		onblur={() => onhover?.(item.id, false)}
		aria-label={item.concealed ? 'Hidden item' : undefined}
		disabled={item.concealed}
	>
		{#if item.concealed}
			<!-- Says something is here and refuses to say what: no image at all, since the server sends
			     no bytes for a concealed item. The withheld face (the ground, blurred) every Hidden thing
			     wears, never a flat slab that reads as a failed load, and the hidden mark, not a padlock,
			     which means a locked filter. -->
			<Withheld label="Hidden" />
		{:else if broken}
			<!-- No picture for this one and there will not be: better than the torn-page glyph. -->
			<span class="flat missing">
				<Icon name="hide_image" size={16} label="No preview" />
			</span>
		{:else if placeholder && !thumbSrc}
			<!-- Still and static: nothing is happening. The words are drawn and announced as one string,
			     so a screen of grey boxes says whether the library is broken or behind. -->
			{#if reason}
				<Tooltip label={reason} placement="bottom">
					<span class="flat still" aria-label={placeholder}>
						<span class="shimmer-label">{placeholder}</span>
					</span>
				</Tooltip>
			{:else}
				<span class="flat still" aria-label={placeholder}>
					<span class="shimmer-label">{placeholder}</span>
				</span>
			{/if}
		{:else if importing || !thumbSrc}
			<!-- Accepted and still settling, so the library never seems to swallow a file; the word
			     says what the shimmer means. -->
			<span class="shimmer" aria-label="Importing">
				<span class="shimmer-label">Importing&hellip;</span>
			</span>
		{:else}
			<img src={thumbSrc} alt="" loading="lazy" decoding="async" onerror={() => (broken = true)} />

			{#if playing && previewSrc}
				<!-- Muted and inline, or a browser will refuse to play it without a gesture. -->
				<video src={previewSrc} autoplay muted loop playsinline disablepictureinpicture></video>
			{/if}

			<span class="scrim"></span>

			{#if resumeAt !== null}
				<!-- Where you got to, along the bottom edge. `aria-hidden`: fifty tiles reading out "38 per
				     cent" would bury the names, and opening the file says where it starts. The width is a
				     `style:` directive (see the top of this file). -->
				<span class="progress" aria-hidden="true">
					<span class="progress-fill" style:inline-size="{resumeAt}%"></span>
				</span>
			{/if}

			{#if badge && tileMarks.shows(badgeMark)}
				<!-- One corner, two kinds of fact: the class stays `duration`, where the corner is
				     defined. `data-format`, not a class, since it draws nothing and only states a fact
				     the stylesheet's face choice reads. -->
				<span
					class="duration tile-badge {tileMarks.revealed(badgeMark)}"
					data-format={item.media_type === 'gif' ? 'gif' : undefined}
				>
					{badge}
				</span>
			{/if}
		{/if}
	</button>

	<!--
		What is true of this file besides what it looks like, in the corner opposite the clock: a
		SIBLING of the button, so each mark can be a button of its own. Restricted wins over shared,
		as everywhere.
	-->
	{#if showMarks && anyMark}
		<span class="marks">
			<!-- The file is not where Sift last saw it: first, as the one mark saying the tile is not
			     what it looks like (the thumbnail outlives the bytes). Not a button, since an unmounted
			     drive and a deleted archive look alike from here. Its own glyph in the warning ink,
			     not the hidden mark's eye (`icons.ts`). -->
			{#if showsGone}
				<Tooltip label="Sift can't reach this file. It's still in the library." placement="bottom">
					<span class="mark tile-badge tile-badge-square gone">
						<Icon name="unknown_document" size={16} />
					</span>
				</Tooltip>
			{/if}
			<!-- EVERY MARK DRAWN ALWAYS COMES BEFORE EVERY MARK WAITING FOR THE HOVER: one list
			     (`TOP_ROW`) reordered by `tileMarks.restingFirst`, in the markup rather than CSS
			     `order`, so what is seen and what the keyboard walks stay one order. -->
			{#snippet pinnedMark()}
				<!-- Kept at the top of this wall by this account, which is why it is first on it. A mark,
				     not a button: the pin is set from the menu and the bar. -->
				{#if showsPinned}
					<Tooltip label="Pinned to the top" placement="bottom">
						<span
							class="mark tile-badge tile-badge-square pinned {tileMarks.revealed(PINNED_MARK)}"
						>
							<Icon name="keep" size={16} />
						</span>
					</Tooltip>
				{/if}
			{/snippet}
			{#snippet seenMark()}
				<!-- Already seen, with the count, behind the views mark's own setting
				     (`Settings > Appearance`): the small watched set is the one worth marking, early in
				     the row so the number does not move from tile to tile. -->
				{#if showsSeen}
					<!-- VIEWED, the word the rail and the `views` field already use. -->
					<Tooltip
						label={item.views === 1 ? 'Viewed once' : `Viewed ${item.views} times`}
						placement="bottom"
					>
						<!-- The number is its own element so only the digits take the ink correction. -->
						<span class="mark tile-badge seen {tileMarks.revealed(VIEWS_MARK)}">
							<Icon name="visibility" size={16} />
							<span class="tally">{item.views}</span>
						</span>
					</Tooltip>
				{/if}
			{/snippet}
			{#snippet oCountMark()}
				{#if showsOCount}
					<!-- The O counter, shaped like the watched count; the number is on the row already. -->
					<Tooltip
						label={item.o_count === 1 ? 'O counter: 1' : `O counter: ${item.o_count}`}
						placement="bottom"
					>
						<span class="mark tile-badge seen {tileMarks.revealed(O_COUNT_MARK)}">
							<Icon name="water_drop" size={16} />
							<span class="tally">{item.o_count}</span>
						</span>
					</Tooltip>
				{/if}
			{/snippet}
			{#snippet sharingMark()}
				{#if mark}
					<!-- The status word wears its colour and the sentence does not, as on every wall. -->
					<Tooltip
						lead={mark.verb}
						tone={mark.kind === 'restricted' ? 'restrict' : mark.kind === 'both' ? 'both' : 'share'}
						label={mark.rest}
						placement="bottom"
					>
						<button
							type="button"
							class="mark tile-badge tile-badge-square {tileMarks.revealed(SHARING_MARK)}"
							class:restricted={mark.kind === 'restricted'}
							class:both={mark.kind === 'both'}
							aria-label="{mark.words}. Open sharing"
							onclick={() => onmark?.(item.id)}
						>
							<Icon name={mark.icon} size={16} filled={mark.filled} />
						</button>
					</Tooltip>
				{/if}
			{/snippet}
			{#snippet hiddenMark()}
				<!-- In the vault: drawn only where a concealed file is actually shown (Hidden, or with the
				     vault unlocked). Not governed by the sharing marks' switch. -->
				{#if showsHidden}
					<!-- Straight to the panel answering which thing is hiding this (the file, a folder, a
					     person), not the legend or Sharing. -->
					<Tooltip
						lead={HIDDEN_VERB}
						tone="hidden"
						label="{HIDDEN_REST}. {CLICK_THROUGH}"
						placement="bottom"
					>
						<button
							type="button"
							class="mark tile-badge tile-badge-square vaulted {tileMarks.revealed(HIDDEN_MARK)}"
							aria-label="{HIDDEN_WORDS}. Open hidden"
							onclick={() => onhidden?.(item.id)}
						>
							<!-- Solid where the switch is on this very file, hollow where it is inherited
							     (`hidden_here`), the fill rule of the sharing mark and the hidden panel. -->
							<Icon name="visibility_off" size={16} filled={item.hidden_here === true} />
						</button>
					</Tooltip>
				{/if}
			{/snippet}
			{#each tileMarks.restingFirst(TOP_ROW) as key (key)}
				{@const draw = {
					[PINNED_MARK]: pinnedMark,
					[VIEWS_MARK]: seenMark,
					[O_COUNT_MARK]: oCountMark,
					[SHARING_MARK]: sharingMark,
					[HIDDEN_MARK]: hiddenMark
				}[key]}
				{@render draw()}
			{/each}
		</span>
	{/if}

	{#if controls && showControls}
		<span class="controls">{@render controls(item.id)}</span>
	{/if}

	<!--
		Picked, drawn as its own thing over the tile: a `box-shadow` on the tile would lose to the
		hover shadow exactly under the pointer that just picked it. A sibling, outside the button that
		swallows clicks.
	-->
	{#if swapRefused}
		<!-- Will not go in the swap: the pick's look in the danger colour, taking no presses; a press
		     on the tile says why. -->
		<PickMark purpose="refused" />
	{:else if swapPicked}
		<!-- The one pick look (`PickMark`), over the picture and taking no presses: pressing the
		     tile again takes the pick off. -->
		<PickMark purpose="swap" />
	{/if}
	{#if picked}
		<span class="ring" aria-hidden="true"></span>
		<!-- The tick, so selected is said in shape as well as by the ring's colour. The button's
		     `aria-pressed` is what a screen reader hears; this is for the eye alone. -->
		<span class="selected-check" aria-hidden="true"><Icon name="check" size={16} /></span>
	{/if}
</div>

<style>
	.tile-frame {
		position: relative;
		flex: none;
		/* Where a pick (`PickMark`) sits: above a hovered tile's lift (2), under the ring (4). */
		--pick-radius: var(--tile-radius);
		--pick-layer: 3;
		/* `size`, not `inline-size`: the grid fixes HEIGHT, so height is how much room there is. */
		container-type: size;
	}

	.tile {
		position: relative;
		display: block;
		width: 100%;
		height: 100%;
		padding: 0;
		border: 0;
		border-radius: var(--tile-radius);
		background: var(--sift-surface-2);
		overflow: hidden;
		cursor: pointer;
		outline: none;
		transition:
			transform var(--dur-fast) var(--ease),
			translate var(--dur-fast) var(--ease),
			box-shadow var(--dur-fast) var(--ease);
	}

	/* Focus as a layer OVER the picture (`--focus-ring`): an outline or shadow on the button would
	   be painted under the picture. */
	.tile:focus-visible::after {
		content: '';
		position: absolute;
		inset: 0;
		border-radius: inherit;
		box-shadow: var(--focus-ring);
		pointer-events: none;
		z-index: 3;
	}

	/*
	 * HOVER is the lift: the tile rises, casts its shadow, and its picture grows inside its own clip
	 * (scaling the tile would move the picture past the ring and re-rasterise the chips). No ring:
	 * a ring means selected. The rise translates everything the frame holds; the frame stays, so
	 * the hover area does not move. `:has(:focus-visible)`, since `:focus-within` lights a tile for
	 * mouse focus a closing panel restores.
	 */
	.tile-frame:hover:not(:has(> .concealed)) > :is(.tile, .marks, .controls, .ring, .selected-check),
	.tile-frame:has(:focus-visible):not(:has(> .concealed))
		> :is(.tile, .marks, .controls, .ring, .selected-check) {
		translate: 0 var(--lift-y);
	}

	.tile-frame > :is(.marks, .controls, .ring, .selected-check) {
		transition: translate var(--dur-fast) var(--ease);
	}

	.tile-frame:hover .tile:not(.concealed),
	.tile-frame:has(:focus-visible) .tile:not(.concealed) {
		box-shadow: var(--elev-tile-lift);
		z-index: 2;
	}

	/* PRESSED: the tile settles back onto the wall under the press, shadow and all. */
	.tile-frame:has(> .tile:active:not(.concealed))
		> :is(.tile, .marks, .controls, .ring, .selected-check) {
		translate: none;
	}

	.tile-frame .tile:not(.concealed):active {
		box-shadow: none;
	}

	/* Not on a picked tile: its picture is already pulled in behind the ring, and growing it would
	   fill the gutter the ring sits in. */
	.tile-frame:hover .tile:not(.concealed):not(.picked) :is(img, video),
	.tile-frame:has(:focus-visible) .tile:not(.concealed):not(.picked) :is(img, video) {
		scale: 1.04;
	}

	.tile.concealed {
		cursor: default;
	}

	/* SELECTED: the ring, the pulled-in picture and the tick (`--selected-ring`). An inset ring, so
	   picking reflows nothing; above the lift's stacking context. */
	.ring {
		position: absolute;
		inset: 0;
		z-index: 4;
		border-radius: var(--tile-radius);
		box-shadow: var(--selected-ring);
		pointer-events: none;
	}

	/* The picture pulls in behind the ring by the ring's width (a scale would give unequal margins),
	   with the inner radius the outer minus that gap, so the curves are concentric (`app.css`). */
	.tile.picked {
		padding: var(--selected-ring-width);
	}

	.tile.picked img,
	.tile.picked video {
		border-radius: calc(var(--tile-radius) - var(--selected-ring-width));
	}

	img,
	video {
		width: 100%;
		height: 100%;
		transition: scale var(--dur-fast) var(--ease);
		/* The tile has this item's own proportions; cover only absorbs a pixel of rounding. */
		object-fit: cover;
		display: block;
	}

	video {
		position: absolute;
		inset: 0;
	}

	/* The shimmer's still twin, with no movement, since nothing is happening. `.flat` is the picture
	   area drawn flat for want of a picture; `Empty` is a screen's sentence. */
	.flat {
		display: block;
		width: 100%;
		height: 100%;
		background: var(--sift-surface-3);
	}

	.flat.missing {
		display: grid;
		place-items: center;
		color: var(--sift-ink-3);
	}

	/* Centred like the shimmer, in its label's type: the only difference is whether anything is coming. */
	.flat.still {
		display: grid;
		place-items: center;
	}

	.shimmer {
		display: grid;
		place-items: center;
		width: 100%;
		height: 100%;
		background: linear-gradient(
			90deg,
			var(--sift-surface-2) 0%,
			var(--sift-surface-3) 50%,
			var(--sift-surface-2) 100%
		);
		background-size: 200% 100%;
		--pan-from: 200%;
		--pan-to: -200%;
		animation: pan var(--dur-loop) linear infinite;
	}

	/* Muted, a status not a title, and clipped to one line on a narrow tile. */
	.shimmer-label {
		max-width: 100%;
		padding-inline: var(--space-2);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The scrim only exists to keep the duration and the controls readable over a bright picture. */
	.scrim {
		position: absolute;
		inset: 0;
		background: linear-gradient(to top, rgb(6 7 10 / 0.55), transparent 45%);
		opacity: 0;
		transition: opacity var(--dur-fast) var(--ease);
		pointer-events: none;
	}

	.tile-frame:hover .scrim,
	.tile:focus-visible .scrim {
		opacity: 1;
	}

	/*
	 * The resume bar: a track along the very bottom edge and the accent filled across it. ALWAYS
	 * drawn, unlike the scrim, since it is a fact about the file. The track is the media scrim token;
	 * the fill is the accent, this app's "yours". The chips' inset leaves the bottom edge free.
	 */
	.progress {
		position: absolute;
		inset-block-end: 0;
		inset-inline: 0;
		block-size: var(--progress-line);
		background: var(--sift-scrim);
		pointer-events: none;
	}

	.progress-fill {
		display: block;
		block-size: 100%;
		background: var(--sift-accent);
	}

	/*
	 * The corner furniture (the clock in one corner, the marks in the other) as one shape:
	 * `.tile-badge` in `app.css` with its `--tile-chip-*` numbers, shared by every component that
	 * draws it, so two corners of one photograph never differ. Here is only where each corner is;
	 * `tile-badge-square` marks the square ones.
	 */

	.duration {
		position: absolute;
		right: var(--tile-chip-inset);
		bottom: var(--tile-chip-inset);
		/* Its whole content is text, so the ink correction is on the chip. See `--tile-chip-ink`. */
		padding-block-start: var(--tile-chip-ink);
		pointer-events: none;
	}

	/*
	 * The marks, in the top left, since the bottom belongs to the controls: a row, so two chips sit
	 * side by side. `.seen` carries a number, so it is not square, with less room on the glyph's
	 * side; it keeps the marks' one ink.
	 */
	.seen {
		padding-inline-start: var(--space-1);
	}

	/* The warning colour, not the destructive red: nothing is lost; the file is not where Sift looked. */
	.gone {
		color: var(--sift-warn);
	}

	/* The pin in the marks' one ink: the accent means selected or taking a drop. Kept for the markup
	   and the tests that name it. */
	.pinned {
		color: var(--sift-ink);
	}

	/* The count dropped onto the letters' line, apart from the already-centred glyph. */
	.tally {
		padding-block-start: var(--tile-chip-ink);
	}

	.marks {
		position: absolute;
		/* Above the button, whose hover lift makes a stacking context, or presses would open the asset. */
		z-index: 3;
		left: var(--tile-chip-inset);
		top: var(--tile-chip-inset);
		display: flex;
		align-items: center;
		/* ONE gap for the row, never a margin on a mark. */
		gap: var(--space-1);
	}

	/* A mark over the picture, not a chip; its shape is the clock's, and it is also a control. */
	.mark {
		border: 0;
		cursor: pointer;
	}

	/* On a phone a pressable mark keeps its size and grows its reach to the touch target with an
	   invisible ring, lifted one step so a neighbouring count (which presses nothing) cannot cover
	   it. */
	@media (max-width: 767px) {
		button.mark {
			position: relative;
			z-index: 1;
		}

		button.mark::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	/* On a TILE, shared stays in the plain ink: green on every tile of a mostly shared library means
	   nothing. The two that need noticing keep their colours; elsewhere every state wears its own. */

	/* A restrict is the promise, and what somebody comes looking for when a share seems not to work. */
	.mark.restricted {
		color: var(--sift-bad-text);
	}

	/* Shared with somebody and kept from somebody else: amber, "look closer", the honest answer. */
	.mark.both {
		color: var(--sift-warn);
	}

	/* The ordinary state of everything on the Hidden screen, so the furniture's own ink. */
	.mark.vaulted {
		color: var(--sift-ink);
	}

	.controls {
		position: absolute;
		/* Above the button, which the lift gives a stacking context. */
		z-index: 3;
		/* The chips' own inset, so the heart and the clock share one line along the bottom. */
		inset: auto var(--tile-chip-inset) var(--tile-chip-inset) var(--tile-chip-inset);
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* The heart and the score fade on their own preferences (`app.css`), keyed off the frame, since
	   the overlay is the button's sibling. */

	/*
	 * The smallest setting: at 120px the picture is the point, so every mark but `gone` goes (it
	 * matters most here, and nobody chose it). "Everything except" hides a future mark by default;
	 * `:is(.gone, :has(.gone))` covers it wrapped in a tooltip or not.
	 */
	@container (max-height: 140px) {
		.marks > *:not(:is(.gone, :has(.gone))) {
			display: none;
		}
	}

	/* Otherwise one size at every notch: a breakpoint straddled by notches would resize a badge as
	   the slider moves. A future step-down belongs on the notches, never a pixel height. */

	:global(:root[data-motion='reduce']) .shimmer {
		animation: none;
	}

	/* Still, the lift is only its shadow: the rise and the settle are movement. */
	:global(:root[data-motion='reduce'])
		.tile-frame
		> :is(.tile, .marks, .controls, .ring, .selected-check) {
		translate: none;
		transition: none;
	}

	:global(:root[data-motion='reduce']) .tile,
	:global(:root[data-motion='reduce']) .scrim {
		transition: none;
	}

	:global(:root[data-motion='reduce']) .tile-frame:hover .tile:not(.concealed) :is(img, video) {
		scale: 1;
		transition: none;
	}
</style>
