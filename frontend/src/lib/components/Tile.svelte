<script lang="ts">
	/* WHY NOT SHARED: button: the tile IS the control (a picture with nothing else in it) and the two badges in
	   its corner are marks on a photograph. `Pressable` lifts on hover and draws its ring as a
	   box-shadow on itself; this tile zooms the PICTURE inside its own clip and draws the ring as a
	   SIBLING (both explained below), and its 20px badges are smaller than a button's minimum. */

	/*
	 * One item in the grid, with `controls` drawn on hover and `onopen`. The width is a `style:`
	 * directive: the policy refuses inline style attributes.
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
		/** Whether the wall honours the pin, so no wall marks an order it lacks. */
		pinnable?: boolean;
		width: number;
		height: number;
		thumbSrc?: string;
		previewSrc?: string;
		playing?: boolean;
		picked?: boolean;
		/** The swap pick's look, apart from the selection's. */
		swapPicked?: boolean;
		/** A file no swap will send, in the danger colour (`PickMark`'s `refused`). */
		swapRefused?: boolean;
		importing?: boolean;
		/** A still that will never arrive, said in words, not a shimmer forever. */
		placeholder?: string;
		reason?: string;
		controls?: Snippet<[string]>;
		onopen?: (id: string) => void;
		onmark?: (id: string) => void;
		/* Hiding, not sharing: its own panel. */
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

	/* GIF says GIF; a video is timed; a still gets nothing. */
	const badge = $derived(
		item.media_type === 'image'
			? null
			: item.media_type === 'gif'
				? 'GIF'
				: length(item.duration_ms)
	);

	const badgeMark = $derived(item.media_type === 'gif' ? GIF_MARK : DURATION_MARK);

	/* The server judges `resume_ms`; clamped, since a re-probed shorter duration could overflow. */
	const resumeAt = $derived(
		item.resume_ms != null && item.duration_ms
			? Math.min(100, Math.max(0, (item.resume_ms / item.duration_ms) * 100))
			: null
	);

	/* Reset on a new address, or a recycled tile keeps the old failure. */
	let broken = $state(false);
	$effect(() => {
		void thumbSrc;
		broken = false;
	});

	const showControls = $derived(!item.concealed && !importing && Boolean(thumbSrc));

	/* Marks need no picture; none on a concealed tile or while importing. */
	const showMarks = $derived(!item.concealed && !importing);

	/* One string for the tooltip and the accessible name. */
	const mark = $derived(markFor(tileMarks.shows(SHARING_MARK) ? item : {}, { file: true }));

	/* `gone` answers to no preference (`tile_marks` on the server). */
	const showsGone = $derived(item.unreachable === true);
	const showsPinned = $derived(item.pinned === true && pinnable && tileMarks.shows(PINNED_MARK));
	const showsSeen = $derived((item.views ?? 0) > 0 && tileMarks.shows(VIEWS_MARK));
	const showsOCount = $derived((item.o_count ?? 0) > 0 && tileMarks.shows(O_COUNT_MARK));
	const showsHidden = $derived(item.hidden === true && tileMarks.shows(HIDDEN_MARK));
	const anyMark = $derived(
		showsGone || showsPinned || showsSeen || showsOCount || showsHidden || Boolean(mark)
	);

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

