<script lang="ts">
	/* One queue, full width, with the ones it shares a page with beside its heading. */
	import { page } from '$app/state';
	import { untrack, type Snippet } from 'svelte';

	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import { Empty, Problem, Skeleton } from '$lib/components/common';
	import Pager, { type PagerProps } from '$lib/components/common/Pager.svelte';
	import OrganizeHeader from '$lib/components/organize/OrganizeHeader.svelte';
	import { needsWiderWindow, panelFor, WIDER_WINDOW } from '$lib/organize/panels';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { heldBoard, type Board, answered } from '$lib/organize/organize.svelte';
	import { organizeCrumbs, tabsFor } from '$lib/organize/bands';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { FACES_WORDS, wordsIn } from '$lib/components/shell/wall-words';

	const name = $derived(page.params.queue ?? '');

	/* The board as last held, so this screen draws immediately with what Organize already knew
	   and replaces it when the fresh answer lands: the same shape as the board's own screen. */
	let found = $state<Board | null>(heldBoard.found);
	let failed = $state(false);

	const ready = $derived(found !== null || failed);
	/* Whether the board has answered and says this install has no queue of this name. */
	const absent = $derived(found !== null && !found.queues.some((one) => one.name === name));
	const queues = $derived(found?.queues ?? []);
	const here = $derived(queues.find((one) => one.name === name));
	const Panel = $derived(panelFor(name));

	/* The panel's pager, drawn in the FRAME's foot: the same place every screen in Sift puts it. */
	let pager = $state<PagerProps | null>(null);
	/* A tab searched by the words in its address counts what the search found: the panel's pager
	   holds that total, the board's count is of everything. */
	const searchedCount = $derived(wordsIn(page.url, FACES_WORDS) && pager ? pager.total : undefined);

	/* And the panel's controls for the whole tab, drawn at the far end of the TAB LINE: the one
	   slot for them, the same way the pager has one. */
	let tools = $state<Snippet | null>(null);

	function drawn(): string[] {
		const tabs = tabsFor(found?.queues ?? [], name).map((one) => one.name);
		return tabs.length > 0 ? tabs : [name];
	}

	/* The first read: the queues this screen draws where a board is held, the whole board (which
	   the trail and the tabs are read from) where none is. */
	async function load() {
		try {
			found = found ? await heldBoard.refreshOnly(drawn()) : await heldBoard.refresh();
			failed = false;
		} catch {
			failed = true;
		}
	}

	/* Asked again when the library moves or something is decided: only the queues this screen
	   draws, not a survey of every queue. */
	async function reload() {
		try {
			found = await heldBoard.refreshOnly(drawn());
			failed = false;
		} catch {
			failed = true;
		}
	}

	/* The first read, made while this screen is being set up and NOT from an effect. */
	void load();

	/* And again whenever anything is decided, but not for the stamp this screen started with,
	   which the read above has already answered. */
	let seen = untrack(() => answered.stamp);
	$effect(() => {
		const now = answered.stamp;
		if (now === seen) return;
		seen = now;
		void reload();
	});

	reloadOnLibraryChange(() => void reload());
</script>

<svelte:head><title>{here?.title ?? 'Organize'}</title></svelte:head>

<!-- No pager under an empty page: there is nothing to step through. -->
<PageFrame
	footer={pager && pager.total > 0 ? pagerFooter : undefined}
	crumbs={organizeCrumbs(found?.queues ?? [], name, undefined)}
>
	{#snippet header()}
		<!--
			One shape for every screen under Organize: the trail, the title, the tabs under it, and no
			way back on the right.
		-->
		<OrganizeHeader
			queue={name}
			lede={here?.decision || here?.advice ? lede : undefined}
			controls={tools ?? undefined}
			litCount={searchedCount}
		></OrganizeHeader>
	{/snippet}
	<!--
		The PANEL does not wait for the board. It fetches its own list, so its thumbnails are not held
		behind a survey of every queue.
	-->
	{#if Panel === undefined}
		{#if !ready}
			<Skeleton lines={3} />
		{:else if failed}
			<Problem message="That couldn't be loaded. Try again in a moment." />
		{:else if here === undefined}
			<!-- A queue this install does not have. Not an error page: the feature behind it is
			     switched off, or it belongs to a version this one is not. -->
			<Empty scope="page" title="No queue has that name">
				Nothing in Organize has that name. <a href="/organize">Go back to Organize.</a>
			</Empty>
		{:else}
			<Empty scope="page" title="This version can't open that">
				<a href="/organize">Go back to Organize.</a>
			</Empty>
		{/if}
	{:else if absent}
		<Empty scope="page" title="No queue has that name">
			Nothing in Organize has that name. <a href="/organize">Go back to Organize.</a>
		</Empty>
	{:else if phoneWidth.yes && needsWiderWindow(name)}
		<!-- A contact sheet on a phone: said, and the tabs above still lead to the queues that are
		     one question at a time. See `needsWiderWindow`. -->
		<Empty scope="page" title={WIDER_WINDOW.title}>{WIDER_WINDOW.body}</Empty>
	{:else}
		<Panel
			onpaging={(reported: PagerProps | null) => (pager = reported)}
			ontools={(reported: Snippet | null) => (tools = reported)}
		/>
	{/if}
</PageFrame>

<!-- What deciding one does, then how to work through the pile, in the queue's own words. -->
{#snippet lede()}
	{[here?.decision, here?.advice].filter(Boolean).join(' ')}
	<!-- Work the pile is waiting on elsewhere (`Summary.aside`): a line of its own, with the one
	     place it is done, because it is not a way to work through this page. -->
	{#if here?.aside}
		<span class="aside">{here.aside.said} <a href={here.aside.href}>{here.aside.link}</a></span>
	{/if}
{/snippet}

{#snippet pagerFooter()}
	{#if pager}
		<Pager {...pager} />
	{/if}
{/snippet}

<style>
	.aside {
		display: block;
		margin-block-start: var(--space-2);
	}
</style>
