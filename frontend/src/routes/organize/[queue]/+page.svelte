<script lang="ts">
	/*
	 * One queue, full width, with the ones it shares a page with beside its heading.
	 *
	 * Full width because the work here is looking at a contact sheet and deciding about the whole
	 * of it, and that needs the room.
	 *
	 * ## Tabs
	 *
	 * What belongs beside a heading is that heading's own ALTERNATIVES, which is what a tab is:
	 * not every queue in the application, which would be a second navigation competing with the
	 * rail and with the work. So the row is `Tabs` (the same primitive every entity page in
	 * Sift wears, quiet words rather than pills, each a real address, the live one announced), and
	 * it holds the queues sharing this one's GROUP. Near duplicates sit beside exact copies; a
	 * queue that stands alone draws no row at all rather than a row with one word in it.
	 *
	 * The group is DECLARED BY THE QUEUE on the server, beside its band, so a queue joining a page
	 * is a one-word change there with no edit here. See `kernel/workbench.Queue.group`.
	 *
	 * The page is titled with the group's name ("Faces") or the queue's own where it stands alone,
	 * and the tabs sit directly under the title, the way an entity page's do. See `OrganizeHeader`.
	 *
	 * This screen still knows nothing about what it is drawing. It looks up the queue by name, asks
	 * the panel registry what draws it, and renders that. A panel added later appears here without
	 * this file changing.
	 *
	 * ## No thread above the work
	 *
	 * This queue's history is in Settings > Tasks and Activity > App History, which is every act in the library in one list
	 * with the same Undo. This screen's promise is that it EMPTIES, and a list that only ever grows
	 * would be the one thing on it that never looked finished.
	 */
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

	/* The board as last held, so this screen draws immediately with what Organize already knew and
	   replaces it when the fresh answer lands: the same shape as the board's own screen. */
	let found = $state<Board | null>(heldBoard.found);
	let failed = $state(false);

	const ready = $derived(found !== null || failed);
	/* Whether the board has answered and says this install has no queue of this name. Only an
	   ANSWER can say that: until one lands the queue is assumed to exist, so the panel is drawn at
	   once and its own list is asked for in parallel with the board rather than after it. */
	const absent = $derived(found !== null && !found.queues.some((one) => one.name === name));
	const queues = $derived(found?.queues ?? []);
	const here = $derived(queues.find((one) => one.name === name));
	const Panel = $derived(panelFor(name));

	/* The panel's pager, drawn in the FRAME's foot: the same place every screen in Sift puts it.
	   A panel drawing its own under its last row would put the band at a different height on
	   every queue and nowhere near where Browse keeps it. A panel that pages reports here; one
	   that does not says nothing and the frame draws no foot. See `PagerProps`. */
	let pager = $state<PagerProps | null>(null);
	/* A tab searched by the words in its address counts what the search found: the panel's pager
	   holds that total, the board's count is of everything. See `OrganizeHeader.litCount`. */
	const searchedCount = $derived(wordsIn(page.url, FACES_WORDS) && pager ? pager.total : undefined);

	/* And the panel's controls for the whole tab, drawn at the far end of the TAB LINE: the
	   one slot for them, the same way the pager has one. Reported by the panel as a snippet,
	   taken back with null as it goes; a panel that never reports leaves this null and the
	   header draws nothing there. See `OnTools`. */
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

	/* The first read, made while this screen is being set up and NOT from an effect.
	 *
	 * A child's effects run before its parent's: from an effect, the header's `ensure` would find
	 * nothing in flight and ask for the board itself, and this screen's `refresh` (which never
	 * joins an ask it did not make) would ask again: two surveys of every queue on every
	 * arrival, served one after the other, with the panel waiting for both. Asked here, the ask
	 * is in flight before the header exists, so the header joins it.
	 */
	void load();

	/* And again whenever anything is decided, but not for the stamp this screen started with,
	   which the read above has already answered. The same shape as `whenChanged`: remember what
	   was current, act only when it moves. A decision made inside a panel changes counts this
	   screen is drawing. */
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
		<!-- One shape for every screen under Organize: the trail, the title, the tabs under it,
		     and no way back on the right. See `OrganizeHeader`, which is where all of that
		     lives.

		     The lede is the queue's two sentences, in order: what deciding one of these DOES
		     (`Summary.decision`, the same words the board card carries as its title's tooltip),
		     then how to work through the pile (`Summary.advice`, which almost no queue has).
		     Drawn here rather than inside a panel because both belong to the heading: they are
		     true of the whole screen, and a panel that drew its own would be many places for
		     one kind of sentence. A tooltip is nothing on a touch screen and nothing to a
		     screen reader, so this page is the one place the sentence is guaranteed to be read. -->
		<OrganizeHeader
			queue={name}
			lede={here?.decision || here?.advice ? lede : undefined}
			controls={tools ?? undefined}
			litCount={searchedCount}
		></OrganizeHeader>
	{/snippet}
	<!-- The PANEL does not wait for the board. It fetches its own list, so its thumbnails are
	     not held behind a survey of every queue. A name the registry knows is drawn immediately;
	     the board fills in the header and the counts when it lands, and only its ANSWER can say
	     the queue is not here. -->
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

<!-- What deciding one does, then how to work through the pile, in the queue's own words. See the
     header above. One paragraph: they are one telling, and the header draws one lede. -->
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
