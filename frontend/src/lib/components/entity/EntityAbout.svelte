<script lang="ts">
	/*
	 * The column beside an entity's cover: the summary under the name, the counts and the O tally,
	 * the heart, the stars and the stash-box marks, who made it, and the tags with their picker.
	 * Held to the cover's height, so every entity's header is one shape; what does not fit scrolls.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		Chip,
		Heart,
		MenuButton,
		PickMenu,
		RatingChip,
		Scroller,
		TagChip
	} from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EnrichmentMarks from '$lib/components/entity/EnrichmentMarks.svelte';
	import { madeByGlyph, madeBySaid, type Maker } from '$lib/entity/enrich.svelte';
	import { tags as tagStore } from '$lib/entity/tags.svelte';
	import { PICK_PAGE } from '$lib/search/frequent.svelte';
	import type { PickChoice, PickPage } from '$lib/components/common/verbs';
	import { pickRow } from '$lib/entity/entity-picture';
	import type { components } from '$lib/api/schema';

	interface Props {
		name: string;
		/** The facts under the name; see `EntityHeader.summary`. */
		summary?: Snippet;
		counts: string;
		oCount: number | null;
		favorite: boolean;
		rating: number | null;
		onfavorite?: (favorite: boolean) => void;
		onrate?: (rating: number | null) => void;
		enrichedBy: readonly { name: string; box: string | null }[];
		madeBy: Maker | null;
		tags: components['schemas']['TagOnEntity'][];
		onuntag?: (tagId: string) => void;
		ontag?: (tag: PickChoice) => void | Promise<void>;
		/** While the record form is up the tags are chips only: the form edits them into its draft. */
		editing: boolean;
	}

	let {
		name,
		summary,
		counts,
		oCount,
		favorite,
		rating,
		onfavorite,
		onrate,
		enrichedBy,
		madeBy,
		tags,
		onuntag,
		ontag,
		editing
	}: Props = $props();

	/* The O tally, whole and never below nought; nought draws nothing. */
	const tallied = $derived(
		typeof oCount === 'number' && Number.isFinite(oCount) ? Math.max(0, Math.round(oCount)) : 0
	);

	/*
	 * One page of tags, filtered by what is being typed, less the ones already on this thing. The
	 * count is corrected with the removal: `more` is the server's answer about the whole list.
	 */
	async function askTags(typed: string): Promise<PickPage> {
		const asked = await tagStore.choices(typed, PICK_PAGE);
		const on = new Set(tags.map((chip) => chip.id));
		const rows = asked.items.filter((tag) => !on.has(tag.id));
		const dropped = asked.items.length - rows.length;
		return {
			choices: rows.map((tag) => pickRow('tag', tag)),
			more: Math.max(0, asked.total - asked.items.length + dropped)
		};
	}

	/** Make one under the name that was typed. The store owns the write and the list it keeps. */
	async function makeTag(named: string): Promise<PickChoice> {
		const made = await tagStore.create(named);
		return { id: made.id, name: made.name };
	}
</script>

<div class="about">
	<!-- The layout goes on the box inside the scroller, which the library renders unscoped. -->
	<Scroller>
		<div class="about-inner">
			<!-- Directly under the name, always: what somebody is called and where they can be found. -->
			{@render summary?.()}

			{#if counts || tallied > 0}
				<p class="counts">
					{counts}
					{#if tallied > 0}
						<!-- The player's O counter, in its words and its glyph. -->
						<Tooltip label="O counter: {tallied}">
							<span class="o-tally">
								<Icon name="water_drop" size={16} filled />
								<span>{tallied}</span>
							</span>
						</Tooltip>
					{/if}
				</p>
			{/if}

			{#if onfavorite || onrate || enrichedBy.length > 0}
				<div class="opinions">
					{#if onfavorite || onrate}
						<!-- Both sizes written out, so the star cannot drift from the heart. -->
						<Heart {favorite} onchange={(next) => onfavorite?.(next)} size={20} />
						<RatingChip {rating} onchange={(next) => onrate?.(next)} size={20} label={name} />
					{/if}
					<!-- An entity is only ever enriched by a stash-box, so `via` is supplied here. -->
					<EnrichmentMarks
						padded
						onPage
						sources={enrichedBy.map((one) => ({
							via: 'stash',
							name: one.name,
							box: one.box
						}))}
					/>
				</div>
			{/if}

			{#if madeBy}
				<!-- Who made it, on its own line: a mark says a box described this thing, and this says
				     a box invented it. `said` gives the mark the "Created by" sentence. -->
				<p class="made-by">
					{#if madeBy.box_name}
						<EnrichmentMarks
							onPage
							said="created"
							sources={[{ via: 'stash', name: madeBy.box_name, box: madeBy.box_slug }]}
						/>
					{:else if madeBy.via && madeByGlyph(madeBy)}
						<!-- A pass of Sift's own keeps the accent; a person gets no glyph, only words. -->
						<EnrichmentMarks
							onPage
							said="created"
							sources={[{ via: madeBy.via, act: madeBy.act, name: null, box: null }]}
						/>
					{/if}
					<span>{madeBySaid(madeBy)}</span>
				</p>
			{/if}

			{#if ontag || tags.length > 0}
				<!-- The tags, and the one picker. Chips only while the form is up: the form edits tags
				     into its draft and this writes on a press, and two live editors for one field would
				     save half the changes immediately and half on Save. -->
				<div class="tags">
					{#each tags as chip (chip.id)}
						<TagChip
							name={chip.name}
							onremove={onuntag && !editing ? () => void onuntag?.(chip.id) : undefined}
						/>
					{/each}

					{#if ontag && !editing}
						<!-- A dashed chip at the end of the row reads as the place the next tag goes.
						     `scrolls={false}`: the picker scrolls itself. -->
						<MenuButton label="Add a tag to {name}" scrolls={false}>
							{#snippet trigger({ props })}
								<Chip tone="outline" icon="add" trigger={props}>Add tag</Chip>
							{/snippet}
							<PickMenu
								label="Tag"
								icon="shoppingmode"
								kind="tag"
								plural="tags"
								inline
								ask={askTags}
								onpick={(choice) => void ontag?.(choice)}
								oncreate={makeTag}
							/>
						</MenuButton>
					{/if}
				</div>
			{/if}
		</div>
	</Scroller>
</div>

<style>
	.about {
		align-self: start;
		grid-area: about;
		min-width: 0;
		/* The cover's height and never more (a 3:4 portrait of the token's width), so many aliases do
		   not push the tabs down. A grid of one shrinkable row, so the cap is a box the scroller
		   fits; `check_capped_scroller.js` refuses a capped flex column around a Scroller. */
		max-block-size: calc(var(--entity-cover-width) * 4 / 3);
		min-block-size: 0;
		display: grid;
		grid-template-rows: minmax(0, 1fr);
	}

	.about-inner {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-width: 0;
	}

	.counts {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.o-tally {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* A quiet fact in the counts' ink; the mark inside keeps the box's own colour. */
	.made-by {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* One step between boxes, the marks `padded` to match, so heart, stars and marks are evenly
	   spaced. */
	.opinions {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* A finger apart on a phone, so the reaches meet at the touch target. */
	@media (max-width: 767px) {
		.opinions {
			gap: var(--space-2);
		}
	}

	.tags {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/* On a phone the row is sized to its content, so the scroller never scrolls inside a page that
	   already does. */
	@media (max-width: 767px) {
		.about {
			max-block-size: none;
		}
	}
</style>
