<script lang="ts">
	/*
	 * A photograph, and the picture itself as the control.
	 *
	 * Cropping is a rectangle dragged directly on the picture, with everything outside it dimmed.
	 * Rotate and mirror are four buttons that take effect on what is drawn at once. There is no
	 * separate resize: a rectangle drawn freely is the same instruction with fewer words.
	 * There are no number fields: the numbers are shown, small, because somebody who wants to know
	 * what they have selected should be able to find out, and they are not how it is set.
	 *
	 * **The rectangle itself is `CropStage`**, the one crop control in the app: a cover's framing
	 * step draws the same one, locked to the cover's shape. What is this file's own is what goes
	 * around it: the shapes the rectangle can be held to, and the four turns.
	 *
	 * **Nothing here decides anything.** Whether the rectangle fits, whether that width is bigger
	 * than the picture, what the copy will be called: all of it is the server's, asked again every
	 * time a number changes. What this owns is the gesture.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import CropStage from '$lib/components/edit/CropStage.svelte';
	import { SHAPES, fitted, type Box, type Facing, type Frame } from '$lib/edit/geometry';

	interface Props {
		/** Where the picture is. */
		src: string;
		/** The picture as it is seen, once it is this way round. */
		frame: Frame;
		facing: Facing;
		/** The rectangle to keep, in this frame's own pixels. */
		box: Box;
		/** Rendered between the picture and the buttons: the name the copy will have. */
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

	/** Which shape the rectangle is held to, or none. */
	let shape = $state<(typeof SHAPES)[number]['id']>('free');
	const ratio = $derived(SHAPES.find((one) => one.id === shape)?.ratio ?? null);

	function chooseShape(id: (typeof SHAPES)[number]['id']): void {
		shape = id;
		const chosen = SHAPES.find((one) => one.id === id)?.ratio ?? null;
		box = chosen ? fitted(frame, chosen) : box;
	}

	/*
	 * How much wider than tall the picture is BEFORE it is turned, as a fraction of the frame.
	 *
	 * The element holding the picture has to be the frame's other way round whenever the picture is
	 * on its side, or a quarter turn leaves it sticking out of the stage at the top and bottom and
	 * short at the sides.
	 */
	const sideways = $derived(facing.quarters % 2 === 1 && frame.width > 0 && frame.height > 0);
	const holderRatio = $derived(sideways ? frame.height / frame.width : 1);
</script>

<CropStage {frame} bind:box {ratio}>
	{#snippet picture()}
		<!-- Turned by the browser rather than by asking the server for another picture. The holder is
		     given the size it needs to fill the frame ONCE turned, which is the frame the other way
		     round whenever the picture is on its side. -->
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
	<!-- The shapes, where a phone's editor puts them: a row of names rather than numbers, and each
	     one snaps the rectangle to the largest of that shape the picture can hold. -->
	<div class="modes" role="group" aria-label="What shape">
		{#each SHAPES as one (one.id)}
			<Button size="small" pressed={shape === one.id} onclick={() => chooseShape(one.id)}>
				{one.label}
			</Button>
		{/each}
	</div>
	<!-- Four presses rather than a mode of their own. Each one takes effect on the picture at once,
	     and a rectangle already drawn is turned with it rather than left where its numbers were. -->
	<div class="modes" role="group" aria-label="Which way round">
		{#each TURNS as turn (turn.id)}
			<Tooltip label={turn.label}>
				<!-- One glyph for both mirrors: the font has a mirror across a vertical line and none
				     across a horizontal one, so the second is the same picture turned a quarter,
				     which is what mirroring down IS. The quarter turn is a class on the button, and
				     the rule below reaches the glyph inside it. -->
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

	/* The same shape and weight as Cancel, because these are the same kind of thing: a secondary
	   control inside a sheet. The one that is chosen is filled in rather than outlined, so which it
	   is survives a screenshot and does not depend on colour alone. */
	/* The mirror-down button, which is the mirror-across glyph turned a quarter. The rule reaches
	   the GLYPH rather than the button, so the focus ring and the ground stay square while only the
	   picture inside turns. `:global` because the class is handed to a component. */
	.modes :global(.quartered .icon) {
		rotate: 90deg;
	}

	/* Centred and then turned, so the middle of the picture stays in the middle of the frame
	   whichever way round it is. */
	.turner {
		position: absolute;
		inset-block-start: 50%;
		inset-inline-start: 50%;
	}

	/* Filling its holder rather than fitting inside it. The holder already carries the picture's own
	   shape, so there is nothing to letterbox and nothing to distort, and the stage's edges are the
	   picture's edges, which is what the drag arithmetic assumes. */
	.turner img {
		display: block;
		inline-size: 100%;
		block-size: 100%;
		object-fit: fill;
		pointer-events: none;
	}
</style>
