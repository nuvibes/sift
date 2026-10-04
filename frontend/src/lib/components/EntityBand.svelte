<script lang="ts">
	import { Button, Chip, SectionHeading } from '$lib/components/common';
	import Scroller from '$lib/components/common/Scroller.svelte';
	/*
	 * A plain word answers with the things it NAMES, not only with the files it appears in.
	 *
	 * Searching `reya` shows Reya Solberg under People, the sites and tags and collections called
	 * that, the folders named that, and then the files, which are the grid underneath. With only
	 * the files answering, a person whose name was in no filename would be unfindable by their
	 * own name.
	 *
	 * It sits in the slot the recently-viewed strip occupies on an unfiltered library, and it is
	 * shown only for a BARE word. A query carrying filters is a different question: those results
	 * are files, and a band of entities above them answers something nobody asked.
	 *
	 * The sections are the rail's own pages, in the rail's own order, because a section named
	 * anything the sidebar does not say is a second vocabulary for the same things. The order is a
	 * property of this band rather than of the sidebar: each account can rearrange its rail, and a
	 * band that reshuffled itself per account would be a different screen for every person.
	 */
	import { goto } from '$app/navigation';
	import { api } from '$lib/api/client';
	import Icon from '$lib/components/Icon.svelte';
	import Tile from '$lib/components/Tile.svelte';
	import { openAsset } from '$lib/player/asset-view';
	import { thumbUrl } from '$lib/entity/art';
	import type { GridItem } from '$lib/grid/grid.svelte';
	import type { IconName } from '$lib/design/icons';
	import { quoted } from '$lib/search/search.svelte';
	/* The one rule for which picture a thing is drawn by, and the one rule for where its page is.
	   Read here rather than built from `section.href` and the row's id, because those two lines
	   would be a second copy of an address that already exists, and the copy is the one that
	   spells `/photo-sets` as `/photo_sets` the day a kind is added. */
	import EntityPreview, { entityPicture } from '$lib/components/EntityPreview.svelte';
	import { pageOf, type EntityKind } from '$lib/entity/related.svelte';
	import type { components } from '$lib/api/schema';
	import { counted } from '$lib/entity/entity-counts';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';

	interface Props {
		/** The bare word being searched for. The band draws nothing without one. */
		word: string;
	}

	let { word }: Props = $props();

	/* `id` is the entity's own id, and it is what makes a picture possible: a cover is served at the
	   thing's own address. Null for a folder, which has no page and no picture.

	   The cover fields name WHICH cover that address answers with (the chosen file and its
	   moment, an upload, or a Site's shipped logo) and the account's token beside them. The
	   server copies them off the same scoped row the entity's wall reads, and they are handed to
	   `entityPicture` as they arrive: an address that names its cover is the only one the browser
	   may keep, so without them every chip in the band would be re-checked on every search. */
	type Row = Pick<
		components['schemas']['SuggestionOut'],
		| 'field'
		| 'value'
		| 'count'
		| 'id'
		| 'art'
		| 'cover_asset_id'
		| 'cover_upload_id'
		| 'cover_at_ms'
		| 'icon'
	>;

	/*
	 * The sections, in the rail's order, then Folders, then the files.
	 *
	 * Browse, Organize and Favorites are absent from the entity half deliberately: a section here
	 * is a thing a word can name, and none of those three is one. Favorites earns a group of its
	 * own further down, as files rather than an entity.
	 *
	 * Folders is last and has no page of its own: a folder is a filtered library view, so its "see
	 * all" is the grid filtered to it.
	 *
	 * `kind` is the entity vocabulary's name for the section's rows (`person` where the query
	 * language says `people`); both `entityPicture` and `pageOf` are keyed by it. Null for Folders,
	 * which have no id, page or cover. A tag with no cover wears `Avatar`'s tinted letter, as it
	 * does under the player; the letter is not offered as a photograph.
	 */
	const SECTIONS: {
		field: string;
		label: string;
		icon: IconName;
		href: string;
		kind: EntityKind | null;
	}[] = [
		{ field: 'people', label: 'People', icon: 'person', href: '/people', kind: 'person' },
		{
			field: 'sites',
			label: 'Sites',
			icon: 'public',
			href: '/sites',
			kind: 'site'
		},
		{
			field: 'collections',
			label: 'Collections',
			icon: 'box',
			href: '/collections',
			kind: 'collection'
		},
		{ field: 'tags', label: 'Tags', icon: 'shoppingmode', href: '/tags', kind: 'tag' },
		/* After Tags, as the rail lists the Music page: a word that is a song's name finds it. */
		{ field: 'songs', label: 'Music', icon: 'music_note_2', href: '/songs', kind: 'song' },
		{ field: 'in', label: 'Folders', icon: 'folder', href: '/browse', kind: null }
	];

	/*
	 * FOUR per section, in a 2x2 block. The rest are behind "see all".
	 *
	 * A row that wraps would make a section a different shape on every screen and the band's height
	 * a function of how many things happen to be called what was typed. Four in a
	 * fixed square is one shape, whatever a section holds, and the band above the wall stays the
	 * size somebody expects it to be.
	 */
	const PER_SECTION = 4;

	let named = $state<Row[]>([]);

	/* Rising counters, one EACH, so a slow answer for an old word cannot land on a new one: with
	 * one shared, whichever answered first would throw its own result away, at random. */
	let namedGeneration = 0;
	let heartedGeneration = 0;

	/* Both reads are asked again when what this session may see changes (Hidden opened or shut),
	   or the band would draw an answer taken under the old one above a wall drawn from the new. */
	let seeing = $state(0);
	whenChanged(libraryChanges, () => (seeing += 1));

	$effect(() => {
		const asked = word;
		void seeing;
		const mine = ++namedGeneration;
		void (async () => {
			try {
				const answer = await api.get<components['schemas']['Named']>('/search/named', {
					query: { q: asked }
				});
				if (mine !== namedGeneration) return;
				named = answer.items;
			} catch {
				// A band that could not be filled is no band. The files below are still the answer.
				if (mine !== namedGeneration) return;
				named = [];
			}
		})();
	});

	function rowsFor(field: string): Row[] {
		return named.filter((row) => row.field === field);
	}

	/*
	 * Where a chip goes: the thing's own page. A chip is a noun, and a noun goes to its page, as
	 * the same chip does under the player and as middle-click, the status bar and the back button
	 * expect. The filtered library is one press away from the wall on that page.
	 *
	 * A folder has no page, so it keeps the filtered view, which for a folder is the thing: `in:`
	 * is a filter and a folder is what it selects. `quoted` because a folder name may hold a space.
	 */
	function rowHref(section: (typeof SECTIONS)[number], row: Row): string {
		if (section.kind && row.id) return pageOf(section.kind, row.id);
		return `/browse?q=${encodeURIComponent(`${section.field}:${quoted(row.value)}`)}`;
	}

	/** Everything of this kind that matched, on the page that kind belongs to. */
	function seeAll(section: (typeof SECTIONS)[number]) {
		if (section.field === 'in') {
			// Folders have no wall of their own (a folder IS a filtered library view) so "see
			// all" is the grid asking about them rather than a page that would have to be invented.
			void goto(`/browse?q=${encodeURIComponent(word)}`);
			return;
		}
		void goto(`${section.href}?q=${encodeURIComponent(word)}`);
	}

	/*
	 * Favorites, as FILES rather than as an entity.
	 *
	 * A section here is normally a thing a word can name, and "favorite" is not one: it is a
	 * facet. This group is the other shape: the matching files that are also favorites, which is a
	 * shortcut into the answer rather than a claim that Favorites is a kind of thing.
	 *
	 * A file that matched and is hearted appears here AND in the grid below, and that is not a
	 * duplicate to be removed: taking it out of the grid to avoid showing it twice would make the
	 * main list quietly incomplete, which is worse than showing a shortcut.
	 *
	 * It is a strip of tiles and nothing else: no selecting, no action bar. There is one grid,
	 * and it is the one underneath.
	 */
	const FAVOURITES = 8;
	const STRIP_HEIGHT = 96;

	let hearted = $state<GridItem[]>([]);

	$effect(() => {
		const asked = word;
		void seeing;
		const mine = ++heartedGeneration;
		void (async () => {
			try {
				const answer = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
					query: { q: `${asked} fav:yes`, limit: FAVOURITES }
				});
				if (mine !== heartedGeneration) return;
				hearted = answer.items;
			} catch {
				if (mine !== heartedGeneration) return;
				hearted = [];
			}
		})();
	});

	/** A tile's width at the strip's fixed height, from the file's own proportions. */
	function widthOf(item: GridItem): number {
		if (!item.width || !item.height) return STRIP_HEIGHT;
		return Math.round((item.width / item.height) * STRIP_HEIGHT);
	}

	function allFavourites() {
		void goto(`/browse?q=${encodeURIComponent(`${word} fav:yes`)}`);
	}

	const anything = $derived(named.length > 0 || hearted.length > 0);
