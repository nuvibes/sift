<script lang="ts">
	/* A harness for one board card, drawn as the board draws it: an item of the one list. */
	import BoardCard from './BoardCard.svelte';
	import type { Card } from '$lib/organize/bands';
	import type { Queue } from '$lib/organize/organize.svelte';

	let { card }: { card: Card<Queue> } = $props();
</script>

<ul aria-label="Organize">
	<li><BoardCard {card} /></li>
</ul>
