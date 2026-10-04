<script lang="ts">
	/*
	 * A stage with the harness above inside it: the shape the application uses, minus the player.
	 */
	import MediaStage from './MediaStage.svelte';
	import StageProbe from './StageProbe.svelte';
</script>

<MediaStage>
	{#snippet media()}
		<StageProbe />
	{/snippet}
</MediaStage>
