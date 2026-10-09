<script lang="ts">
	/*
	 * Theater's own controls on the shared bar: a component, since a snippet would be styled where
	 * rendered.
	 */
	import { Button, type ButtonSize } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { showing } from '$lib/theater/wall.svelte';
	import { ACTS, keyOf } from '$lib/player/acts';

	interface Props {
		size?: ButtonSize;
		/**
		 * On the top bar it is the bar's own; beside the cell chip while filled, it comes from
		 * here.
		 */
		pauseToo?: boolean;
	}

	let { size = 'medium', pauseToo = false }: Props = $props();

	const wall = $derived(showing.wall);
</script>

<!-- No play control: the top bar draws one on every screen. -->
{#if wall}
	{#if pauseToo}
		<Tooltip
			label={wall.paused ? ACTS.playEverything : ACTS.pauseEverything}
			shortcut={keyOf(wall.paused ? 'playEverything' : 'pauseEverything', 'theater')}
		>
			<Button
				tone="ghost"
				{size}
				icon={wall.paused ? 'autoplay' : 'autostop'}
				aria-label={wall.paused ? ACTS.playEverything : ACTS.pauseEverything}
				pressed={!wall.paused}
				onclick={() => wall.togglePause()}
			/>
		</Tooltip>
	{/if}
	<Tooltip
		label={wall.masterMuted ? ACTS.unmuteEverything : ACTS.muteEverything}
		shortcut={keyOf(wall.masterMuted ? 'unmuteEverything' : 'muteEverything', 'theater')}
	>
		<Button
			tone="ghost"
			{size}
			icon={wall.masterMuted ? 'volume_off' : 'volume_up'}
			aria-label={wall.masterMuted ? ACTS.unmuteEverything : ACTS.muteEverything}
			pressed={wall.masterMuted}
			onclick={() => wall.toggleMaster()}
		/>
	</Tooltip>
	<!-- No Screenshot: it is one cell's, in its drawer. -->
{/if}

<!-- The ways out are on the wall's own scrub bar; this row is not drawn while the wall fills. -->
