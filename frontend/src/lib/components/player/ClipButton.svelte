<script lang="ts">
	/*
	 * Clip: keep what just happened, the last few seconds as a new file cut beside this one.
	 *
	 * One control for the player's drawer and a Theater cell's, as `ScreenshotButton` is: the same
	 * glyph, the same word, the same menu of lengths and the same cut (`keepTheLast`), so the two
	 * drawers cannot come to offer the question two ways.
	 *
	 * Always drawn. Where nothing can be clipped (a picture, a GIF, a cell with nothing in it) it is
	 * dimmed with the reason as its label, because a drawer that loses a control between one file
	 * and the next moves every control after it.
	 */
	import { Button, ContextMenuItem, MenuButton } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { ACTS } from '$lib/player/acts';
	import { LAST_SECONDS, keepTheLast } from '$lib/player/snapshot';

	interface Props {
		/** The file to clip, or null where nothing is playing. */
		of: string | null;
		/** Where the playhead is, in seconds, read at the moment a length is chosen. */
		playhead: () => number;
		/** Why nothing can be clipped here, said on the dimmed control. Null where it can be. */
		unclippable?: string | null;
		/** Whether the menu is open. Bound, so a bar that goes can shut it with itself. */
		open?: boolean;
		/** Where the open menu is drawn: the filled screen while one is filled. See `MenuButton`. */
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
