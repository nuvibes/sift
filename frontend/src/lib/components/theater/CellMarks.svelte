<script lang="ts">
	/*
	 * The marks a Theater cell draws over its picture: its number, the chosen cell's edge, the aiming
	 * mark, the flash when a cell is chosen by its number, the sound's square, the waiting filter's
	 * mark, and the echo of a key. Labels and states rather than controls, so none takes the pointer
	 * except the waiting mark, whose words are read on hover.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { KeyEcho, Tooltip } from '$lib/components/common';
	import type { CellEcho } from '$lib/theater/echoes';

	interface Props {
		/** Which cell this is, from one. */
		number: number;
		/** Whether the cell says its number; not in the corner panel. */
		numbered: boolean;
		/** Whether the wall's chrome has gone quiet, which the number and the edge follow. */
		quiet: boolean;
		/** Whether the marks saying this cell is being heard are lit. */
		marked: boolean;
		/** Whether this is the chosen cell, whose edge is drawn. */
		chosen: boolean;
		/** Whether the bar, the filter panel or a chip is aiming at this cell. */
		aimed: boolean;
		/** The press this cell is flashing for, or null. A number so a second press replays it. */
		flashing: number | null;
		/** The flash has finished. */
		onflashed: () => void;
		/** Whether a filter chosen while this cell plays waits for the file to end. */
		waits: boolean;
		/** What a key last did at this cell, or null. See `KeyEcho`. */
		echo: CellEcho | null;
	}

	let { number, numbered, quiet, marked, chosen, aimed, flashing, onflashed, waits, echo }: Props =
		$props();

	/** The waiting mark's words, on hover and to a screen reader alike, so the two cannot differ. */
	const WAITS_WORDS = 'New filter starts when this file ends';
</script>

<!-- The number sits in the stage's own layer, so it fades with the rest of the chrome rather than
     staying lit over a picture with nothing else on it. -->
{#if numbered}
	<span class="at" class:quiet class:beside={marked} aria-hidden="true">{number}</span>
{/if}

<!-- The mark Sift draws over anything about to be acted on: a dashed accent edge and the wash. -->
{#if aimed}
	<span class="aimed" aria-hidden="true"></span>
{/if}

<!-- The chosen cell's edge, a layer over the picture because an outline on the cell would be painted
     under the stage. Always mounted and faded, so moving the choice fades one edge out and the next
     in, and it goes with the chrome rather than staying lit. -->
<span class="picked" class:chosen class:quiet aria-hidden="true"></span>

<!-- Chosen by its number: the wash, up and away again. Keyed on the press so every press replays the
     wash, and cleared when the motion ends so one duration is written once. -->
{#key flashing}
	{#if flashing !== null}
		<span class="chose" aria-hidden="true" onanimationend={onflashed}></span>
	{/if}
{/key}

<!-- An outline (the cell's own, in its stylesheet) AND a glyph, faded rather than removed so the
     pair goes quiet on a clock instead of blinking out each time the sound moves. -->
<span class="hearing" class:up={marked} aria-hidden={!marked}>
	<Icon name="volume_up" size={20} label="You are hearing this" />
</span>

<!-- A filter chosen while this cell plays is taken up at the end of the file (see `Cell.narrowTo`);
     without a mark the press reads as ignored. A mark, not a button: it takes the pointer only so
     its words can be hovered. -->
{#if waits}
	<span class="waits">
		<Tooltip label={WAITS_WORDS} placement="bottom">
			<Icon name="filter_alt" size={20} label={WAITS_WORDS} />
		</Tooltip>
	</span>
{/if}

<!-- What a key just did at this cell. It shows itself when the count of presses rises, so only a key
     draws a badge. See `$lib/theater/echoes`. -->
<div class="echo-spot">
	<KeyEcho
		icon={echo?.icon ?? 'repeat'}
		label={echo?.label ?? ''}
		detail={echo?.detail}
		muted={echo?.muted ?? false}
		press={echo?.press ?? 0}
	/>
</div>

<style>
	/* The chosen cell's edge and the aiming mark are the drop target's line (the dashed accent a tile
	   wears while a download is held over it), written once for both so they cannot drift; the aiming
	   mark adds the wash. */
	.picked,
	.aimed {
		position: absolute;
		inset: 0;
		border: 2px dashed var(--sift-accent);
		border-radius: var(--stage-radius, 0);
		pointer-events: none;
	}

	.picked {
		z-index: 4;
		opacity: 0;
		transition: opacity var(--dur-slow) var(--ease);
	}

	.picked.chosen {
		opacity: 1;
	}

	.picked.chosen.quiet {
		opacity: 0;
	}

	/* A label, so it takes no pointer: a badge that did would eat the presses that choose a cell or
	   swap a preview in, and at nought opacity it would be an invisible hole. */
	.at {
		position: absolute;
		z-index: 4;
		pointer-events: none;
		inset-block-start: var(--space-2);
		inset-inline-start: var(--space-2);
		transition: opacity var(--dur-slow) var(--ease);
		min-inline-size: 1.5rem;
		padding: 2px var(--space-1);
		border-radius: var(--radius-sm);
		background: var(--sift-scrim);
		color: var(--sift-ink);
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
		text-align: center;
	}

	.at.quiet {
		opacity: 0;
	}

	/* While the sound's square owns the corner, the number sits beside it, level with its top. */
	.at.beside {
		inset-block-start: var(--space-3);
		inset-inline-start: calc(var(--space-3) + var(--space-8) + var(--space-1));
	}

	.aimed {
		z-index: 5;
		background: var(--sift-accent-wash);
	}

	/* Just chosen by its number. Three steps of the moderate token (in, held, out), because one each
	   way is too quick for the eye to find the cell on a wall of nine. */
	.chose {
		position: absolute;
		inset: 0;
		z-index: 5;
		border-radius: var(--stage-radius, 0);
		background: var(--sift-accent-wash);
		pointer-events: none;
		animation: flash calc(var(--dur-base) * 3) var(--ease) both;
	}

	/* The echo lands in the sound mark's place and over it: one shape in one corner. */
	.echo-spot {
		position: absolute;
		inset-block-start: var(--space-3);
		inset-inline-start: var(--space-3);
		z-index: 5;
		pointer-events: none;
	}

	/* Square, in the key echo's own shape, so a cell draws one kind of sound mark. */
	.hearing,
	.waits {
		opacity: 0;
		pointer-events: none;
		transition: opacity var(--dur-base) var(--ease);
		position: absolute;
		inset-block-start: var(--space-3);
		inset-inline-start: var(--space-3);
		z-index: 4;
		display: grid;
		place-items: center;
		inline-size: var(--space-8);
		block-size: var(--space-8);
		border-radius: var(--radius-md);
		background: var(--sift-scrim-strong);
		color: var(--sift-ink);
	}

	.hearing.up {
		opacity: 1;
	}

	/* Drawn only while there is a waiting filter, at the other end of the top edge. It takes the
	   pointer so its words can be read, with the arrow because it is not a control. */
	.waits {
		opacity: 1;
		pointer-events: auto;
		cursor: default;
		inset-inline-start: auto;
		inset-inline-end: var(--space-3);
	}
</style>
