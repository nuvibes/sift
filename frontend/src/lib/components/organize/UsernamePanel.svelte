<script lang="ts">
	/*
	 * Usernames nobody has said who they belong to.
	 *
	 * Every downloaded file records the username that posted it, and a username is a name on a site
	 * rather than a person. Joining one to a person makes every file under it count under them and
	 * makes them findable by the spelling on the files, so the search box answers a username typed
	 * off a filename.
	 *
	 * A wall of cards rather than rows: the still under a username is usually the fastest way to
	 * recognise who it is.
	 *
	 * The question is answered on the card; a username is stored and is not a place, so it has no
	 * page. The picture and the name open the username's files (the Files wall filtered with
	 * `?username=`, exactly the set the server filed under this row), and "See the files" says so
	 * for anybody who does not try the picture.
	 *
	 * "Who is this?" opens the same picker "Add as person" opens on Faces to name: `PickMenu` over
	 * the whole library, each person drawn by their face, "Type to filter", one press to pick, and
	 * its create row making somebody new from what was typed. Whoever carries a name is on its page
	 * with their face, which is how two of the same name are told apart, and the create row is
	 * offered only where nobody on the page is called what was typed.
	 *
	 * The username is passed as the creator name rather than the site's: a download files what it
	 * fetched under a person named by the username, so that is the name a picture may already be
	 * fetched under, and otherwise a monogram of the username tells two apart. The site's mark
	 * would draw every username on one site identically, so the Site is the small mark over the
	 * picture's foot, where every card draws the Sites a thing is on.
	 */
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

	/*
	 * A page of whole rows of cards, with the pager every wall draws (`CardPaging`, the frame's
	 * `Pager`). Without page buttons a pile of more than one page would show the first page under a
	 * count that said more, and the rest could not be reached.
	 *
	 * Answering one is a re-read at the SAME place, so page 2 is still page 2 after a join, and a
	 * page the join emptied steps back to where the pile now ends (`CardPaging.fill`).
	 *
	 * And the page is kept in the ADDRESS, the way every other Organize list keeps it: the first card
	 * of the page on screen is written as `from` (with where it was, `near`), and an arrival asks for
	 * the page BY that username. Leaving for its files or a person and coming back (Back, or a
	 * crumb) opens this page again rather than the first. Answering the page's first card is what
	 * takes it off the queue, and the server then serves where the page was (`resume_at`).
	 */
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

	/* A page turned, or a window that now holds more rows or fewer. The anchor in the address is
	   honoured once, on arrival. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		untrack(() => void load());
	});

	/*
	 * And when the library moves under it, at the same place. A username comes back to this pile
	 * without anybody pressing anything here: an Undo in History takes a join back, and a join
	 * undone in another window puts one on the pile's far side. Both announce as a library change,
	 * the way every Organize list hears one; without this the card would stay gone until the page
	 * was reloaded, while the server was already listing it again.
	 */
	whenChanged(libraryChanges, () => void load());

	/*
	 * WHAT THIS ONE IS ASKING, which is not the same question on every card. See `asking`.
	 */
	function facts(one: Username): string {
		// "files", the word every wall counts in: an "item" is nothing anybody put in a library.
		// `filesSaid`, so a count in the thousands is grouped like every other.
		const files = filesSaid(one.asset_count);
		const where = one.site_name ? `${files} on ${one.site_name}` : files;
		// The QUESTION first and the facts after it. This wall is a queue: what somebody is deciding
		// leads, and how many files sit behind it is the context for that decision rather than the
		// point of the row.
		return joinCounts(asking(one), where);
	}

	/*
	 * WHAT THIS ONE IS ASKING, which is not the same question on every card.
	 *
	 * A waiting username is one of two things and they are different work. Sift files a download
	 * under somebody when EXACTLY ONE person answers to the username's spelling and refuses to guess
	 * past that, so **several** is that rule declining, and it is a judgement nobody else can
	 * make. **None** is not a judgement at all: it is an offer to make somebody, and it only piles
	 * up where making people from usernames has been switched off.
	 *
	 * Without this line the two are indistinguishable on the wall, so the pile reads as one
	 * undifferentiated chore. The count on the card deliberately does NOT split: a number answers
	 * "is this worth opening" and both kinds answer yes. This answers "what do I do with this one", which is
	 * the question that actually costs somebody time, and it is a fact about the row.
	 */
	function asking(one: Username): string {
		return one.name_candidates > 1
			? `${counted(one.name_candidates)} people go by this`
			: 'Nobody goes by this yet';
	}

	/** The Site's own mark over the foot of the picture: the shipped logo, where the pack has one
	 *  (`site_icon` is its token). A Site the pack does not cover wears no mark here, which is the
	 *  same answer its own card gives. Through `coverUrl` like every other Site picture; the row
	 *  carries no account token, so the address is answered the careful way rather than kept. */
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

<!-- Inside the queue's own frame: the route draws the title, the tabs and the pager. See
     `EntityGrid.inside`. -->
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
