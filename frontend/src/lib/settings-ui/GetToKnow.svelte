<script lang="ts">
	/* Get to know Sift: the learning paths, each made of goals drawn from every part of the app. */
	import Path from '$lib/components/insights/Path.svelte';
	import { COPY } from './GetToKnow.search';
</script>

<p class="lede">{COPY.lede}</p>

<Path />

<style>
	.lede {
		margin: 0 0 var(--space-6);
	}
</style>
