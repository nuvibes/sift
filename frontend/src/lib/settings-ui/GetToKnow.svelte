<script lang="ts">
	/*
	 * Get to know Sift: the learning paths, each made of goals drawn from every part of the app.
	 *
	 * `Path` is the one drawing of them and fetches its own answer, so this pane holds only the
	 * sentence that says what the section is for. A section beside Profile because learning the app
	 * is about the person doing it, and every signed-in person has paths of their own.
	 */
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
