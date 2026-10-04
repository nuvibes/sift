<script lang="ts">
	/* A still on a real stage. The shape the application uses for a photograph or a GIF.
	 *
	 * A component rather than a snippet written in the test, for the reason `StageProbe` spells out:
	 * context follows the render tree, and a snippet is compiled in the scope of the file that wrote
	 * it, so one written outside the stage reads no context however deep inside it is drawn.
	 */
	import MediaStage from './MediaStage.svelte';
	import StillView from './StillView.svelte';

	let {
		id = 'asset-1',
		mediaType = 'image',
		compact = false,
		onprevious,
		onnext,
		onplayedthrough,
		mayNotDraw = false
	}: {
		id?: string;
		mediaType?: string;
		compact?: boolean;
		/* The list this picture was opened from, when there is one. A picture steps through a list
		   exactly as a clip does, so the pair on the bar has to be reachable from here to be tested
		   at all. */
		onprevious?: () => void;
		onnext?: () => void;
		/* Where a run goes once this picture has rested; absent, it is a picture outside a run. */
		onplayedthrough?: () => void;
		/* A picture only some browsers draw, as the file's detail says. */
		mayNotDraw?: boolean;
	} = $props();
</script>

<MediaStage>
	{#snippet media()}
		<StillView {id} {mediaType} {compact} {onprevious} {onnext} {onplayedthrough} {mayNotDraw} />
	{/snippet}
</MediaStage>
