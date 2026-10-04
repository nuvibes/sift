<script lang="ts">
	/*
	 * FileVerbs, mounted, with the verbs it hands its children passed straight out to the test, so
	 * a test can press the rows of each door (the selection bar's, a file's own, the right-click
	 * menu's) without opening a menu the unit environment cannot draw.
	 */
	import FileVerbs from './FileVerbs.svelte';
	import type { Selection } from './selection.svelte';
	import type { Verb } from './verbs';
	import type { Actionable } from '$lib/grid/actions.svelte';

	interface Doors {
		bar: (ids: string[]) => Verb[];
		menu: (ids: string[], subjectId?: string) => Verb[];
	}

	interface Props {
		items: Actionable[];
		selection: Selection;
		ondoors: (doors: Doors) => void;
	}

	let { items, selection, ondoors }: Props = $props();
</script>

<FileVerbs {items} around={{ selection }}>
	{#snippet children(verbs)}
		{@const handed = ondoors({ bar: verbs.bar, menu: verbs.menu })}
		{handed ?? ''}
	{/snippet}
</FileVerbs>
