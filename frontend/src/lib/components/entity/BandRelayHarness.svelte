<script lang="ts">
	/* The hop every entity page actually takes, on its own so a test can stand on it.
	 *
	 * No entity page hands its identity band to a frame directly. It hands it to `AssetGrid` or to
	 * `RelatedWall` as `above`, and THAT renders it inside the frame's header snippet, so the
	 * header is declared two components away from the frame it ends up inside.
	 *
	 * That distance is the whole question the backdrop rests on: `theBackdrop()` reads a context, a
	 * context is resolved where a component is CREATED, and a snippet is created where it is
	 * rendered rather than where it is written. A harness that passed the header straight to the
	 * frame would prove the easy case and leave the real one to the first person to open a person's
	 * page. This is the real one, cut down to the forwarding and nothing else.
	 */
	import type { Snippet } from 'svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';

	interface Props {
		/** Whatever the screen puts above the wall. The grid's own prop, by its own name. */
		above: Snippet;
	}

	let { above }: Props = $props();
</script>

<PageFrame>
	{#snippet header()}
		<PageAbove>{@render above()}</PageAbove>
	{/snippet}
	{#snippet children()}
		<p>The files, on a real page.</p>
	{/snippet}
</PageFrame>
