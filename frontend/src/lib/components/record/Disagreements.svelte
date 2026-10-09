<script lang="ts">
	/*
	 * Where a stash-box disagrees with this record, on the record itself, where somebody can answer
	 * it. Nothing at all when there is nothing. The panel is the Stash-boxes pane's own.
	 */
	import ReconcilePanel from '$lib/components/organize/ReconcilePanel.svelte';
	import { waitingText } from '$lib/entity/reconcile.svelte';

	interface Props {
		subject: string;
		localId: string;
		/** How many are waiting; it overwrites the strip's count, being the fresher of the two. */
		onchange?: (waiting: number, boxes: string[]) => void;
		/** The page holds its own copy of the record, so it reads it again. */
		onwritten?: () => void;
	}

	let { subject, localId, onchange, onwritten }: Props = $props();

	let waiting = $state(0);
	let boxes = $state<string[]>([]);
</script>

<div class="disagrees" class:none={waiting === 0}>
	{#if waiting > 0}
		<p class="how-many">
			{waitingText(waiting, boxes)}
		</p>
	{/if}
	<ReconcilePanel
		{subject}
		{localId}
		onchange={(found, named) => {
			waiting = found;
			boxes = named;
			onchange?.(found, named);
		}}
		{onwritten}
	/>
</div>

<style>
	.disagrees {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* `display: contents`: the panel inside still asks, but this is not a box while empty. */
	.none {
		display: contents;
	}

	.how-many {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