</script>

<!--
	The same chip the player's popout draws. A result is a thing: finding a person under People has
	found the person, so they are drawn as the same object everywhere, the chip with its cover rule.

	The count rides in `trail`, inside the pressable body: it is part of what the chip says, not a
	control. `aside` is where a control would go, and there is none here.

	A snippet because both branches above draw the same chip and only one wraps it in a card;
	written out twice, the two would be free to stop being the same object.
-->
{#snippet chip(section: (typeof SECTIONS)[number], row: Row)}
	<Chip
		tone="quiet"
		href={rowHref(section, row)}
		picture={section.kind && row.id
			? entityPicture(section.kind, row.id, row.value, row)
			: undefined}
	>
		{row.value}
		{#snippet trail()}
			{#if row.count !== null}<span class="count">{counted(row.count)}</span>{/if}
		{/snippet}
	</Chip>
{/snippet}

{#if anything}
	<!--
		THE BAND HAS A CEILING AND SCROLLS INSIDE IT.

		It sits above the wall of files, which is the answer somebody is actually looking at. A word
		matching four people, four sites, four tags and four folders would fill the window and
		push every file off the bottom of it. A third of the height at most, and what
		does not fit is scrolled to: the same shape the folder band above the wall already has.
	-->
	<div class="band-box">
		<Scroller>
			<section class="band" aria-label="Things called {word}">
				{#each SECTIONS as section (section.field)}
					{@const rows = rowsFor(section.field)}
					{#if rows.length > 0}
						<div class="group">
							<SectionHeading band level={2}>
								<span class="named"><Icon name={section.icon} size={16} />{section.label}</span>
							</SectionHeading>
							<ul>
								{#each rows.slice(0, PER_SECTION) as row (row.value)}
									<li>
										{#if section.kind && row.id}
											<!--
												And the same hover card. The chips under the popout
												player open a `LinkPreview` on a rest of the pointer
												(the cover, the name as a real link, one number per
												tab of that thing's page), and these are the same
												chips naming the same things.

												The trigger props go on a wrapper rather than the
												chip, as under the player: they are the library's
												listeners, and a component is not an element to hang
												them on. The wrapper is the whole chip.
											-->
											<EntityPreview kind={section.kind} id={row.id} name={row.value} cover={row}>
												{#snippet children({ props })}
													<span {...props} class="hovered">
														{@render chip(section, row)}
													</span>
												{/snippet}
											</EntityPreview>
										{:else}
											<!-- A FOLDER, and it has no card because it has no page to preview. It is a
											     filtered view of the library rather than a thing with a record of its
											     own, so there is no cover to draw and no strip of tabs to count. Its
											     chip is the same chip and goes to the filtered grid, which for a folder
											     IS the thing. -->
											{@render chip(section, row)}
										{/if}
									</li>
								{/each}
							</ul>
							{#if rows.length > PER_SECTION}
								<!-- The same quiet word-that-acts the filter bar's "Clear all" is. It is not a
								     thing to aim at beside four buttons; it is the way out of a section that
								     has more in it, and it should read as one. -->
								<Button tone="quiet" onclick={() => seeAll(section)}>See all</Button>
							{/if}
						</div>
					{/if}
				{/each}

				{#if hearted.length > 0}
					<div class="group">
						<SectionHeading band level={2}>
							<span class="named"><Icon name="favorite" size={16} />Favorites</span>
						</SectionHeading>
						<Scroller horizontal>
							<div class="strip">
								{#each hearted as item (item.id)}
									<Tile
										{item}
										width={widthOf(item)}
										height={STRIP_HEIGHT}
										thumbSrc={item.concealed || !item.thumb ? undefined : thumbUrl(item)}
										placeholder={item.concealed ? 'Hidden' : undefined}
										onopen={item.concealed ? undefined : openAsset}
									/>
								{/each}
							</div>
						</Scroller>
						<!-- Lands on the grid with a Favorites chip on the bar, so the screen says why those
						     files are the ones being shown. -->
						<Button tone="quiet" onclick={allFavourites}>See all</Button>
					</div>
				{/if}
			</section>
		</Scroller>
	</div>
{/if}

<style>
	.band {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-6);
		padding-inline: var(--space-6);
		padding-block-end: var(--space-4);
	}

	.group {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-width: 0;
	}

	/* The glyph beside the heading's words, on their line. */
	.named {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* A ceiling on the box that SCROLLS rather than on the band: a region with no height of its own
	   cannot be bounded, so a cap on the band alone would clip instead of scroll. Direct child, so
	   the horizontal strip of favourites inside keeps its own. */
	.band-box > :global(.scroll-root) {
		max-block-size: 33vh;
	}

	/* Two by two. A fixed square rather than a row that wraps, so a section is the same shape
	   whatever it holds. */
	ul {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/*
	 * A chip is as wide as the name in it. Left in a grid cell it would stretch to the column, so
	 * the cell holds it at its own width. The chip owns its height, ellipsis and face size, so
	 * there is no rule here for the name or the picture.
	 */
	li {
		justify-self: start;
		min-width: 0;
		max-width: 100%;
	}

	/* The hover target, and it is a flex box for the reason the identical wrapper under the player
	   is: an inline box inside a flex run falls back to an inline formatting context and rides a
	   line-height taller than the chip inside it, which reads as a chip of a different size. No
	   width cap here, unlike the player's: these cells are already bounded by the two-column grid
	   above, and a second ceiling would cut a name the grid had room for. */
	.hovered {
		display: flex;
		min-width: 0;
	}

	/* Scoped, like every count in the app: how many this account can actually reach. */
	.count {
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	.strip {
		display: flex;
		gap: var(--space-2);
		max-width: 100%;
	}
</style>
