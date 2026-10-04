<script lang="ts">
	// The art is markup rather than a file behind an <img>. An SVG loaded through a src is a separate
	// document and cannot see this page's stylesheet, so the accent would never reach the grain.
	import Mark from '$lib/brand/Mark.svelte';
	import Wordmark from '$lib/brand/Wordmark.svelte';

	interface Props {
		/** `lockup` is the mark plus the word; `mark` is the mark alone, for tight spaces. */
		variant?: 'lockup' | 'mark';
		/** Rendered height in px. Vector, so any value is sharp. */
		height?: number;
	}

	let { variant = 'lockup', height = 28 }: Props = $props();
</script>

<span class="logo" role="img" aria-label="Sift">
	{#if variant === 'lockup'}
		<Wordmark {height} />
	{:else}
		<Mark {height} />
	{/if}
</span>

<style>
	.logo {
		/* The letterforms are drawn with fill="currentColor", so they take the surrounding ink. The
		   grain names the accent for itself. */
		display: inline-flex;
		color: var(--sift-ink);
		user-select: none;
	}
</style>
