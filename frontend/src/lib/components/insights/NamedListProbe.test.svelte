<script lang="ts">
	/* Holds one list and hands it a second, the way a change of period does. */
	import type { components } from '$lib/api/schema';
	import NamedList from './NamedList.svelte';

	type List = components['schemas']['NamedList'];

	interface Props {
		first: List;
	}

	let { first }: Props = $props();

	let list = $state<List | null>(null);

	export function show(next: List): void {
		list = next;
	}
</script>

<NamedList list={list ?? first} />
