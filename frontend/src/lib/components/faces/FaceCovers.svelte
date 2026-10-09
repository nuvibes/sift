<script lang="ts">
	/*
	 * The face crops that stand for a card, and the link into what they stand for, for every face
	 * wall.
	 */
	import { TILE_ID } from '$lib/components/common';
	import { cropUrl, type Sighting } from '$lib/people/faces.svelte';

	interface Props {
		faces: Sighting[];
		/** Omitted, it is not a link: a person this account may not be told about has no page. */
		href?: string;
		label?: string;
		picked?: boolean;
		onpointerdown?: (event: PointerEvent) => void;
		onpointerup?: () => void;
		onclickcapture?: (event: MouseEvent) => void;
		/** Forwarded to the wall, or a bare `<img>` would answer with the browser's menu. */
		oncontextmenu?: (event: MouseEvent) => void;
		/** For the press-and-drag sweep, which reads the id off the DOM; only on the link form. */
		sweepId?: string;
		/**
		 * How many cells, so every card on a wall is one size: six a row, the last saying how many
		 * more there are; short rows hold their room. Absent, exactly what was given.
		 */
		most?: number;
		/**
		 * Two or three where a card's six columns are shared, so every crop on a wall is one size.
		 */
		across?: 2 | 3 | 6;
		/** How many the row stands for, when handed only some ("+217" of 229). */
		total?: number;
		/** Off, `most` is only the cap and its counter (`DecisionCard`). */
		hold?: boolean;
	}

	let {
		faces,
		href,
		label,
		picked = false,
		onpointerdown,
		onpointerup,
		onclickcapture,
		oncontextmenu,
		sweepId,
		most,
		total,
		across = 6,
		hold = true
	}: Props = $props();

	const sweepable = $derived(sweepId ? { [TILE_ID]: sweepId } : {});

	/* The counter takes a cell, so it counts the cap LESS ONE. */
	const standing = $derived(Math.max(faces.length, total ?? 0));
	const hidden = $derived(most !== undefined && standing > most ? standing - (most - 1) : 0);
	const shown = $derived(most === undefined ? faces : faces.slice(0, hidden > 0 ? most - 1 : most));
	const holes = $derived(
		most === undefined || !hold
			? []
			: Array.from({ length: most - shown.length - (hidden > 0 ? 1 : 0) })
	);
</script>

{#if href}
	<!-- The hook spread FIRST, before the class (`caller-class.test.ts`). -->
	<a
		{...sweepable}
		class="faces"
		class:rows={most !== undefined}
		class:across-2={across === 2}
		class:across-3={across === 3}
		class:picked
		{href}
		aria-label={label}
		{onpointerdown}
		{onpointerup}
		onpointercancel={onpointerup}
		{onclickcapture}
		{oncontextmenu}
	>
		{#each shown as face (face.track_id)}
			<img src={cropUrl(face)} alt="" loading="lazy" />
		{/each}
		{#if hidden > 0}
			<span class="more">+{hidden.toLocaleString()}</span>
		{/if}
		{#each holes as _hole, at (at)}
			<!-- Room held, nothing to announce. -->
			<span class="hole" aria-hidden="true"></span>
		{/each}
	</a>
{:else}
	<div
		class="faces"
		class:rows={most !== undefined}
		class:across-2={across === 2}
		class:across-3={across === 3}
	>
		{#each shown as face (face.track_id)}
			<img src={cropUrl(face)} alt="" loading="lazy" />
		{/each}
		{#if hidden > 0}
			<span class="more">+{hidden.toLocaleString()}</span>
		{/if}
		{#each holes as _hole, at (at)}
			<!-- Room held, nothing to announce. -->
			<span class="hole" aria-hidden="true"></span>
		{/each}
	</div>
{/if}

<style>
	/* Crops grow to fill their share, so no strip of ground is left down the card's edge. */
	.faces {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(3.25rem, 1fr));
		gap: var(--space-1);
	}

	.faces.rows {
		grid-template-columns: repeat(6, minmax(0, 1fr));
	}

	.faces.rows.across-3 {
		grid-template-columns: repeat(3, minmax(0, 1fr));
	}

	.faces.rows.across-2 {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	a.faces {
		border-radius: var(--radius-sm);
		text-decoration: none;
	}

	a.faces:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	a.faces.picked {
		outline: 2px solid var(--sift-accent);
		outline-offset: 2px;
	}

	.faces img,
	.faces .more,
	.faces .hole {
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		object-fit: cover;
		background: var(--sift-surface-3);
	}

	.faces .more {
		display: grid;
		place-items: center;
		color: var(--sift-ink-2);
		font: var(--text-label);
	}

	/* No ground: the height is the point. */
	.faces .hole {
		background: none;
	}
</style>
