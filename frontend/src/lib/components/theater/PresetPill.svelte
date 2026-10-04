<script lang="ts">
	/* DRESSED BY: KeptPill (the pill, the bubble, the three dots and the right-click are all its:
	   this file declares what a saved WALL's verbs are and what its bubble draws). */

	/*
	 * One kept wall, as a pill: the same pill a kept filter is.
	 *
	 * A text button with a cross beside it would give no way to tell two apart without loading one,
	 * no rename, no right-click, and a delete with no confirmation a few pixels from the thing that
	 * opens it. A kept wall is the same kind of object as a kept filter and is the same control;
	 * see `KeptPill` for the argument.
	 *
	 * ## What its bubble draws, and why it is two things rather than one
	 *
	 * The SHAPE first, big enough to read: the same picture the layout list draws, from the same
	 * `grid-template` the wall itself uses, so it cannot come to disagree with what loading it
	 * produces. That answers "what will the screen look like".
	 *
	 * Then what makes this wall different from another one of the same shape: what each cell draws
	 * from, numbered the way the cells are. Two walls can both be a 2x2 grid and be completely
	 * different things to watch, and the shape alone would say they were the same.
	 */
	import KeptPill from '$lib/components/common/KeptPill.svelte';
	import {
		area,
		layout as layoutNamed,
		readShape,
		template,
		type Shape
	} from '$lib/theater/layouts';
	import FilterChip from '$lib/components/shell/FilterChip.svelte';
	import { typedParts } from '$lib/search/query-parts';
	import { FIELDS } from '$lib/search/search.svelte';
	import type { Preset } from '$lib/theater/presets.svelte';
	import type { Verb } from '$lib/components/common/verbs';

	interface Props {
		kept: Preset;
		onopen: (kept: Preset) => void;
		onupdate?: (kept: Preset) => void;
		onrename?: (kept: Preset) => void;
		onremove?: (kept: Preset) => void;
		/** A write against this one is in flight. See `KeptPill.busy`, which is what draws it. */
		busy?: boolean;
	}

	let { kept, onopen, onupdate, onrename, onremove, busy = false }: Props = $props();

	/* The stored shape when it can be drawn, and otherwise the layout it was also saved under: the
	   same fall-back the wall itself takes when it loads one, so the picture and the result agree. */
	const shape = $derived<Shape>(readShape(kept.shape) ?? layoutNamed(kept.layout).shape);

	/*
	 * What each cell draws from, in the order the cells sit in.
	 *
	 * As the query somebody TYPED, rather than as the bar's chips. A cell stores a typed query (`people:jane`), and `partsOf`, which is what turns
	 * a kept filter into chips, reads only the NAMED spelling and skips `q` outright. Handed a cell's
	 * source it returns an empty list, so every wall would draw a shape with nothing under it and
	 * nothing anywhere saying why.
	 *
	 * A cell with no source draws the whole library, which is a fact worth saying rather than a blank.
	 */
	/*
	 * What each cell draws from, as the CHIPS the rest of the app says a filter with.
	 *
	 * A saved filter's bubble on Browse is a row of `FilterChip`s, so in words this bubble, the one
	 * place that shows what a saved WALL holds, would speak a different language from the one place
	 * that shows what a saved FILTER holds, about the same thing.
	 *
	 * The words stay as the fallback and that is not a hedge: a cell pointed at free words has no
	 * (`people=jane`) and skips `q` outright, so a cell pointed at a typed search has no chips to
	 * draw and its own words are the only honest answer. A cell with nothing set draws the whole
	 * library, which is worth saying rather than leaving blank.
	 */
	const sources = $derived(
		kept.cells.map((cell, at) => ({
			at: at + 1,
			parts: typedParts(cell.source, FIELDS),
			from: cell.source.trim() || 'Everything'
		}))
	);

	/*
	 * The wall's places, and then the strip.
	 *
	 * A wall in Center stage carries more cells than its shape has places (the extra ones are the
	 * previews under it) so placing every cell by `shape.slots[at]` puts the last five nowhere at
	 * all. The split is the rule the wall itself uses: the shape first, the strip after.
	 */
	const inFocus = $derived(sources.slice(0, shape.slots.length));
	const previews = $derived(sources.slice(shape.slots.length));

	/* The shape as a `grid-template`, from the same function the wall itself is drawn with, so the
	   picture cannot come to disagree with what loading this produces. */
	const grid = $derived(template(shape));

	const verbs = $derived.by(() => {
		const rows: Verb[] = [];
		if (onrename) {
			rows.push({
				id: 'rename',
				label: 'Rename',
				icon: 'edit_square',
				group: 'change',
				singleOnly: true,
				run: () => onrename(kept)
			});
		}
		if (onupdate) {
			rows.push({
				id: 'update',
				label: 'Update from the wall on screen',
				icon: 'sync',
				group: 'change',
				singleOnly: true,
				run: () => onupdate(kept)
			});
		}
		if (onremove) {
			rows.push({
				id: 'remove',
				label: 'Delete',
				icon: 'delete',
				destructive: true,
				singleOnly: true,
				run: () => onremove(kept)
			});
		}
		return rows;
	});
