<script lang="ts">
	/*
	 * Every person, site and tag a stash-box has been agreed to know: the record tab beside the
	 * pile of files a box recognised.
	 *
	 * A record, not a decision. Auto-enrich and Enrich write a link and fill what is blank, and
	 * this lists what they have done. Paged like every long list in Sift, newest first, with the
	 * pager in the frame's foot; a glyph on each row says which kind of thing it is, and the kind
	 * can be filtered to. A link comes off from the record itself.
	 */
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { DataRow, DataRows, Empty, Problem, Select, Skeleton } from '$lib/components/common';
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import PanelBar from '$lib/components/organize/PanelBar.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import type { IconName } from '$lib/design/icons';
	import { onDestroy, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page as address } from '$app/state';
	import { anchorIn, asked, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { exactly, onRecord } from '$lib/shell/when';

	type Linked = components['schemas']['LinkedEntity'];

	const PAGE = 50;

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
	let { onpaging }: { onpaging?: OnPaging } = $props();

	let items = $state<Linked[]>([]);
	let total = $state(0);

	/*
	 * Which page: the same paging every list in Organize has (`CardPaging`), with the link the page
	 * starts at written into the address as `from` (and where it was, `near`), so the way back from
	 * a person, a Site or a tag lands on this page again. See `DuplicatesPanel`.
	 */
	const paging = new CardPaging(PAGE);
	const path = address.url.pathname;
	let arriving = true;

	/* Which kind is showing: in the address, as the tagger's state is, because it decides which
	   list the row in `from` is a place in. Held in memory it would be lost on the way back. */
	const KIND = 'kind';
	const subject = $derived(kindIn(address.url));

	function kindIn(url: URL): string {
		const kind = url.searchParams.get(KIND) ?? '';
		// Own keys only: `in` would take `toString` from an address for a kind.
		return Object.hasOwn(KINDS, kind) ? kind : '';
	}
	/* Files carrying an answer somebody agreed to: the other half of what the boxes enriched.
	   Said here because the Browse column counts FILES and this tab lists SUBJECTS, and the two
	   numbers read as a disagreement until the screen says which is which. */
	let files = $state(0);
	let loading = $state(true);
	let failed = $state(false);

	async function load() {
		failed = false;
		const kind = subject;
		try {
			const page = await paging.fill(
				kind,
				() => items,
				(query) => {
					// Only when a request goes out: a landing asks nothing.
					loading = true;
					return api.get<components['schemas']['LinkedLedger']>('/stash-boxes/linked', {
						query: { subject: kind, ...asked(query) }
					});
				},
				(ledger) => ({ rows: ledger.items, total: ledger.total, offset: ledger.offset ?? 0 })
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			items = page.rows;
			total = page.total;
			if (page.answer) files = page.answer.files;
			// In the same synchronous turn as the rows. See `CardPaging.land`.
			paging.land(page.offset);
			const first = items[0];
			rememberAnchor(address.url, path, first ? key(first) : null, page.offset);
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	/* A page turned, or the kind moved. The anchor in the address is honoured once, on arrival;
	   another kind is another list, so it starts at its top with no anchor carried across. */
	let kindAt: string | null = null;
	$effect(() => {
		void paging.offset;
		void paging.size;
		const kind = subject;
		if (arriving) {
			arriving = false;
			kindAt = kind;
			paging.arrive(untrack(() => anchorIn(address.url)));
		} else if (kind !== kindAt) {
			kindAt = kind;
			// A page other than the first is moved to the first, and that move is what reads it.
			if (untrack(() => paging.restart())) return;
		}
		untrack(() => void load());
	});

	/* And again, at the same place, whenever the library's shape changes underneath. */
	whenChanged(libraryChanges, () => void load());

	/** Another kind: a new address, with no anchor. */
	function showKind(kind: string): void {
		void goto(kind ? `${path}?${KIND}=${encodeURIComponent(kind)}` : path, {
			keepFocus: true,
			noScroll: true
		});
	}

	$effect(() => {
		onpaging?.(paging.asPager(items.length, total, 'links'));
	});
	onDestroy(() => onpaging?.(null));

	/** The three kinds, with where each one's page is and the glyph its wall wears. */
	const KINDS: Record<string, { wall: string; icon: IconName; word: string }> = {
		person: { wall: 'people', icon: 'person', word: 'Person' },
		site: { wall: 'sites', icon: 'public', word: 'Site' },
		tag: { wall: 'tags', icon: 'shoppingmode', word: 'Tag' }
	};

	const kindOptions = [
		{ value: '', label: 'Everything' },
		{ value: 'person', label: 'People' },
		{ value: 'site', label: 'Sites' },
		{ value: 'tag', label: 'Tags' }
	];

	/** Which rows have been opened out, by the same key the list is drawn on. One screen's
	 *  arrangement while somebody reads it, not a preference. */
	let opened = $state<Set<string>>(new Set());

	function key(one: Linked): string {
		return `${one.id}:${one.box_id}`;
	}

	function toggle(one: Linked): void {
		const next = new Set(opened);
		const id = key(one);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		opened = next;
	}

	/*
	 * What the enrichment filled in is the SERVER'S line (`said`), drawn as History draws every
	 * line: each field with the value it took, the aliases, usernames and tags by name, a username
	 * or a tag as a way to its page, a long list folded at five. The browser builds no sentence
	 * (`kernel/access/sentences.py`), so this tab and the record's own History say one thing. A link
	 * made before Sift recorded what a box fills in says exactly that, and what the record agrees
	 * with it on today.
	 */
</script>

<section>
	{#if failed}
		<Problem message="Enriched records couldn't be loaded. Refresh the page to try again." />
	{:else if !loading && total === 0 && subject === ''}
		<Empty scope="page" icon="inventory_2" title="Nothing enriched by a stash-box yet">
			Every person, Site and tag linked to a stash-box appears here, with the stash-box and the
			date.
		</Empty>
	{:else}
		<PanelBar>
			<p class="files">
				{#if files > 0}
					{files === 1 ? '1 file has' : `${files.toLocaleString()} files have`} details from a stash-box.
					<a href="/browse?enriched=stash">See them in Browse</a>. The rows below are the People,
					Sites and tags linked to a stash-box.
				{:else}
					The People, Sites and tags linked to a stash-box.
				{/if}
			</p>
			<Select label="Which kind" value={subject} options={kindOptions} onValueChange={showKind} />
		</PanelBar>
		{#if loading && items.length === 0}
			<Skeleton lines={3} />
		{:else}
			<DataRows {items} key={(one: Linked) => key(one)} label="Links, newest first">
				{#snippet row(one: Linked)}
					{@const kind = KINDS[one.subject] ?? KINDS.person}
					<DataRow
						compact
						expanded={opened.has(key(one))}
						ontoggle={() => toggle(one)}
						toggleLabel="what {one.box} wrote to {one.name}"
					>
						<!-- One line, centred on itself: the glyph has no text of its own, so on the
						     text's baseline it would ride three pixels above the name beside it. -->
						<span class="named">
							<Tooltip label={kind.word}>
								<span class="kind" role="img" aria-label={kind.word}>
									<Icon name={kind.icon} size={16} />
								</span>
							</Tooltip>
							<a class="name" href="/{kind.wall}/{one.id}">{one.name}</a>
							{#if one.known_as && one.known_as !== one.name}
								<span class="known">as {one.known_as}</span>
							{/if}
						</span>
						{#snippet trailing()}
							<!-- When the box last answered is a record, so it says the day; the hover gives
							     the whole moment. -->
							<Tooltip label={exactly(one.fetched_at)}>
								<span class="data">{one.box} &middot; {onRecord(one.fetched_at)}</span>
							</Tooltip>
							<!-- The caret is `DataRow`'s own, at the end of the row, where every list that
							     folds draws it. -->
						{/snippet}
						{#snippet expansion()}
							{#if opened.has(key(one))}
								<p class="wrote"><HistorySentence pieces={one.said} /></p>
							{/if}
						{/snippet}
					</DataRow>
				{/snippet}
			</DataRows>
		{/if}
	{/if}
</section>

<style>
	.files {
		flex: 1 1 24ch;
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.files a {
		color: var(--sift-accent-text);
	}

	.named {
		display: inline-flex;
		align-items: center;
		min-inline-size: 0;
	}

	.kind {
		display: inline-flex;
		margin-inline-end: var(--space-2);
		color: var(--sift-ink-3);
	}

	.name {
		min-inline-size: 0;
		color: var(--sift-accent-text);
		text-decoration: none;
		overflow-wrap: anywhere;
	}

	.name:hover {
		text-decoration: underline;
	}

	.known {
		margin-inline-start: var(--space-2);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	/* Under the row that opened it, indented to the width the row's own glyph takes, so what a
	   box wrote reads as belonging to the link above rather than as a line of its own. */
	.wrote {
		margin: 0 0 var(--space-2) var(--space-5);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}
</style>
