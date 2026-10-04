<script lang="ts">
	/*
	 * Theater's own controls on the shared bar: the ones that act rather than open something.
	 *
	 * A component and not a snippet, which the bar's own comment explains: a snippet is compiled
	 * where it is written and STYLED where it is rendered, so these buttons handed up as markup would
	 * arrive on the most visible row in the app wearing the browser's own chrome.
	 *
	 * Play all is drawn with the same glyph the grid's own playback toggle uses, because it is the
	 * same question asked of a different kind of screen: is everything moving or not.
	 */
	import { Button, type ButtonSize } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { showing } from '$lib/theater/wall.svelte';
	import { ACTS, keyOf } from '$lib/player/acts';

	interface Props {
		/** Small on the wall's own bar, where every control is; medium on the top bar. */
		size?: ButtonSize;
		/**
		 * Draw the play-everything control too.
		 *
		 * On the top bar it is not drawn here: the bar draws one on every screen and the wall
		 * answers it. Beside the cell chip while the screen is filled the top bar is gone, so the
		 * verb has to come from here. One component, two doors.
		 */
		pauseToo?: boolean;
	}

	let { size = 'medium', pauseToo = false }: Props = $props();

	const wall = $derived(showing.wall);
</script>

<!-- No play control here. It is the top bar's, which draws one on every screen and the wall
     answers; a second here would be a copy of it directly below the live one. See the screen's own
     publish. -->
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
	<!--
		No Screenshot here. A cell's screenshot is the first control in that cell's drawer, where the
		popout player's drawer keeps it (`CellControls`). This bar holds what acts on the whole wall,
		and a verb about one cell has one door, in the cell's own controls.
	-->
{/if}

<!--
	The two ways out (the corner panel and filling the screen) are not here. They are on the wall's
	own scrub bar, where the ordinary player keeps them and where a hand already is. This row is at
	the top of the window and is not drawn at all while the wall fills it, so controls for leaving a
	filled wall cannot live on it.
-->
