<script lang="ts">
	/*
	 * The mark a Site wears when other Sites are part of it: the network glyph and "3 Sites within".
	 *
	 * One drawing for the card and the page's header, so the two say it the same way. It takes the
	 * ink and the type of the line it sits in, like every other mark beside a name: it is a fact
	 * about the Site rather than a control, and the page's Sites tab is where the Sites are listed.
	 * Draws nothing for nought: a Site nothing is part of is not a network.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { counted } from '$lib/entity/entity-counts';

	interface Props {
		/** How many Sites are directly part of this one, as its Sites tab counts them. */
		count: number;
	}

	let { count }: Props = $props();

	const said = $derived(`${counted(count)} ${count === 1 ? 'Site' : 'Sites'} within`);
</script>

{#if count > 0}
	<span class="network">
		<span class="glyph"><Icon name="hub" size={14} /></span>
		<span class="words">{said}</span>
	</span>
{/if}

<style>
	.network {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	/* The words give way before the glyph on a narrow card, and the glyph never shrinks. */
	.words {
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.glyph {
		display: inline-flex;
		flex: none;
	}
</style>
