<script lang="ts">
	/*
	 * Screenshot, in a video's or a picture's drawer: one press in a browser, a menu of three in
	 * the app (`takeShot`).
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
		video?: HTMLVideoElement | null;
		still?: HTMLImageElement | HTMLCanvasElement | null;
		stage?: HTMLElement | null;
		portalTo?: HTMLElement | null;
		open?: boolean;
		side?: 'top' | 'bottom';
		size?: ButtonSize;
		label?: string;
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
