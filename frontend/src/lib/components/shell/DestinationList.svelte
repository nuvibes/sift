<script lang="ts" module>
	import type { IconName } from '$lib/design/icons';

	/** One place a row of the list goes to: a real address, with the glyph and the name it has everywhere. */
	export interface Destination {
		id: string;
		href: string;
		label: string;
		icon: IconName;
		/**
		 * Takes a plain press instead of following the link, for a place that opens over the screen
		 * (a Settings section). The row stays a real link either way.
		 */
		open?: (event: MouseEvent) => void;
	}
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: it is the list the phone's Library and More screens are made of, and both are
	   screens of their own at a real address; the rows it is built from (`DataRows`, `DataRow`) are
	   drawn on the gallery in every state they have. */

	/*
	 * A list of places to go, one row each: the glyph, the name, and the chevron that says the row
	 * leads somewhere.
	 *
	 * The rows are the shared list's rows, so they have its height, its hairline between two rows and
	 * its hover ground. What this adds is that the WHOLE row is the link: the link's own box is the
	 * words, and a press on the row's inset beside them would otherwise do nothing, which on a phone
	 * is a press that misses. A link rather than a pressable row because each row is an address: it
	 * can be opened in a new tab, copied and read as a link.
	 */
	import DataRow from '$lib/components/common/DataRow.svelte';
	import DataRows from '$lib/components/common/DataRows.svelte';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		items: readonly Destination[];
		/** Names the list for a screen reader. */
		label: string;
	}

	let { items, label }: Props = $props();
</script>

<div class="places">
	<DataRows {items} key={(item) => item.id} {label}>
		{#snippet row(item)}
			<DataRow>
				<a class="go" href={item.href} onclick={item.open}>
					<Icon name={item.icon} size={20} />
					<span class="name">{item.label}</span>
					<span class="onward"><Icon name="chevron_right" size={20} /></span>
				</a>
			</DataRow>
		{/snippet}
	</DataRows>
</div>

<style>
	/* The row is what the link covers, so the row is what it is positioned against. Bounded by this
	   file's own wrapper: no other list's rows are reached. */
	.places :global(.line) {
		position: relative;
	}

	.go {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		min-inline-size: 0;
		color: var(--sift-ink);
		text-decoration: none;
		font: var(--text-body);
	}

	/* The whole row, the inset included, is the press. */
	.go::after {
		content: '';
		position: absolute;
		inset: 0;
		border-radius: var(--radius-sm);
	}

	.go:focus-visible {
		outline: none;
	}

	.go:focus-visible::after {
		box-shadow: var(--focus-ring);
	}

	.name {
		flex: 1;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	/* Quieter than the name: it says where the row goes, and the name is what the row is. */
	.onward {
		display: inline-flex;
		color: var(--sift-ink-3);
	}
</style>
