<script module lang="ts">
	/* The least room the bar is worth drawing in, as the sum of its parts (two-column picker). */

	const LEAST_FOR_THE_CONTROLS = 400;
	/** Measured. */
	const LEADING_EDGE = 112;
	const THE_BAR_ITSELF = 24 + 12 + 2;
	/** `--space-8`, doubled. */
	const CLEAR_OF_THE_EDGES = 32;

	export const LEAST_FOR_THE_BAR =
		LEAST_FOR_THE_CONTROLS + LEADING_EDGE + THE_BAR_ITSELF + CLEAR_OF_THE_EDGES;
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: this reads Theater's wall and drives it. A second live copy would be a
	   second bar acting on the same cells, which is the app drawn twice. Everything it is MADE of
	   (the bar, the buttons, the player bar) is on the gallery. */

	/*
	 * One bar for the whole wall while the window is filled; the cells draw no chrome then. A
	 * picker of numbers marks its cell with the accent wash, fixed squares so the bar never resizes
	 * under the hand.
	 */
	import { Button } from '$lib/components/common';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { stage } from '$lib/components/shell/stage.svelte';
	import { ACTS, keyOf } from '$lib/player/acts';
	import StageBar from './StageBar.svelte';
	import CellControls from './CellControls.svelte';
	import { aims } from '$lib/theater/aim';
	import Icon from '$lib/components/Icon.svelte';
	import { controlledWords, screenOffer } from '$lib/remote/offer.svelte';
	import type { Wall } from '$lib/theater/wall.svelte';

	interface Props {
		wall: Wall;
		up: boolean;
		/** Handed in: only the thing drawing a wall knows which clock applies (`TheaterWall`). */
		quiet?: boolean;
		tall?: number;
	}

	let { wall, up, quiet = false, tall = $bindable(0) }: Props = $props();

	/* The cell the keyboard is on: one answer, not a second selection. */
	const chosen = $derived(Math.min(wall.focused, wall.cells.length - 1));
	const cell = $derived(wall.cells[chosen]);

	/* No wash survives the bar going, faded included. */
	$effect(() => {
		if (!up || quiet) wall.aiming = null;
	});

	const controlled = $derived(
		screenOffer.wallControlled ? controlledWords(screenOffer.controlledBy) : ''
	);

	/* Folded, they stay laid out out of sight, so their width is still known. */
	let room = $state(0);
	let numbersWide = $state(0);
	let foldWide = $state(0);
	const folds = (beside: number) =>
		room > 0 && numbersWide + beside > room && (foldWide === 0 || foldWide < numbersWide);
	const onCell = $derived(wall.everyCell ? 'All' : `${chosen + 1} of ${wall.cells.length}`);
	const foldNames = $derived(wall.everyCell ? 'all cells' : `cell ${onCell}`);
</script>

<!-- Quiet on the one clock a filled screen runs (`stage.barHidden`). -->
<StageBar open={up && cell !== undefined} {quiet} bind:tall bind:room label="The wall's controls">
	{#if cell}
		<CellControls
			{wall}
			{cell}
			index={chosen}
			position={cell.position}
			duration={cell.duration}
			onseek={(seconds) => cell.seek?.(seconds)}
			filled
			{picker}
		/>
	{/if}
</StageBar>

<!-- A phone is driving this wall: a mark, not a button. -->
{#snippet controlledMark()}
	<span class="controlled">
		<Tooltip label={controlled} placement="top">
			<Icon name="settings_remote" size={18} label={controlled} />
		</Tooltip>
	</span>
{/snippet}

<!-- Under the timeline, which is reached for constantly. -->
{#snippet picker(beside: number)}
	{@const folded = folds(beside)}
	<div class="picker">
		<!--
		Every cell as one row of numbers, not in the wall's shape, which would make the bar three
		rows tall.
		-->
		<div
			class="numbers"
			class:folded
			role="group"
			aria-label="Which cell to control"
			aria-hidden={folded || undefined}
			inert={folded || undefined}
			bind:offsetWidth={numbersWide}
		>
			{#if controlled}{@render controlledMark()}{/if}
			<!--
			All, a SELECTION: every verb on the bar then lands on the whole wall. A fixed word.
			-->
			<Tooltip label={ACTS.everyCell} placement="top" shortcut={keyOf('everyCell', 'theater')}>
				<Button
					size="small"
					tone="ghost"
					class="pick every {wall.everyCell ? 'on' : ''}"
					pressed={wall.everyCell}
					aria-label="Controls for all cells"
					{...aims(wall, 'every')}
					onclick={() => wall.focusEvery()}
				>
					All
				</Button>
			</Tooltip>
			{#each wall.cells as one, at (one.key)}
				<Button
					size="small"
					tone="ghost"
					class="pick {at === chosen && !wall.everyCell ? 'on' : ''}"
					pressed={at === chosen && !wall.everyCell}
					aria-label="Controls for cell {at + 1}"
					{...aims(wall, at)}
					onclick={() => wall.chooseByNumber(at)}
				>
					{at + 1}
				</Button>
			{/each}
		</div>
		{#if folded}
			<div class="fold" bind:offsetWidth={foldWide}>
				{#if controlled}{@render controlledMark()}{/if}
				<MenuButton label="Which cell to control" side="top" portalTo={stage.whatFillsTheWindow}>
					{#snippet trigger({ props })}
						<Tooltip label="Which cell to control" placement="top">
							<Button
								{...props}
								size="small"
								tone="ghost"
								class="pick on"
								trailing="expand_more"
								aria-label="Choose which cell to control, now {foldNames}"
							>
								{onCell}
							</Button>
						</Tooltip>
					{/snippet}
					<ContextMenuItem
						label="All"
						checked={wall.everyCell}
						oneOf
						onselect={() => wall.focusEvery()}
					/>
					{#each wall.cells as one, at (one.key)}
						<ContextMenuItem
							label={`${at + 1}`}
							checked={at === chosen && !wall.everyCell}
							oneOf
							onselect={() => wall.chooseByNumber(at)}
						/>
					{/each}
				</MenuButton>
			</div>
		{/if}
	</div>
{/snippet}

<style>
	/* Square whatever the digit; `:global` for the shared button. */
	.picker :global(.pick) {
		min-inline-size: 28px;
		inline-size: 28px;
		block-size: 28px;
		padding-inline: 0;
		font-variant-numeric: tabular-nums;
	}

	.numbers,
	.fold {
		display: flex;
		flex: none;
		align-items: center;
		gap: var(--space-1);
	}

	.picker {
		position: relative;
		display: flex;
	}

	.numbers.folded {
		position: absolute;
		inset-inline-end: 0;
		inline-size: max-content;
		visibility: hidden;
	}

	.fold :global(.pick) {
		min-inline-size: auto;
		inline-size: auto;
		padding-inline: var(--space-2);
	}

	.controlled {
		display: inline-flex;
		color: var(--sift-accent-text);
	}

	.numbers :global(.pick.every) {
		min-inline-size: 44px;
		inline-size: 44px;
	}

	/*
	 * The selected ground (`--sift-accent-bg`), opaque, since a wash takes the colour of the frame
	 * behind.
	 */
	.picker :global(.pick.on) {
		border: 1px solid var(--sift-accent);
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}
</style>