<!-- The controls sit BESIDE the button, never inside it. -->
<div class="tile-frame" style:width="{width}px" style:height="{height}px">
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
			<!-- Says nothing: the `Withheld` face, no bytes sent. -->
			<Withheld label="Hidden" />
		{:else if broken}
			<span class="flat missing">
				<Icon name="hide_image" size={16} label="No preview" />
			</span>
		{:else if placeholder && !thumbSrc}
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
			<span class="shimmer" aria-label="Importing">
				<span class="shimmer-label">Importing&hellip;</span>
			</span>
		{:else}
			<img src={thumbSrc} alt="" loading="lazy" decoding="async" onerror={() => (broken = true)} />

			{#if playing && previewSrc}
				<!-- Muted and inline, or the browser refuses to play it. -->
				<video src={previewSrc} autoplay muted loop playsinline disablepictureinpicture></video>
			{/if}

			<span class="scrim"></span>

			{#if resumeAt !== null}
				<span class="progress" aria-hidden="true">
					<span class="progress-fill" style:inline-size="{resumeAt}%"></span>
				</span>
			{/if}

			{#if badge && tileMarks.shows(badgeMark)}
				<span
					class="duration tile-badge {tileMarks.revealed(badgeMark)}"
					data-format={item.media_type === 'gif' ? 'gif' : undefined}
				>
					{badge}
				</span>
			{/if}
		{/if}
	</button>

	<!-- Marks are SIBLINGS of the button, so each can be a button. -->
	{#if showMarks && anyMark}
		<span class="marks">
			<!-- Not where Sift last saw it: first, and not a button. -->
			{#if showsGone}
				<Tooltip label="Sift can't reach this file. It's still in the library." placement="bottom">
					<span class="mark tile-badge tile-badge-square gone">
						<Icon name="unknown_document" size={16} />
					</span>
				</Tooltip>
			{/if}
			<!--
			Resting marks first, in the markup (`tileMarks.restingFirst`), so the keyboard walks the
			same order.
			-->
			{#snippet pinnedMark()}
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
				{#if showsSeen}
					<Tooltip
						label={item.views === 1 ? 'Viewed once' : `Viewed ${item.views} times`}
						placement="bottom"
					>
						<span class="mark tile-badge seen {tileMarks.revealed(VIEWS_MARK)}">
							<Icon name="visibility" size={16} />
							<span class="tally">{item.views}</span>
						</span>
					</Tooltip>
				{/if}
			{/snippet}
			{#snippet oCountMark()}
				{#if showsOCount}
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
				{#if showsHidden}
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
							<!-- Solid on this very file, hollow inherited (`hidden_here`). -->
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

	<!-- A sibling layer, or the hover shadow would hide the pick. -->
	{#if swapRefused}
		<PickMark purpose="refused" />
	{:else if swapPicked}
		<PickMark purpose="swap" />
	{/if}
	{#if picked}
		<span class="ring" aria-hidden="true"></span>
		<!-- The tick for the eye; `aria-pressed` for a screen reader. -->
		<span class="selected-check" aria-hidden="true"><Icon name="check" size={16} /></span>
	{/if}
</div>

<style>
	.tile-frame {
		position: relative;
		flex: none;
		--pick-radius: var(--tile-radius);
		--pick-layer: 3;
		/* `size`: the grid fixes HEIGHT. */
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

	/* Focus as a layer OVER the picture. */
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
	 * HOVER lifts the frame's contents and grows the picture inside its clip;
	 * `:has(:focus-visible)`, not `:focus-within`.
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

	.tile-frame:has(> .tile:active:not(.concealed))
		> :is(.tile, .marks, .controls, .ring, .selected-check) {
		translate: none;
	}

	.tile-frame .tile:not(.concealed):active {
		box-shadow: none;
	}

	.tile-frame:hover .tile:not(.concealed):not(.picked) :is(img, video),
	.tile-frame:has(:focus-visible) .tile:not(.concealed):not(.picked) :is(img, video) {
		scale: 1.04;
	}

	.tile.concealed {
		cursor: default;
	}

	/* SELECTED: an inset ring, reflowing nothing. */
	.ring {
		position: absolute;
		inset: 0;
		z-index: 4;
		border-radius: var(--tile-radius);
		box-shadow: var(--selected-ring);
		pointer-events: none;
	}

	/* Pulled in by the ring's width, concentric (`app.css`). */
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
		object-fit: cover;
		display: block;
	}

	video {
		position: absolute;
		inset: 0;
	}

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

	.shimmer-label {
		max-width: 100%;
		padding-inline: var(--space-2);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

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

	/* The resume bar, ALWAYS drawn: a fact about the file. */
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

	/* `.tile-badge` in `app.css`, shared by every corner. */

	.duration {
		position: absolute;
		right: var(--tile-chip-inset);
		bottom: var(--tile-chip-inset);
		padding-block-start: var(--tile-chip-ink);
		pointer-events: none;
	}

	.seen {
		padding-inline-start: var(--space-1);
	}

	/* Warning, not red: nothing is lost. */
	.gone {
		color: var(--sift-warn);
	}

	.pinned {
		color: var(--sift-ink);
	}

	.tally {
		padding-block-start: var(--tile-chip-ink);
	}

	.marks {
		position: absolute;
		/* Above the button's stacking context. */
		z-index: 3;
		left: var(--tile-chip-inset);
		top: var(--tile-chip-inset);
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.mark {
		border: 0;
		cursor: pointer;
	}

	/* On a phone a pressable mark grows its reach, not its size. */
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

	/* Shared stays plain on a tile; the two that need noticing keep their colours. */

	.mark.restricted {
		color: var(--sift-bad-text);
	}

	.mark.both {
		color: var(--sift-warn);
	}

	.mark.vaulted {
		color: var(--sift-ink);
	}

	.controls {
		position: absolute;
		z-index: 3;
		inset: auto var(--tile-chip-inset) var(--tile-chip-inset) var(--tile-chip-inset);
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* The smallest setting drops every mark but `gone`. */
	@container (max-height: 140px) {
		.marks > *:not(:is(.gone, :has(.gone))) {
			display: none;
		}
	}

	:global(:root[data-motion='reduce']) .shimmer {
		animation: none;
	}

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
