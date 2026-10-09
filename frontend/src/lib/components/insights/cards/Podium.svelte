<script lang="ts">
	/*
	 * The top five as faces, never a bar chart: the first large with her name and her figure, the
	 * rest a row of small faces, each with its place. A face that has no picture draws its letter.
	 * Each name is a way to the person.
	 */
	import { Avatar, HistorySentence } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { saidOf } from '$lib/components/insights/figures';

	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		rows: readonly NamedRow[];
		/** Square faces for People; a Site's mark is drawn whole. */
		marks?: boolean;
		/** One line of faces, the first larger, for a holder with no height to stack them. */
		flat?: boolean;
	}

	let { rows, marks = false, flat = false }: Props = $props();

	const lead = $derived(rows[0]);
	const rest = $derived(rows.slice(1, 5));
</script>

{#if lead}
	<div class="podium" class:flat>
		<div class="lead">
			<span class="face lead-face">
				<Avatar src={lead.cover} name={lead.piece.text} mark={marks} decorative lazy />
			</span>
			<span class="place lead-place">1</span>
			<p class="who">
				<span class="name"><HistorySentence pieces={[lead.piece]} /></span>
				<span class="figure">{saidOf(lead.said, lead.value, lead.unit)}</span>
			</p>
		</div>
		{#if rest.length > 0}
			<ol class="rest" start="2">
				{#each rest as row, index (row.piece.id ?? index)}
					<li>
						<span class="face">
							<Avatar src={row.cover} name={row.piece.text} mark={marks} decorative lazy />
						</span>
						<span class="place">{index + 2}</span>
						<span class="name small"><HistorySentence pieces={[row.piece]} /></span>
					</li>
				{/each}
			</ol>
		{/if}
	</div>
{/if}

<style>
	/* Safe: in a holder too short for it the podium is cut at its foot, never at the first face. */
	.podium {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: safe center;
		gap: var(--podium-gap, var(--space-4));
		min-block-size: 0;
	}

	.lead {
		position: relative;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--podium-lead-gap, var(--space-2));
		text-align: center;
	}

	.face {
		display: block;
		overflow: hidden;
		inline-size: var(--card-face-small);
		aspect-ratio: 1;
		border-radius: 50%;
		box-shadow: 0 0 0 calc(var(--card-face-small) / 26) var(--sift-accent-tint-2);
	}

	.lead-face {
		inline-size: var(--card-face);
		box-shadow:
			0 0 0 calc(var(--card-face) / 40) var(--sift-accent-tint-1),
			var(--elev-3);
	}

	.face > :global(.avatar) {
		block-size: 100%;
	}

	/* The place, on the face's lower edge, in the family's ink. */
	/* Positioned, so it is painted over the face it overlaps. */
	.place {
		position: relative;
		display: grid;
		place-items: center;
		min-inline-size: calc(var(--card-face-small) * 0.42);
		margin-block-start: calc(var(--card-face-small) * -0.28);
		border-radius: var(--radius-sm);
		background-color: var(--sift-accent-tint-1);
		color: var(--sift-accent-shade-2);
		font: var(--text-micro);
		font-variant-numeric: tabular-nums;
	}

	.lead-place {
		min-inline-size: calc(var(--card-face) * 0.22);
		margin-block-start: calc(var(--card-face) * -0.16);
		font: var(--text-label);
		font-weight: 700;
	}

	.who {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		max-inline-size: 100%;
		margin: 0;
	}

	.name {
		max-inline-size: 100%;
		overflow: hidden;
		font: var(--text-h2);
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.name.small {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.figure {
		font: var(--text-label);
		color: var(--sift-accent-tint-1);
		font-variant-numeric: tabular-nums;
	}

	.rest {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* Flat: the first and the rest along one line, the pictures alone: a holder this short has no room for names. */
	.flat {
		flex-direction: row;
		align-items: center;
	}

	.flat .lead {
		flex: none;
	}

	.flat .rest {
		flex: 1;
	}

	.flat .who,
	.flat .name.small {
		display: none;
	}

	.rest li {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		min-inline-size: 0;
	}
</style>
