<script lang="ts">
	/*
	 * One hint, where somebody is, shown once and never again.
	 *
	 * One sentence and one Close. Mount it inside whatever condition makes it the moment (the
	 * Organize board with nothing on it, a face group opened, Insights) and it decides for itself
	 * whether this account has already been shown it. The rule that makes it once-only is in
	 * `your-path.ts` (`claimHint`, `hintShown`): the account is told the moment the hint is DRAWN,
	 * not when Close is pressed, because a hint that returned on every visit until dismissed would
	 * be a nag.
	 */
	import { onMount } from 'svelte';

	import Button from '$lib/components/common/Button.svelte';
	import Note from '$lib/components/common/Note.svelte';
	import Panel from '$lib/components/common/Panel.svelte';
	import { HINT_WORDS, claimHint, hintShown, type HintName } from './your-path';

	interface Props {
		name: HintName;
	}

	let { name }: Props = $props();

	let showing = $state(false);

	onMount(() => {
		let here = true;
		void claimHint(name).then((due) => {
			if (!here || !due) return;
			showing = true;
			hintShown(name);
		});
		return () => {
			here = false;
		};
	});
</script>

{#if showing}
	<Panel tone="recessed" inset="sm" corner="md" row>
		<span class="words"><Note>{HINT_WORDS[name]}</Note></span>
		<Button size="small" tone="ghost" onclick={() => (showing = false)}>Close</Button>
	</Panel>
{/if}

<style>
	/* The sentence left, taking the room, and its one control at the end of the row. */
	.words {
		flex: 1;
		min-inline-size: 0;
	}
</style>
