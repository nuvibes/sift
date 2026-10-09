<script lang="ts">
	/*
	 * Where a file's song name came from, under its Music field: a Site's page, AcoustID, or
	 * another file sharing the song (a link, or "another file" where this account may not see it).
	 * A typed name draws nothing. Only a shared name can be undone: it is Sift's guess, and the
	 * undo is the History line's own.
	 */
	import Button from '$lib/components/common/Button.svelte';
	import AssetLink from '$lib/components/common/AssetLink.svelte';
	import type { MusicFrom } from '$lib/player/music';

	interface Props {
		source: string | null;
		from?: MusicFrom | null;
		site?: string | null;
		onundo?: () => void;
		undoing?: boolean;
	}

	let { source, from = null, site = null, onundo, undoing = false }: Props = $props();

	const canUndo = $derived(source === 'shared' && onundo !== undefined);
</script>

{#if source === 'site' || source === 'acoustid' || source === 'shared'}
	<p class="source">
		<span class="said">
			{#if source === 'site'}
				Named from {site ? `${site}'s page` : 'its download page'}
			{:else if source === 'acoustid'}
				Named from AcoustID
			{:else if from}
				Shared with <AssetLink id={from.id}>{from.name}</AssetLink>
			{:else}
				Shared with another file
			{/if}
		</span>
		{#if canUndo}
			<Button icon="undo" tone="ghost" size="small" busy={undoing} onclick={() => onundo?.()}
				>Undo</Button
			>
		{/if}
	</p>
{/if}

<style>
	.source {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-1) 0 0;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.said {
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}
</style>
