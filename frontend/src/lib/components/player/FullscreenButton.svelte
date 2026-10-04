<script lang="ts">
	/*
	 * The way in and out of fullscreen, wherever the controls for this asset are.
	 *
	 * Its own component because there are two bars (the player's, and a photograph's) and they must
	 * be the same button, agreeing on its label and on whether it is drawn while the pointer is
	 * idle. The stage is what knows, and it is asked here once.
	 *
	 * Draws nothing when there is no stage: the player also renders on a page of its own with no
	 * frame to take the screen. It matches the other controls on the bar by being one of them.
	 */
	import { Button, Tooltip } from '$lib/components/common';
	import { ACTS, keyOf, type Keyboard } from '$lib/player/acts';
	import { getStage } from './stage.svelte';

	/* Drawn wherever there is a stage, a picture's as much as a video's: where the browser lets
	   nothing fill the screen (a picture on an iPhone), the stage fills the window instead. */
	const frame = getStage();

	/* Whose keys answer F here: the Player's, or a picture's. The same key on both, read from the
	   act table rather than written beside the button. */
	let { keyboard }: { keyboard: Keyboard } = $props();
	const isFullscreen = $derived(frame?.isFullscreen ?? false);
</script>

{#if frame}
	<Tooltip
		label={isFullscreen ? ACTS.leaveFullScreen : ACTS.fullScreen}
		shortcut={keyOf(isFullscreen ? 'leaveFullScreen' : 'fullScreen', keyboard)}
	>
		<Button
			tone="ghost"
			icon={isFullscreen ? 'fullscreen_exit' : 'fullscreen'}
			aria-label={isFullscreen ? ACTS.leaveFullScreen : ACTS.fullScreen}
			onclick={() => frame.toggleFullscreen()}
		/>
	</Tooltip>
{/if}
