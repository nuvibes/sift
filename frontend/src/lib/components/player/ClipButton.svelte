<script lang="ts">
	/*
	 * Clip: the last few seconds as a new file (`keepTheLast`), for the player's and a cell's
	 * drawer; dimmed, never removed.
	 */
	import { Button, ContextMenuItem, MenuButton } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { ACTS } from '$lib/player/acts';
	import { LAST_SECONDS, keepTheLast } from '$lib/player/snapshot';

	interface Props {
		of: string | null;
		playhead: () => number;
		unclippable?: string | null;
		open?: boolean;
		portalTo?: Element | null;
	}

	let {
		of,
		playhead,
		unclippable = null,
		open = $bindable(false),
		portalTo = null
	}: Props = $props();

	const label = $derived(of === null ? (unclippable ?? 'Nothing is playing to clip') : unclippable);
</script>

{#if of !== null && label === null}
	{@const file = of}
	<MenuButton label={ACTS.clip} side="top" bind:open {portalTo}>
		{#snippet trigger({ props })}
			<Tooltip label={ACTS.clip} placement="top">
				<Button {...props} tone="ghost" icon="content_cut" aria-label={ACTS.clip} />
			</Tooltip>
		{/snippet}
		{#each LAST_SECONDS as seconds (seconds)}
			<ContextMenuItem
				label={`Last ${seconds} seconds`}
				onselect={() => void keepTheLast(file, playhead(), seconds)}
			/>
		{/each}
	</MenuButton>
{:else}
	<Tooltip label={label ?? ACTS.clip} placement="top">
		<Button tone="ghost" icon="content_cut" aria-label={label ?? ACTS.clip} disabled />
	</Tooltip>
{/if}
