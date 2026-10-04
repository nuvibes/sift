<script lang="ts">
	/* A player on a real stage: the shape the application draws for a video.
	 *
	 * A component rather than a snippet written in the test, for the reason `StageProbe` spells out:
	 * context follows the render tree, and a snippet is compiled in the scope of the file that wrote
	 * it, so one written outside the stage reads no context however deep inside it is drawn.
	 */
	import MediaStage from './MediaStage.svelte';
	import Player from './Player.svelte';
	import type { SpriteSheet } from '$lib/player/trickplay';
	import type { FileFacts } from '$lib/player/facts';
	import type { SittingPlace } from '$lib/player/sitting.svelte';

	let {
		id = 'asset-1',
		sprite = null,
		art = null,
		file = null,
		compact = false,
		draggable = false,
		onprevious,
		onnext,
		onplayedthrough,
		place,
		held
	}: {
		id?: string;
		sprite?: SpriteSheet | null;
		art?: string | null;
		file?: FileFacts | null;
		/** The small panel's player. The bar is not drawn at all. See the component. */
		compact?: boolean;
		/** Whether the picture can be dragged out of the window into another application. */
		draggable?: boolean;
		/* The list this file was opened from, when there is one. Handed through so the pair on the
		   bar and the two keys that step it have somewhere to go: without them the player is a
		   file opened on its own, which is a different thing to test. */
		onprevious?: () => void;
		onnext?: () => void;
		/* Where a run goes when the file ends by itself, for a test about the end of a file. */
		onplayedthrough?: () => void;
		/* Where the sittings happen, for a test about that and nothing else. */
		place?: SittingPlace;
		/* The player itself once it is drawn, for a test that asks what it hands the phone. */
		held?: (player: ReturnType<typeof Player>) => void;
	} = $props();

	let player = $state<ReturnType<typeof Player> | null>(null);
	$effect(() => {
		if (player) held?.(player);
	});
</script>

<MediaStage>
	{#snippet media()}
		<Player
			bind:this={player}
			{id}
			{sprite}
			{art}
			{file}
			{compact}
			{draggable}
			{onprevious}
			{onnext}
			{onplayedthrough}
			{place}
		/>
	{/snippet}
</MediaStage>
