<script lang="ts">
	/*
	 * A Saved Layout drawn: the shape with its places numbered and the strip under it, then what
	 * each cell plays. The hover bubble and the save dialog both draw this, so they cannot disagree.
	 */
	import {
		area,
		layout as layoutNamed,
		named,
		readShape,
		template,
		type Shape
	} from '$lib/theater/layouts';
	import FilterChip from '$lib/components/shell/FilterChip.svelte';
	import { typedParts } from '$lib/search/query-parts';
	import { FIELDS } from '$lib/search/search.svelte';
	import { RANDOM } from '$lib/grid/sort-state.svelte';
	import { CELL_DEFAULT_ORDER, CELL_ORDERS } from '$lib/theater/orders';
	import { aspectLabel, isAspect } from '$lib/theater/aspects';
	import { isLoopMode, loopModeLabel } from '$lib/player/loop-modes';
	import { sayLength } from '$lib/shell/duration';
	import { MEDIA_KIND_LABELS } from '$lib/theater/wall.svelte';
	import type { MediaKind } from '$lib/theater/cell.svelte';
	import type { SavedCell } from '$lib/theater/presets.svelte';

	interface Props {
		wall: { layout: string; shape: unknown; strip?: number; cells: SavedCell[] };
		/** Each cell's settings as well as its source, for the save dialog. */
		detailed?: boolean;
	}

	let { wall, detailed = false }: Props = $props();

	// The fall-back the wall itself takes when it loads one, so the picture and the result agree.
	const shape = $derived<Shape>(readShape(wall.shape) ?? layoutNamed(wall.layout).shape);
	const places = $derived(shape.slots.length);
	const strip = $derived(wall.strip ?? Math.max(0, wall.cells.length - places));
	const shapeName = $derived.by(() => {
		const id = named(shape, strip);
		return id ? layoutNamed(id).label : 'Custom';
	});
	const grid = $derived(template(shape));

	function orderWords(sort: string | null): string {
		const value = sort || CELL_DEFAULT_ORDER;
		const label = CELL_ORDERS.find((one) => one.value === value)?.label ?? value;
		return value === RANDOM ? `${label}, a fresh shuffle each time` : label;
	}

	// The same fall-backs `Cell` takes on load, so a row never promises what loading will not give.
	function settings(cell: SavedCell, preview: boolean, from: string): string {
		const kind = MEDIA_KIND_LABELS[cell.media_kind as MediaKind] ?? MEDIA_KIND_LABELS.video_gif;
		const end = isLoopMode(cell.end_behaviour) ? cell.end_behaviour : 'loop_all';
		const timer = cell.timer_seconds
			? `Move on after ${sayLength(cell.timer_seconds)}`
			: 'No timer';
		const aspect = aspectLabel(isAspect(cell.aspect) ? cell.aspect : 'dynamic');
		const parts = [
			kind,
			orderWords(cell.sort),
			loopModeLabel(end),
			timer,
			`Shape: ${aspect}`
		].slice(kind === from ? 1 : 0);
		return (preview ? ['Preview', ...parts] : parts).join(', ');
	}

	// Typed words have no chips, so they are said as typed; nothing set is the whole library.
	const cells = $derived(
		wall.cells.map((cell, at) => {
			const from = cell.source.trim() || 'Everything';
			return {
				at: at + 1,
				parts: typedParts(cell.source, FIELDS),
				from,
				settings: settings(cell, at >= places, from)
			};
		})
	);
	const inFocus = $derived(cells.slice(0, places));
	const previews = $derived(cells.slice(places));
</script>

<div class="snapshot">
	{#if detailed}
		<p class="named">{shapeName}</p>
	{/if}
	<!-- The property through the CSSOM: the app's policy refuses style attributes. -->
	<div class="preview" style:--wall={grid}>
		{#each inFocus as one, at (one.at)}
			<div class="stall" style:grid-area={area(shape.slots[at])}>
				<span class="at">{one.at}</span>
			</div>
		{/each}
	</div>
	{#if previews.length > 0}
		<div class="preview under">
			{#each previews as one (one.at)}
				<div class="stall"><span class="at">{one.at}</span></div>
			{/each}
		</div>
	{/if}

	<ul class="sources">
		{#each cells as one (one.at)}
			<li>
				<span class="at">{one.at}</span>
				<span class="cell">
					{#if one.parts.length > 0}
						<span class="parts">
							{#each one.parts as part, index (index)}
								<FilterChip
									field={part.field}
									values={part.values}
									all={part.all}
									excluded={part.excluded}
								/>
							{/each}
						</span>
					{:else}
						<span class="from">{one.from}</span>
					{/if}
					{#if detailed}
						<span class="behaviour">{one.settings}</span>
					{/if}
				</span>
			</li>
		{/each}
	</ul>
</div>

<style>
	/* Absolute sizes: a tooltip is as wide as its content, so shares of it would be a loop. */
	.snapshot {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}

	.named {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-label);
	}

	.preview {
		display: grid;
		grid-template: var(--wall, '. .' 1fr / 1fr 1fr);
		gap: 3px;
		inline-size: 260px;
		block-size: 140px;
	}

	/* Taller than the real strip looks, so 'Everything' is not cut in half. */
	.preview.under {
		display: flex;
		block-size: 40px;
	}

	.preview.under .stall {
		flex: 1;
		min-inline-size: 0;
	}

	.stall {
		display: flex;
		flex-direction: column;
		gap: 2px;
		overflow: hidden;
		min-inline-size: 0;
		padding: var(--space-1);
		border-radius: var(--radius-sm);
		background: var(--sift-surface-4);
	}

	/* `check_handrolled.js` reads a `.chip` rule as a hand-made chip, so the row is `.parts`. */
	.sources {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		margin-block-start: var(--space-1);
		padding: 0;
		list-style: none;
	}

	.sources li {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.sources .at {
		flex: none;
		inline-size: 1.25rem;
	}

	.cell {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.parts {
		display: flex;
		flex-wrap: wrap;
		gap: 2px;
		min-inline-size: 0;
	}

	.at {
		color: var(--sift-ink-3);
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
	}

	.from {
		color: var(--sift-ink-2);
		font: var(--text-label);
		overflow-wrap: anywhere;
	}

	.behaviour {
		color: var(--sift-ink-3);
		font: var(--text-label);
	}
</style>
