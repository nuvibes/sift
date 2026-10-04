<script lang="ts">
	/*
	 * The names the unattended enrichment could not choose for.
	 *
	 * A stash-box holding two certain entries for a name is a judgement about which of the two is
	 * this person, and the enrichment declines it. Without this list the decline would be a number
	 * in a job's note ("24 had more than one match") and nowhere else, with nothing to work from.
	 * Each row opens the chooser HERE, over the queue, with the name already in it; a row goes the
	 * moment the record is linked, by whatever linked it.
	 */
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { DataRow, DataRows, Empty, Pressable, Problem, Skeleton } from '$lib/components/common';
	import LinkToStashBox from '$lib/components/record/LinkToStashBox.svelte';
	import type { LinkSubject } from '$lib/entity/enrich.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import type { IconName } from '$lib/design/icons';
	import { onDestroy, untrack } from 'svelte';
	import { page as address } from '$app/state';
	import { anchorIn, asked, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';

	type Undecided = components['schemas']['UndecidedEntity'];

	const PAGE = 50;

	let { onpaging }: { onpaging?: OnPaging } = $props();

	let items = $state<Undecided[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state(false);

	/* Which page: the same paging every list in Organize has (`CardPaging`), with the name the page
	   starts at written into the address as `from` (and where it was, `near`) so the way back lands
	   on this page again, and a name chosen for since, which leaves the list, is answered with
	   where the page was. */
	const paging = new CardPaging(PAGE);
	const path = address.url.pathname;
	let arriving = true;

	async function load() {
		failed = false;
		try {
			const page = await paging.fill(
				'',
				() => items,
				(query) => {
					// Only when a request goes out: a landing asks nothing.
					loading = true;
					return api.get<components['schemas']['UndecidedList']>('/stash-boxes/undecided', {
						query: asked(query)
					});
				},
				(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset ?? 0 })
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			items = page.rows;
			total = page.total;
			// In the same synchronous turn as the rows. See `CardPaging.land`.
			paging.land(page.offset);
			rememberAnchor(address.url, path, items[0]?.id, page.offset);
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	/* A page turned. The anchor in the address is honoured once, on arrival. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		untrack(() => void load());
	});

	/* And again, at the same place, whenever the library's shape changes underneath. */
	whenChanged(libraryChanges, () => void load());

	$effect(() => {
		onpaging?.(paging.asPager(items.length, total, 'names'));
	});
	onDestroy(() => onpaging?.(null));

	const KINDS: Record<string, { wall: string; icon: IconName; word: string }> = {
		person: { wall: 'people', icon: 'person', word: 'Person' },
		site: { wall: 'sites', icon: 'public', word: 'Site' },
		tag: { wall: 'tags', icon: 'shoppingmode', word: 'Tag' }
	};

	/**
	 * What a row opens: the chooser, on this page, over the queue, so the entity link never leaves
	 * the page.
	 *
	 * The chooser needs what Sift holds for every field and the record's own save, and both come
	 * from the server: what Sift holds is read through the subject's writer (`GET
	 * /stash-boxes/record/...`, the same `current` every plan compares against), and the ticked
	 * fields are taken through the take route, which writes them with the same writer and records
	 * what landed. Nothing of the record screen lives here, only the sheet, which is the same
	 * component.
	 *
	 * Not the reconciliation sheet: it draws a linked box's answers against Sift's own, and a row
	 * is on this tab because nothing is linked yet, so it would open empty on every row.
	 */
	let choosing = $state<Undecided | null>(null);
	let values = $state<Record<string, unknown>>({});
	let open = $state(false);
	let problem = $state<string | undefined>();

	async function choose(one: Undecided): Promise<void> {
		problem = undefined;
		try {
			// Read BEFORE the sheet opens, so the first entry somebody picks is measured against
			// what is really held: a field shown as blank would start ticked and replace a value.
			const held = await api.get<components['schemas']['RecordHeld']>(
				`/stash-boxes/record/${one.subject}/${one.id}`
			);
			values = held.values;
			choosing = one;
			open = true;
		} catch {
			problem = `The details for ${one.name} couldn't be loaded. Try again.`;
		}
	}

	/* A link was kept (or forgotten, or asked again): the row this was about leaves the list on the
	   next read, which the server decides: linking clears it, by whatever linked it. */
	function linked(): void {
		void load();
	}
</script>

<section>
	{#if failed}
		<Problem message="Names to choose for couldn't be loaded. Refresh the page to try again." />
	{:else if loading && items.length === 0}
		<Skeleton lines={3} />
	{:else if total === 0}
		<Empty scope="page" icon="inventory_2" title="Nothing to choose">
			When a stash-box has more than one entry for a name, the name appears here until you choose
			the right one.
		</Empty>
	{:else}
		<Problem message={problem} />
		<DataRows
			{items}
			key={(one: Undecided) => `${one.subject}:${one.id}`}
			label="Names to choose for"
		>
			{#snippet row(one: Undecided)}
				{@const kind = KINDS[one.subject] ?? KINDS.person}
				<DataRow compact>
					<!-- The glyph and the name on one line, centred on each other: the press is a
					     block of its own, so beside a bare glyph it would take the line under it. -->
					<span class="named">
						<Tooltip label={kind.word}>
							<span class="kind" role="img" aria-label={kind.word}>
								<Icon name={kind.icon} size={16} />
							</span>
						</Tooltip>
						<Pressable
							class="undecided-name"
							aria-label="Choose {one.name}'s entry"
							onclick={() => void choose(one)}>{one.name}</Pressable
						>
					</span>
					{#snippet trailing()}
						<span class="data">{one.candidates} entries, open to choose</span>
					{/snippet}
				</DataRow>
			{/snippet}
		</DataRows>
	{/if}
</section>

<!-- One sheet for the page, re-aimed at whichever row was pressed. Keyed on the row so a second
     row gets a fresh sheet rather than the first one's search and picks. -->
{#if choosing}
	{#key `${choosing.subject}:${choosing.id}`}
		<LinkToStashBox
			bind:open
			subject={choosing.subject as LinkSubject}
			id={choosing.id}
			name={choosing.name}
			{values}
			onlinked={linked}
		/>
	{/key}
{/if}

<style>
	.named {
		display: flex;
		align-items: center;
		min-inline-size: 0;
	}

	.kind {
		display: inline-flex;
		margin-inline-end: var(--space-2);
		color: var(--sift-ink-3);
	}

	/* `:global` because the class lands on `Pressable`'s own element, compiled in that file, and
	   held under this section and under a class no other component wears, because a bare
	   `:global(.name)` in one component restyles every `.name` in the application. */
	section :global(.undecided-name) {
		color: var(--sift-accent-text);
		text-decoration: none;
		overflow-wrap: anywhere;
	}

	section :global(.undecided-name:hover) {
		text-decoration: underline;
	}

	.data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}
</style>
