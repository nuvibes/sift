<script lang="ts">
	/*
	 * One item of a queue, opened up.
	 *
	 * The same arrangement as the queue above it: the address says which queue, the panel registry
	 * says what draws it, and this file knows neither. A queue that grows a detail screen later
	 * declares it there and appears here without this changing.
	 *
	 * ## The frame, and which half of this file wears it
	 *
	 * Each detail screen draws its own `PageFrame`, with its own trail, heading and pager: the
	 * pile, the person's proposals and the duplicate chain all do. So this file must not draw one
	 * around them: two frames is two scrolling regions and the page's inset counted twice.
	 *
	 * The other branch needs one of its own. This route IS in the layout's `FULL_BLEED_ROUTES`, so
	 * the shell hands it the whole box with no padding and no scroll of its own, and a bare `Empty`
	 * for a queue name this build has no drawing for would land hard against the top of the screen
	 * with no trail and no way back but the words. So it is a page, like the queue screen's own
	 * version of the same message.
	 */
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
