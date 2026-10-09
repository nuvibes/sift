<script lang="ts" module>
	/*
	 * The picture rule lives in `$lib/entity/entity-picture`, re-exported here, to avoid an import
	 * cycle through `common`.
	 */
	import { entityPicture, type CoverOf } from '$lib/entity/entity-picture';

	export { entityPicture, type CoverOf };
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the behaviour is the library's, one level down. `LinkPreview` is the
	primitive (the hover delay, the safe travel into the card, the portal, the escape) and
	what is written here is which thing it is about and what goes in the card. */
	/*
	 * What an entity is, while the pointer rests on it: the cover, the name as a link, and its
	 * counts, each a way into its page's tab, from `/related/<kind>/<id>` like the page's own.
	 * Asked when the card OPENS. Every link here is on the page too.
	 */
	import { untrack } from 'svelte';
	import { LinkPreview, Avatar, Skeleton, Tooltip } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import EntityCounts from '$lib/components/entity/EntityCounts.svelte';
	import EnrichmentMarks from '$lib/components/entity/EnrichmentMarks.svelte';
	import { api } from '$lib/api/client';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import {
		madeByGlyph,
		madeBySaid,
		makerOf,
		sourcesOf,
		type LinkSubject,
		type Maker
	} from '$lib/entity/enrich.svelte';
	import type { components } from '$lib/api/schema';
	import {
		loadCounts,
		pageOf,
		tabsFor,
		type EntityKind,
		type RelatedKind
	} from '$lib/entity/related.svelte';

	interface Props {
		kind: EntityKind;
		id: string;
		name: string;
		/** Names the cover on its address so the picture may be kept (`CoverOf`). */
		cover?: CoverOf;
		children: import('svelte').Snippet<[{ props: Record<string, unknown> }]>;
	}

	let { kind, id, name, cover, children }: Props = $props();

	const href = $derived(pageOf(kind, id));
	const picture = $derived(entityPicture(kind, id, name, cover));

	/** `{}` is a real answer: it failed, and the links draw with no numbers. */
	let counts = $state<Partial<Record<RelatedKind, number>> | null>(null);

	/*
	 * Which stash-boxes know this thing, from `sourcesOf` as its page asks; never on the counts
	 * route.
	 */
	let enrichedBy = $state<readonly { name: string; box: string | null }[]>([]);

	/* Who made it, from `makerOf`, as the page; null means the row does not say. */
	let madeBy = $state<Maker | null>(null);

	const LINKABLE: readonly EntityKind[] = ['person', 'site', 'tag'];

	/* Asked here rather than handed in, so the mark means the same on every chip. */
	let pmvCreator = $state(false);

	const tabs = $derived(tabsFor(kind, id, href, counts ?? {}));

	/* One string: a band re-reading its rows makes a new object for the same person. */
	const about = $derived(`${kind}:${id}`);

	let showing = false;

	/* A different thing under the pointer drops the last answer, and an open card asks again. */
	$effect(() => {
		void about;
		untrack(() => {
			counts = null;
			enrichedBy = [];
			madeBy = null;
			pmvCreator = false;
			if (showing) ask();
		});
	});

	reloadOnLibraryChange(() => {
		if (showing) ask();
	});

	function opened(open: boolean): void {
		showing = open;
		if (open && counts === null) ask();
	}

	function ask(): void {
		const wanted = id;
		void loadCounts(kind, id).then((found) => {
			if (wanted === id) counts = found;
		});
		if (kind === 'person') {
			void api
				.get<components['schemas']['PersonView']>(`/people/${id}`)
				.then((person) => {
					if (wanted === id) pmvCreator = person.pmv_creator;
				})
				.catch(() => {
					// A card is complete without the badge.
				});
		}
		/*
		 * `makerOf` holds the per-kind branch; for a linkable kind it reads the links address a
		 * second time.
		 */
		void makerOf(kind, id).then((held) => {
			if (wanted === id) madeBy = held;
		});
		if (!LINKABLE.includes(kind)) return;
		void sourcesOf(kind as LinkSubject, id).then((held) => {
			if (wanted !== id) return;
			// The name, and the box's word that paints it (`EnrichmentMarks`).
			enrichedBy = held.links.map((one) => ({ name: one.box_name, box: one.box_slug }));
		});
	}
</script>

<LinkPreview onOpenChange={opened}>
	{#snippet trigger({ props })}
		{@render children({ props })}
	{/snippet}

	{#snippet card()}
		<div class="who">
			<a class="face" {href} aria-label={name}>
				<Avatar
					src={picture.src}
					instead={picture.instead}
					{name}
					glyph={picture.glyph}
					shape="face"
					decorative
				/>
			</a>
			<div class="title">
				<div class="said">
					<a class="name" {href}>{name}</a>
					<!-- The PMV-creator mark, where it sits on their own page. -->
					{#if pmvCreator}
						<Tooltip label="PMV creator" placement="top">
							<span class="verified"
								><Icon name="cinematic_blur" size={16} label="PMV creator" /></span
							>
						</Tooltip>
					{/if}
					<EnrichmentMarks
						sources={enrichedBy.map((one) => ({ via: 'stash', name: one.name, box: one.box }))}
					/>
				</div>
				{#if madeBy}
					<!--
					WHO MADE IT, under the title, apart from the marks: invented is not described.
					-->
					<p class="made-by">
						{#if madeBy.box_name}
							<EnrichmentMarks
								said="created"
								sources={[{ via: 'stash', name: madeBy.box_name, box: madeBy.box_slug }]}
							/>
						{:else if madeBy.via && madeByGlyph(madeBy)}
							<EnrichmentMarks
								said="created"
								sources={[{ via: madeBy.via, act: madeBy.act, name: null, box: null }]}
							/>
						{/if}
						<span>{madeBySaid(madeBy)}</span>
					</p>
				{/if}
			</div>
		</div>

		{#if counts === null}
			<!-- Bones, so the card does not change height when the numbers land. -->
			<div class="bones"><Skeleton lines={2} /></div>
		{:else}
			<!-- One pair per tab, `tabsFor`'s, in the cards' `EntityCounts` row. -->
			<div class="counts-gap">
				<EntityCounts {name} cells={tabs.filter((cell) => cell.count !== 0)} />
			</div>
		{/if}
	{/snippet}
</LinkPreview>

<style>
	.counts-gap {
		padding-block-start: var(--space-3);
	}

	.who {
		display: flex;
		align-items: start;
		gap: var(--space-3);
	}

	/* The name is cut with an ellipsis and the marks stay (`min-inline-size: 0`). */
	.title {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.said {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.face {
		display: block;
		flex: none;
		inline-size: var(--space-12);
		block-size: var(--space-12);
		border-radius: 50%;
		overflow: hidden;
	}

	.verified {
		display: inline-flex;
		align-items: center;
	}

	.name {
		font: var(--text-h2);
		color: var(--sift-ink);
		text-decoration: none;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		transition: color var(--dur-instant) var(--ease);
	}

	.name:hover {
		color: var(--sift-accent);
		text-decoration: underline;
	}

	/* As the entity's own header draws it. */
	.made-by {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		padding-block-start: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.bones {
		padding-block-start: var(--space-3);
	}
</style>
