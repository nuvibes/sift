<script lang="ts">
	/*
	 * Screenshot, in a player's drawer: a video's and a picture's, the same control in the same
	 * place.
	 *
	 * In a browser it is one press, the frame on screen. In the desktop app it opens a menu of three
	 * (this frame, the player as drawn, the whole window), because only the app can photograph the
	 * page around the picture. Where the picture goes is the person's setting, not this control's:
	 * see `takeShot`.
	 *
	 * One component for both drawers, so the video's and the picture's cannot come to say different
	 * words or offer different pictures.
	 */
	import {
		Button,
		ContextMenuItem,
		MenuButton,
		Tooltip,
		type ButtonSize
	} from '$lib/components/common';
	import { ACTS } from '$lib/player/acts';
	import { SHOT_LABELS, shotsOffered, takeShot, type Shot } from '$lib/player/snapshot';

	interface Props {
		/** The video on screen, or null on a picture. */
		video?: HTMLVideoElement | null;
		/** The picture on screen, or null on a video. */
		still?: HTMLImageElement | HTMLCanvasElement | null;
		/** The element the player is drawn in, for a picture of the player as drawn. */
		stage?: HTMLElement | null;
		/** Where the menu is painted while the player fills the screen. */
		portalTo?: HTMLElement | null;
		/** Whether the desktop's menu is open. Bindable, so the bar can shut it as it fades. */
		open?: boolean;
		/** Which way the tooltip and the menu open: up from a player's bar, down from the top bar. */
		side?: 'top' | 'bottom';
		size?: ButtonSize;
		/** What it is called where "Screenshot" alone would not say of what: a wall of cells. */
		label?: string;
		/** The id of the file on screen: a screenshot saved into the library is named after it. */
		of?: string | null;
	}

	let {
		video = null,
		still = null,
		stage = null,
		portalTo = null,
		open = $bindable(false),
		side = 'top',
		size = 'medium',
		label = ACTS.screenshot,
		of = null
	}: Props = $props();

	const shots = shotsOffered();

	function take(shot: Shot) {
		void takeShot(shot, { video, stage, still, of });
	}
</script>

{#if shots.length > 1}
	<MenuButton {label} {side} bind:open {portalTo}>
		{#snippet trigger({ props })}
			<Tooltip {label} placement={side}>
				<Button {...props} tone="ghost" {size} icon="screenshot_region" aria-label={label} />
			</Tooltip>
		{/snippet}
		{#each shots as shot (shot)}
			<ContextMenuItem label={SHOT_LABELS[shot]} onselect={() => take(shot)} />
		{/each}
	</MenuButton>
{:else}
	<Tooltip {label} placement={side}>
		<Button
			tone="ghost"
			{size}
			icon="screenshot_region"
			aria-label={label}
			onclick={() => take('frame')}
		/>
	</Tooltip>
{/if}
