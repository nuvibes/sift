<script lang="ts">
	/* The box you type into to find a setting. The box, and nothing else. */
	import { NarrowBox } from '$lib/components/common';
	import { matches } from '$lib/shell/shortcuts';

	interface Props {
		typed?: string;
		activeId?: string;
		/** What the box is called, and what it says while empty. */
		label?: string;
		/** The name of the row a pasted settings path just opened, while the moment lasts. */
		landed?: string;
	}

	let { typed = $bindable(''), activeId, label = 'Search settings', landed }: Props = $props();

	/* "Opened How often": the verb a result's press is, and the row's own name. */
	const said = $derived(landed === undefined ? label : `Opened ${landed}`);

	/* The field itself, so Ctrl-F can put the caret in it. */
	let wrap = $state<HTMLElement | null>(null);
	const box = () => wrap?.querySelector('input') ?? null;

	/* ESCAPE AND THE CROSS ARE `NarrowBox`'s, so every box that narrows a list has them. */

	/* Ctrl-F, while Settings is up, means THIS box. */
	function claimFind(event: KeyboardEvent) {
		if (!matches(event, 'app.search')) return;
		event.preventDefault();
		box()?.focus();
		box()?.select();
	}
</script>

<svelte:document onkeydown={claimFind} />

<!-- `NarrowBox`, the one box in Sift that narrows a list as you type. -->
<div bind:this={wrap} class:landed={landed !== undefined}>
	<NarrowBox
		{label}
		inset="row"
		aria-activedescendant={activeId}
		placeholder={said}
		title={landed === undefined ? undefined : said}
		spellcheck="false"
		bind:value={typed}
	/>
	<span class="unseen" aria-live="polite">{landed === undefined ? '' : said}</span>
</div>

<style>
	/* The paste landed: the box wears the found row's own mark for one ambient pace. */
	.landed :global(.narrow) {
		animation: sift-found var(--dur-ambient) var(--ease);
	}

	/* A row's name can be longer than the box: whole on the tooltip, cut with an ellipsis once
	   blurred. */
	.landed :global(.narrow) {
		text-overflow: ellipsis;
	}
</style>
