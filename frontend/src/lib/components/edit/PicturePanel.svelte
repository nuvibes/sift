<script lang="ts">
	/*
	 * A photograph, the picture itself as the control: a rectangle dragged on it (`CropStage`),
	 * held to a shape or not, and four turns. Every number is the server's, asked on every change.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import CropStage from '$lib/components/edit/CropStage.svelte';
	import { SHAPES, fitted, type Box, type Facing, type Frame } from '$lib/edit/geometry';

	interface Props {
		src: string;
		frame: Frame;
		facing: Facing;
		box: Box;
		under?: Snippet;
		onturn: (which: 'left' | 'right' | 'across' | 'down') => void;
	}

	let { src, frame, facing, box = $bindable(), under, onturn }: Props = $props();

	const TURNS: { id: 'left' | 'right' | 'across' | 'down'; label: string }[] = [
		{ id: 'left', label: 'Turn left' },
		{ id: 'right', label: 'Turn right' },
		{ id: 'across', label: 'Mirror across' },
		{ id: 'down', label: 'Mirror down' }
	];

	let shape = $state<(typeof SHAPES)[number]['id']>('free');
	const ratio = $derived(SHAPES.find((one) => one.id === shape)?.ratio ?? null);

	function chooseShape(id: (typeof SHAPES)[number]['id']): void {
		shape = id;
		const chosen = SHAPES.find((one) => one.id === id)?.ratio ?? null;
		box = chosen ? fitted(frame, chosen) : box;
	}

	/* On its side, the holder is the frame the other way round. */
	const sideways = $derived(facing.quarters % 2 === 1 && frame.width > 0 && frame.height > 0);
	const holderRatio = $derived(sideways ? frame.height / frame.width : 1);
</script>

<CropStage {frame} bind:box {ratio}>
	{#snippet picture()}
		<!-- Turned by the browser; the holder sized for the turned frame. -->
		<div
			class="turner"
			style:inline-size={`${100 * holderRatio}%`}
			style:block-size={`${100 / holderRatio}%`}
			style:translate="-50% -50%"
			style:rotate={`${facing.quarters * 90}deg`}
			style:scale={facing.mirrored ? '-1 1' : '1 1'}
		>
			<img {src} alt="" draggable="false" />
		</div>
	{/snippet}
</CropStage>

{@render under?.()}

<div class="tools">
	<div class="modes" role="group" aria-label="What shape">
		{#each SHAPES as one (one.id)}
			<Button size="small" pressed={shape === one.id} onclick={() => chooseShape(one.id)}>
				{one.label}
			</Button>
		{/each}
	</div>
	<!-- Four presses; a drawn rectangle turns with the picture. -->
	<div class="modes" role="group" aria-label="Which way round">
		{#each TURNS as turn (turn.id)}
			<Tooltip label={turn.label}>
				<!-- Mirroring down is the mirror glyph turned a quarter. -->
				<Button
					size="small"
					class={turn.id === 'down' ? 'quartered' : ''}
					icon={turn.id === 'left' ? 'rotate_left' : turn.id === 'right' ? 'rotate_right' : 'flip'}
					aria-label={turn.label}
					onclick={() => onturn(turn.id)}
				/>
			</Tooltip>
		{/each}
	</div>
</div>

<style>
	.tools {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		gap: var(--space-3);
	}

	.modes {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	/* The glyph turns, not the button, so the ring stays square. */
	.modes :global(.quartered .icon) {
		rotate: 90deg;
	}

	.turner {
		position: absolute;
		inset-block-start: 50%;
		inset-inline-start: 50%;
	}

	/* Filling its holder: the stage's edges are the picture's, as the drag arithmetic assumes. */
	.turner img {
		display: block;
		inline-size: 100%;
		block-size: 100%;
		object-fit: fill;
		pointer-events: none;
	}
</style>
