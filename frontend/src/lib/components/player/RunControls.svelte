<script lang="ts">
	/*
	 * What happens at the end of a file and the order the run walks: the two controls about what
	 * comes next, drawn in a drawer beside Randomize, the third. One pair for the clip's drawer and
	 * the picture's, so stepping between them moves nothing under the hand.
	 */
	import { Button } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { ACTS, type Keyboard, keyOf } from '$lib/player/acts';
	import { loopModeIcon, loopModeLabel, loopRepeats } from '$lib/player/loop-modes';
	import type { RepeatControl, ShuffleControl } from './Transport.svelte';

	interface Props {
		repeat: RepeatControl;
		shuffle: ShuffleControl;
		/** Whose keys these answer, so Shuffle's tooltip shows its key. */
		keyboard: Keyboard;
	}

	let { repeat, shuffle, keyboard }: Props = $props();

	const repeatWords = $derived(repeat.why ?? loopModeLabel(repeat.mode));
	const shuffleWords = $derived(shuffle.why ?? ACTS.shuffle);
</script>

<!-- No key on the tooltip: R answers it, and the act table names no key for it. -->
<Tooltip label={repeatWords} placement="top">
	<Button
		tone="ghost"
		icon={loopModeIcon(repeat.mode)}
		aria-label={repeatWords}
		pressed={loopRepeats(repeat.mode)}
		disabled={repeat.why !== undefined}
		onclick={repeat.onpress}
	/>
</Tooltip>
<Tooltip
	label={shuffleWords}
	placement="top"
	shortcut={shuffle.why ? undefined : keyOf('shuffle', keyboard)}
>
	<Button
		tone="ghost"
		icon="shuffle"
		aria-label={shuffleWords}
		pressed={shuffle.on}
		disabled={shuffle.why !== undefined}
		onclick={shuffle.onpress}
	/>
</Tooltip>
