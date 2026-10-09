<script lang="ts">
	/*
	 * THE ONE COPY HELPER: words that copy themselves when pressed, with a tooltip saying Copy,
	 * then Copied, then Copy again. `Pressable`, since its size is the text's; through `copyText`,
	 * since plain http has no `navigator.clipboard`. A toast only when it fails.
	 */
	import type { Snippet } from 'svelte';
	import { onDestroy } from 'svelte';

	import { Pressable, Tooltip } from '$lib/components/common';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';

	interface Props {
		text: string;
		what?: string;
		class?: string;
		shrinks?: boolean;
		children?: Snippet;
	}

	let { text, what = 'That', class: className = '', shrinks = false, children }: Props = $props();

	const COPIED_MS = 1600;

	let copied = $state(false);
	let copiedFor: ReturnType<typeof setTimeout> | null = null;

	async function copy() {
		if (text === '') return;
		if (!(await copyText(text))) {
			toasts.show(`${what} couldn't be copied`, { tone: 'error' });
			return;
		}
		if (copiedFor) clearTimeout(copiedFor);
		copied = true;
		copiedFor = setTimeout(() => (copied = false), COPIED_MS);
	}

	onDestroy(() => {
		if (copiedFor) clearTimeout(copiedFor);
	});
</script>

<Tooltip label={copied ? 'Copied' : 'Copy'} staysOnPress {shrinks}>
	<Pressable
		class="copyable {className}"
		feedback="wash"
		radius="sm"
		pad="sm"
		onclick={() => void copy()}
	>
		{#if children}{@render children()}{:else}{text}{/if}
	</Pressable>
</Tooltip>

<style>
	/* `:global`: the class is handed to `Pressable`. */
	:global(.copyable) {
		max-inline-size: 100%;
		min-inline-size: 0;
		font: inherit;
		color: inherit;
		text-align: start;
	}
</style>
