<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'AssetLink',
		category: 'primitive',
		role: 'a link to a file that opens it in place instead of tearing down the screen behind it',
		basis: 'site:<a>',
		states: ['default', 'hover']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: it is an anchor. There is nothing to behave: the browser already knows how
	to be a link; a plain left-click opens the file's panel instead, as `SettingLink` does. */

	/*
	 * A link to one file that opens its panel in place; the bare /asset route rebuilds the screen.
	 */
	import type { Snippet } from 'svelte';
	import { openAssetInstead } from '$lib/player/asset-view';

	interface Props {
		id: string;
		/** The words. A filename, a title: what it is, never "open". */
		children: Snippet;
		/** A class from the caller, for a link that has to sit in that screen's own type. */
		extra?: string;
	}

	let { id, children, extra = '' }: Props = $props();
</script>

<a class="asset-link {extra}" href="/asset/{id}" onclick={(event) => openAssetInstead(event, id)}>
	{@render children()}
</a>

<style>
	/* No underline until hover or focus; the text accent, which holds 4.5:1 on every surface. */
	.asset-link {
		color: var(--sift-accent-text);
		text-decoration: none;
		text-underline-offset: 2px;
		border-radius: var(--radius-sm);
		/* Colour only: a fading underline reads as the text moving. */
		transition: color var(--dur-instant) var(--ease);
	}

	/* The underline alone: the accent's hover is not held to the text floor. */
	.asset-link:hover,
	.asset-link:focus-visible {
		text-decoration: underline;
	}
</style>
