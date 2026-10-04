<script lang="ts">
	/* A harness for a menu drawn from a declared list, with the surface's own rows beside it, which
	   takes a snippet, and a snippet cannot be handed to `mount` from a test file. */
	import MenuButton from './MenuButton.svelte';
	import ContextMenuItem from './ContextMenuItem.svelte';
	import VerbMenuItems from './VerbMenuItems.svelte';
	import type { Verb } from './verbs';

	interface Props {
		verbs: Verb[];
		/** Draw the surface's own rows: `'rows'` two of them, `'none'` a snippet that draws nothing. */
		extra?: 'rows' | 'none';
	}

	let { verbs, extra }: Props = $props();
</script>

{#snippet own()}
	{#if extra === 'rows'}
		<ContextMenuItem label="Use as the cover" onselect={() => {}} />
	{/if}
{/snippet}

<MenuButton label="More for this file">
	<VerbMenuItems {verbs} ids={['one']} extra={extra ? own : undefined} />
</MenuButton>
