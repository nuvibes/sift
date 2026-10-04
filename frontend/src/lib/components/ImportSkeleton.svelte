<script lang="ts">
	// The placeholder a file wears while it is being taken in. It appears the moment a file is
	// accepted and stays until the real tile, with its thumbnail, exists, so the person sees that
	// something happened at once rather than waiting on a grid that quietly fills in later.
	//
	// A shimmer, never a spinner: a spinner says "working" and a shimmer says "this is becoming
	// content", which is what it is. The amber dot is the same status colour the rest of the app uses
	// for in-progress, so a glance reads it without a legend.
	import Skeleton from '$lib/components/common/Skeleton.svelte';

	interface Props {
		/** The file's name, for anyone who cannot see the shimmer. */
		name?: string;
	}

	let { name }: Props = $props();
</script>

<div class="skeleton" role="img" aria-label={name ? `Importing ${name}` : 'Importing'}>
	<!-- The app's shimmer, not one of its own: two copies of one animation is two speeds the day
	     somebody tunes one. -->
	<Skeleton shape="block" />

	<span class="status" aria-hidden="true">
		<span class="dot"></span>
		Importing&hellip;
	</span>
</div>

<style>
	.skeleton {
		position: relative;
		aspect-ratio: 1;
		border-radius: var(--radius-lg);
		overflow: hidden;
	}

	.status {
		position: absolute;
		inset-inline: 0;
		bottom: var(--space-3);
		display: flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	.dot {
		width: 8px;
		height: 8px;
		border-radius: var(--radius-full);
		background: var(--sift-warn);
	}
</style>
