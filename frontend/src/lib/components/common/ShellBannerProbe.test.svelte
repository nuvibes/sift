<script lang="ts">
	/* A host for `ShellBanner`, because both of the things it takes are snippets and a snippet
	   cannot be written in a `.ts` file. Nothing here has behaviour; it is the smallest markup that
	   exercises both slots.

	   The action snippet is declared OUTSIDE any block and handed over as a prop. Declared inside an
	   `{#if}` it would be scoped to that block, so the component would be handed nothing and the
	   markup would read as though the slot were broken. */
	import Button from './Button.svelte';
	import ShellBanner from './ShellBanner.svelte';

	interface Props {
		withAction?: boolean;
		testId?: string;
	}

	let { withAction = false, testId }: Props = $props();
</script>

{#snippet doIt()}
	<Button>Do it</Button>
{/snippet}

<ShellBanner {testId} action={withAction ? doIt : undefined}>Something worth knowing.</ShellBanner>
