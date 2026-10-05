<script lang="ts">
	/*
	 * WHY NOT BITS-UI: there is nothing here to reach for. These are marks, not controls: nothing
	 * is pressable, nothing takes focus, and each mark's tooltip is the library's already through
	 * the shared Tooltip.
	 *
	 * Who wrote to this, without a person doing it.
	 *
	 * On a person, a site or a tag that is a stash-box; a file has three answers (a stash-box
	 * recognised it, Sift read a face in it, Sift read the folder it was in). So it takes the
	 * server's own shape (`EnrichedBy`: a `via` and a `name`), and the glyph and words both come
	 * from the `enriched:` filter's own tables: a mark here cannot say something the filter would
	 * not find, and a new pass in that filter is a new mark here with nothing to change.
	 *
	 * It names the box. With several stash-boxes configured, which may disagree about who is in a
	 * scene, "a stash-box" alone sends somebody through Settings to find out which one; the tooltip
	 * gives the box's own name. A pass with no name to give uses the filter's words ("Sift: from a
	 * face", "Sift: from a folder name"), and a stash-box known only from a row carrying `source =
	 * 'stash_box'` says "a stash-box".
	 *
	 * Each stash-box Sift knows wears its own glyph (`boxIcon`), so a row of marks tells the
	 * services apart without hovering each. The glyphs are not the enrichment verbs': a state
	 * wearing a verb's glyph reads as an offer to run it.
	 *
	 * One component for the hover card, the entity's own header and the file's action row, which
	 * draw the same marks about the same question. It lives beside the header rather than in
	 * `common/`, because `common` claims a thing is a primitive, and this is one answer drawn in
	 * three places.
	 *
	 * The Created by line draws this too, through `said`: the paint, glyph and spelling of a box
	 * are the same for both readings, and only the words differ, so only the words are a prop.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		afterWords,
		boxIcon,
		boxRowLabel,
		madeIcon,
		madeLabel
	} from '$lib/components/shell/facet-labels';
	import type { components } from '$lib/api/schema';

	/**
	 * One author, as the server sends it, and for the Created by line the ACT that made the row
	 * where the pass was `produced` (the maker's `act`): a tag Sift put on a file it compressed
	 * wears the Compress glyph, one on a file it edited wears Modify's. The marks of what DESCRIBED
	 * a thing never carry one, so it is optional and absent means the pass's own glyph and words.
	 */
	type Source = components['schemas']['EnrichedBy'] & { act?: string | null };

	/**
	 * Which sentence these marks are in: a box DESCRIBED this thing, or INVENTED it. Only the words
	 * differ, and a word rather than the sentence, so a caller cannot invent a third reading.
	 */
	type Said = 'enriched' | 'created';

	interface Props {
		/**
		 * What wrote to this subject, in the order the server sent them.
		 *
		 * The wire shape, not a copy of it: a `via` says which pass and a `name` says which box,
		 * null where there is no box to name. The order is `fetched_at` descending, which is the
		 * order the subject's own record panel lists them in, so the marks and the panel read the
		 * same way down.
		 *
		 * ONE PER BOX, so the list is keyed on its position as well as its contents, never on `via`.
		 */
		sources: readonly Source[];
		/**
		 * What these marks are marks OF. `enriched` (a box has described this) is the default
		 * because it is what nearly every caller draws; the Created by line on an entity's header
		 * and on its hover card is the one that says otherwise.
		 */
		said?: Said;
		/**
		 * The marks stand in a row of controls (the heart and the stars on an entity's page), so
		 * each takes the box a control there takes: the same room either side as `Heart` and
		 * `RatingChip`, at their glyph size. The row then spaces every glyph alike, whether it can
		 * be pressed or not. Absent, the marks sit close together beside words, as they do beside a
		 * filename.
		 */
		padded?: boolean;
		/** On the page's own ground, where the accent's fill clears 3:1 in every theme. */
		onPage?: boolean;
	}

	let { sources, said = 'enriched', padded = false, onPage = false }: Props = $props();

	/*
	 * THE Enriched by COLUMN'S OWN ROW for this author, word for word.
	 *
	 * The box's NAME is this install's own spelling, not the slug the panel is keyed by, so the
	 * `<who>: <how>` shape is applied here through the panel's own function rather than looked up in
	 * its table. A pass with no box to name says the pass's own sentence, which is what that column
	 * reads: "Sift: from a face". A box known only from a row carrying `source = 'stash_box'` has no
	 * name either, and then it says what it is: "A stash-box".
	 */
	function row(one: Source): string {
		return one.name === null || one.name === undefined
			? madeLabel(one.via, one.act)
			: boxRowLabel(one.name);
	}

	/*
	 * THE TWO SENTENCES, written out rather than built from the word.
	 *
	 * "Created" is not "enrich" with a suffix rule, and a `${said}ed by` would be one: a table of
	 * two is the honest shape and is what makes a third reading a deliberate act rather than a
	 * spelling accident. The group's own label is the same words, so the row and every mark in it
	 * agree.
	 */
	const SAID: Record<Said, string> = {
		enriched: 'Enriched by',
		created: 'Created by'
	};

	/*
	 * What one mark says: the sentence's opening and then that row.
	 *
	 * The "Enriched by" prefix is on every tooltip: nothing announces a group to a pointer, and a
	 * hover shows one bubble over one glyph, which must read as a whole sentence. The rest is the
	 * panel's own row, so "Enriched by Stash-box: Example" here and "Stash-box: Example" in the
	 * panel are one spelling with a preposition in front.
	 */
	function words(one: Source): string {
		return `${SAID[said]} ${afterWords(row(one))}`;
	}
</script>

<!-- A group rather than a list: a screen reader announcing "list, 2 items" before two marks
     that each already say what they are is ceremony around nothing. -->
{#if sources.length > 0}
	<span class="marks" class:padded class:page={onPage} role="group" aria-label={SAID[said]}>
		{#each sources as one, at (`${at}:${one.via}:${one.name ?? ''}`)}
			{@const glyph = boxIcon(one.box) ?? madeIcon(one.via, one.act)}
			{#if glyph}
				<Tooltip label={words(one)} placement="bottom">
					<span class="mark" role="img" aria-label={words(one)}>
						<Icon name={glyph} size={padded ? 20 : 18} />
					</span>
				</Tooltip>
			{/if}
		{/each}
	</span>
{/if}

<style>
	.marks {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* No ground and no hover: read-only marks. The text tone on a raised ground, where the fill
	   misses 3:1. */
	.mark {
		display: inline-flex;
		align-items: center;
		color: var(--sift-accent-text);
	}

	.page .mark {
		color: var(--sift-accent);
	}

	/* A control's box, to the token: `Heart`'s and `RatingChip`'s padding. See `padded`. */
	.padded .mark {
		padding: var(--space-1) var(--space-2);
	}
</style>
