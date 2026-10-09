<script lang="ts">
	/* One item of a queue, opened up. The same arrangement as the queue above it: the address
	 * says which queue, the panel registry says what draws it, and this file knows neither. */
	import { page } from '$app/state';
	import { Empty } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';

	import { organizeCrumbs } from '$lib/organize/bands';
	import { detailFor, needsWiderWindow, WIDER_WINDOW } from '$lib/organize/panels';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';

	const name = $derived(page.params.queue ?? '');
	const Detail = $derived(detailFor(name));
</script>

{#if Detail && phoneWidth.yes && needsWiderWindow(name)}
	<!-- One pile or one chain is the same contact sheet as its queue: said on a phone, in a frame
	     with its trail, as the queue says it. See `needsWiderWindow`. -->
	<PageFrame crumbs={organizeCrumbs([], name, undefined)}>
		<Empty scope="page" title={WIDER_WINDOW.title}>{WIDER_WINDOW.body}</Empty>
	</PageFrame>
{:else if Detail}
	<Detail />
{:else}
	<PageFrame crumbs={organizeCrumbs([], undefined, 'Not found')}>
		<Empty scope="page" title="No queue has that name">
			Nothing in Organize has that name. <a href="/organize">Go back to Organize.</a>
		</Empty>
	</PageFrame>
{/if}
