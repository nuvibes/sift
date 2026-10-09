<script lang="ts">
	/* Usernames nobody has said who they belong to, as a wall of cards. Joining one to a person
	 * counts its files under them; "Who is this?" opens the same PickMenu as Unnamed faces. The
	 * picture is fetched under the username as creator name, the Site a small mark at its foot. */
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import UsernameCard from '$lib/components/entity/UsernameCard.svelte';
	import { coverUrl } from '$lib/entity/art';
	import { counted, filesSaid, joinCounts } from '$lib/entity/entity-counts';
	import { usernames, type Username } from '$lib/people/usernames.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { onDestroy, untrack } from 'svelte';
	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor, type PageAsk } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';

	/** What to ask for before the wall has measured a card: the count it always used. */
	const PAGE = 60;

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
	let { onpaging }: { onpaging?: OnPaging } = $props();

	let items = $state<Username[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state<string | null>(null);

	/* Whole rows of cards with the wall's pager. An answer re-reads at the same place, and the page
	   is kept in the address (`from`, `near`), so coming back opens it again. */
	const paging = new CardPaging(PAGE, 'organize.usernames');
	const path = address.url.pathname;
	let arriving = true;

	$effect(() => {
		onpaging?.(paging.asPager(items.length, total, 'usernames'));
	});
	onDestroy(() => onpaging?.(null));

	/** One page of the pile, by where it starts or by the username it starts at. */
	async function pageOf(query: PageAsk) {
		const where =
			'from' in query ? { from: query.from, near: query.near } : { offset: query.offset };
		return await usernames.list({ unattached: true, limit: query.limit, ...where });
	}

	async function load() {
		failed = null;
		try {
			const page = await paging.fill(
				'',
				() => items,
				(query) => {
					// Only when a request goes out: a trim asks nothing.
					loading = true;
					return pageOf(query);
				},
				(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset })
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			items = page.rows;
			total = page.total;
			// In the same synchronous turn as the rows. See `CardPaging.land`.
			paging.land(page.offset);
			rememberAnchor(address.url, path, items[0]?.id, page.offset);
		} catch {
			failed = "Those couldn't be read.";
		} finally {
			loading = false;
		}
	}

	/* A page turned or the window resized; the address's anchor is honoured once. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		untrack(() => void load());
	});

	/* And when the library moves under it, as an Undo can bring a username back. */
	whenChanged(libraryChanges, () => void load());

	function facts(one: Username): string {
		// `filesSaid`, so a large count is grouped like every other.
		const files = filesSaid(one.asset_count);
		const where = one.site_name ? `${files} on ${one.site_name}` : files;
		// The question first; the file count is its context.
		return joinCounts(asking(one), where);
	}

	/*
	 * Several people answer to it (a judgement) or none (an offer to create one): different work.
	 */
	function asking(one: Username): string {
		return one.name_candidates > 1
			? `${counted(one.name_candidates)} people go by this`
			: 'Nobody goes by this yet';
	}

	/** The Site's shipped mark over the picture's foot, where the pack has one. */
	function markOf(one: Username): { name: string; src: string }[] {
		return one.site_id && one.site_name && one.site_icon
			? [
					{
						name: one.site_name,
						src: coverUrl(`/sites/${one.site_id}`, null, { icon: one.site_icon })
					}
				]
			: [];
	}
</script>

<!-- Inside the queue's own frame. See `EntityGrid.inside`. -->
<EntityGrid
	inside
	title="Usernames to assign"
	icon="person_add"
	empty="Nothing to assign. Usernames Sift can't match to one person appear here."
	drawn={items.length}
	{total}
	{loading}
	{failed}
	measure={paging.cards}
	page={paging.showing}
>
	{#each items as one (one.id)}
		<!-- The card a waiting username has wherever it is asked about. See `UsernameCard`. -->
		<UsernameCard {one} detail={facts(one)} sites={markOf(one)} onjoined={() => void load()} />
	{/each}
</EntityGrid>
