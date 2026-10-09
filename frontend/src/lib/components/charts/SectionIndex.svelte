<script lang="ts">
	/*
	 * AN INDEX OF A PAGE'S SECTIONS, down its side: each a link to its section, its colour, and a
	 * note of what is in it (how many tables). It stays in view while the page scrolls past it, and
	 * on a phone it is a row of links over the page instead.
	 */
	interface Entry {
		/** The section's id on the page, which the link goes to. */
		id: string;
		label: string;
		/** What the section holds, in words: "3 tables". */
		note?: string;
		/** The section's colour, a token. */
		paint: string;
	}

	interface Props {
		/** What the index is, to assistive technology. */
		label: string;
		entries: readonly Entry[];
		/** The section somebody was sent to, marked as where they are. */
		current?: string | null;
	}

	let { label, entries, current = null }: Props = $props();
</script>

<nav class="section-index" aria-label={label}>
	<ol>
		{#each entries as entry (entry.id)}
			<li>
				<a href={`#${entry.id}`} aria-current={entry.id === current ? 'location' : undefined}>
					<span class="mark" aria-hidden="true" style:background-color={entry.paint}></span>
					<span class="label">{entry.label}</span>
					{#if entry.note}<span class="note">{entry.note}</span>{/if}
				</a>
			</li>
		{/each}
	</ol>
</nav>

<style>
	.section-index {
		position: sticky;
		inset-block-start: 0;
		align-self: start;
	}

	ol {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	a {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		column-gap: var(--space-3);
		align-items: center;
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		color: var(--sift-ink-2);
		font: var(--text-body);
		text-decoration: none;
		transition:
			color var(--dur-instant) var(--ease),
			background var(--dur-instant) var(--ease);
	}

	a:hover,
	a[aria-current] {
		background: var(--sift-surface-3);
		color: var(--sift-ink);
	}

	a:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	:global(:root[data-motion='reduce']) a {
		transition: none;
	}

	.mark {
		grid-row: span 2;
		inline-size: var(--chart-key-mark);
		block-size: var(--chart-key-mark);
		border-radius: var(--radius-full);
	}

	.label {
		white-space: nowrap;
	}

	.note {
		grid-column: 2;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	/* A phone has no side to spare: the index is a row of links over the page. */
	@media (max-width: 767px) {
		.section-index {
			position: static;
		}

		ol {
			flex-direction: row;
			flex-wrap: wrap;
		}

		.note {
			display: none;
		}
	}
</style>
