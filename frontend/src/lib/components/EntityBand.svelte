<script lang="ts">
	import { Button, Chip, SectionHeading } from '$lib/components/common';
	import Scroller from '$lib/components/common/Scroller.svelte';
	/*
	 * A plain word answers with the things it NAMES, above the files, for a BARE word only, in the
	 * rail's sections and fixed order.
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
	/* The one rule for a thing's picture and page, never an address built here. */
	import EntityPreview, { entityPicture } from '$lib/components/EntityPreview.svelte';
	import { pageOf, type EntityKind } from '$lib/entity/related.svelte';
	import type { components } from '$lib/api/schema';
	import { counted } from '$lib/entity/entity-counts';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';

	interface Props {
		word: string;
	}

	let { word }: Props = $props();

	/*
	 * `id` is what a cover is served at; the cover fields name which one, so the browser may keep
	 * it.
	 */
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
	 * The rail's sections, then Folders (a filtered view, no page); `kind` keys `entityPicture` and
	 * `pageOf`.
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
		{ field: 'songs', label: 'Music', icon: 'music_note_2', href: '/songs', kind: 'song' },
		{ field: 'in', label: 'Folders', icon: 'folder', href: '/browse', kind: null }
	];

	/* FOUR a section, in a fixed 2x2, so the band is one shape. */
	const PER_SECTION = 4;

	let named = $state<Row[]>([]);

	/* One counter each, so a slow answer cannot land on a new word. */
	let namedGeneration = 0;
	let heartedGeneration = 0;

	/* Asked again when Hidden opens or shuts. */
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
				// No band; the files below are still the answer.
				if (mine !== namedGeneration) return;
				named = [];
			}
		})();
	});

	function rowsFor(field: string): Row[] {
		return named.filter((row) => row.field === field);
	}

	/* A chip goes to the thing's page; a folder keeps the filtered view. */
	function rowHref(section: (typeof SECTIONS)[number], row: Row): string {
		if (section.kind && row.id) return pageOf(section.kind, row.id);
		return `/browse?q=${encodeURIComponent(`${section.field}:${quoted(row.value)}`)}`;
	}

	function seeAll(section: (typeof SECTIONS)[number]) {
		if (section.field === 'in') {
			void goto(`/browse?q=${encodeURIComponent(word)}`);
			return;
		}
		void goto(`${section.href}?q=${encodeURIComponent(word)}`);
	}

	/*
	 * Favorites, as FILES matching the word, a shortcut into the answer; the grid below keeps them
	 * too.
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

	function widthOf(item: GridItem): number {
		if (!item.width || !item.height) return STRIP_HEIGHT;
		return Math.round((item.width / item.height) * STRIP_HEIGHT);
	}

	function allFavourites() {
		void goto(`/browse?q=${encodeURIComponent(`${word} fav:yes`)}`);
	}

	const anything = $derived(named.length > 0 || hearted.length > 0);
</script>

<!-- The popout's chip; the count rides in `trail`. -->
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
	<!-- A CEILING, a third of the height, scrolling inside it, so the files are not pushed off. -->
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
											The same hover card as under the player, on a wrapper.
											-->
											<EntityPreview kind={section.kind} id={row.id} name={row.value} cover={row}>
												{#snippet children({ props })}
													<span {...props} class="hovered">
														{@render chip(section, row)}
													</span>
												{/snippet}
											</EntityPreview>
										{:else}
											<!-- A folder has no page, so no card. -->
											{@render chip(section, row)}
										{/if}
									</li>
								{/each}
							</ul>
							{#if rows.length > PER_SECTION}
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

	.named {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* The cap is on the box that SCROLLS. */
	.band-box > :global(.scroll-root) {
		max-block-size: 33vh;
	}

	ul {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* A chip as wide as its name. */
	li {
		justify-self: start;
		min-width: 0;
		max-width: 100%;
	}

	/* A flex box, or an inline box rides a taller line. */
	.hovered {
		display: flex;
		min-width: 0;
	}

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
