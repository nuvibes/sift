<script lang="ts">
	/*
	 * What the last key did, in the top-left of the picture. R, S and L move controls in the shut
	 * drawer, so a press would otherwise read as nothing; every other key's answer is the last
	 * badge. Keyed on the file, so a badge about one clip says nothing about the next.
	 */
	import { KeyEcho } from '$lib/components/common';
	import { ACTS } from '$lib/player/acts';
	import { loopModeIcon, loopModeLabel, loopRepeats, type LoopMode } from '$lib/player/loop-modes';
	import { run } from '$lib/player/run.svelte';
	import type { Echo } from '$lib/theater/echoes';

	interface Props {
		watching: string;
		loop: LoopMode;
		/** What the A-B control would do to this clip, and whether its loop is running. */
		abLabel: string;
		looping: boolean;
		repeatPresses: number;
		shufflePresses: number;
		loopPresses: number;
		/** What the last other key did, and how many presses ago. */
		keyed: Echo;
		keyedPresses: number;
	}

	let {
		watching,
		loop,
		abLabel,
		looping,
		repeatPresses,
		shufflePresses,
		loopPresses,
		keyed,
		keyedPresses
	}: Props = $props();
</script>

<div class="key-echoes">
	{#key watching}
		<KeyEcho
			icon={loopModeIcon(loop)}
			label={loopModeLabel(loop)}
			muted={!loopRepeats(loop)}
			press={repeatPresses}
		/>
		<!-- Unlit when off, as the drawer's button is. -->
		<KeyEcho
			icon="shuffle"
			label={run.shuffle ? ACTS.shuffle : 'In order'}
			muted={!run.shuffle}
			press={shufflePresses}
		/>
		<KeyEcho icon="all_inclusive" label={abLabel} muted={!looping} press={loopPresses} />
	{/key}
	<!-- Outside the key: a volume or a mute belongs to the player rather than to the file. -->
	<KeyEcho
		icon={keyed.icon}
		label={keyed.label}
		detail={keyed.detail}
		muted={keyed.muted}
		press={keyedPresses}
	/>
</div>

<style>
	/* A column, so two badges never overlap: the other top corner is the notice's, the bottom the
	   bar's. */
	.key-echoes {
		position: absolute;
		inset-block-start: var(--space-5);
		inset-inline-start: var(--space-5);
		z-index: 2;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		pointer-events: none;
	}
</style>