</script>

<KeptPill
	wide
	id={kept.id}
	name={kept.name}
	{verbs}
	{busy}
	onapply={() => onopen(kept)}
	holds={preview}
/>

{#snippet preview()}
	<!--
		The shape, and then what each cell draws from.

		The picture shows the shape and the numbers, and the chips sit under it at the bubble's full
		width, one row per cell, each led by the number drawn in its block. Inside a block (about
		140px on a three-wide wall) a chip would wrap between its field and its value.

		The property is set through the CSSOM rather than a style attribute: the policy this app is
		served under refuses those, silently.
	-->
	<div class="picture">
		<div class="preview" style:--wall={grid}>
			{#each inFocus as one, at (one.at)}
				<div class="stall" style:grid-area={area(shape.slots[at])}>
					<span class="at">{one.at}</span>
				</div>
			{/each}
		</div>

		<!-- The strip, drawn as the row it is: same blocks, shorter box. -->
		{#if previews.length > 0}
			<div class="preview under">
				{#each previews as one (one.at)}
					<div class="stall"><span class="at">{one.at}</span></div>
				{/each}
			</div>
		{/if}

		<ul class="sources">
			{#each sources as one (one.at)}
				<li>
					<span class="at">{one.at}</span>
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
				</li>
			{/each}
		</ul>
	</div>
{/snippet}

<style>
	/*
	 * The wall, at a size somebody can read. Sized in absolute pixels rather than shares of the
	 * bubble: a tooltip is as wide as its content, so a picture measured against its container and
	 * a container measured against its picture would be a loop with no answer. 260 by 150 holds a
	 * few words in every cell of a three-wide wall.
	 *
	 * The wall and its strip, one above the other: the arrangement, at a size somebody can read.
	 */
	.picture {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}

	.preview {
		display: grid;
		grid-template: var(--wall, '. .' 1fr / 1fr 1fr);
		gap: 3px;
		inline-size: 260px;
		block-size: 140px;
	}

	/* The strip: five equal blocks in a row, and shorter than the wall above it, which is the
	   proportion the real one has. Wide enough that a preview's source is readable in a fifth of
	   the width. */
	/* Tall enough for the words rather than as short as the real strip looks: a block too short cuts
	   'Everything' in half, and a picture that cannot be read is not a preview of anything. */
	.preview.under {
		display: flex;
		block-size: 40px;
	}

	/* One cell's filter, wrapped inside a block. Each part is the shared `FilterChip` the bar draws,
	   so a filter looks the same here as it does anywhere else it is described.

	   Named for the ROLE rather than for what it holds. `check_handrolled.js` counts any `.chip` or
	   `.chips` RULE as a hand-declared chip, deliberately (a hand-rolled set is styled `.chips
	   .chip`) and it cannot tell a wrapper from a dressing. The row is the filter's `parts`, which
	   is what the data this iterates is called. */
	/* Each cell's filter, at the bubble's full width. See the snippet's own note. */
	.sources {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.sources li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.sources .at {
		flex: none;
		inline-size: 1.25rem;
	}

	.parts {
		display: flex;
		flex-wrap: wrap;
		gap: 2px;
		min-inline-size: 0;
	}

	.preview.under .stall {
		flex: 1;
		min-inline-size: 0;
	}

	/* One cell of the picture. The ground is the surface a cell of the real wall sits on, so the
	   diagram reads as the thing it is a diagram of rather than as a chart. */
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

	/* Which cell this is: the same number the keyboard talks to, the same one in the corner of the
	   real cell. In the corner of this one too, for the same reason. */
	.at {
		color: var(--sift-ink-3);
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
	}

	/* The query, as it was typed. Wrapped rather than cut off at one line: the cell is the width it
	   is, and a wall of three has narrow ones: two short lines say more than one clipped one. It
	   still stops rather than growing the cell, which is what `overflow: hidden` above is for. */
	.from {
		color: var(--sift-ink-2);
		font: var(--text-label);
		overflow-wrap: anywhere;
	}
</style>
