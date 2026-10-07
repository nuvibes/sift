<script lang="ts" module>
	/*
	 * The picture one entity is drawn as, which lives in `$lib/entity/entity-picture` and is
	 * re-exported from here so that every call site reading it from this component reads one rule
	 * rather than a second copy of it.
	 *
	 * It lives there because a history row opens to groups of named things drawn as the same chip
	 * the band draws, and that row lives in `lib/components/common`, which this component imports
	 * its own primitives out of. Reaching back from there for a function in here would close a
	 * loop through `common/index.ts`, so a rule half the app shares cannot live in a module that
	 * draws.
	 */
	import { entityPicture, type CoverOf } from '$lib/entity/entity-picture';

	export { entityPicture, type CoverOf };
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the behaviour is the library's, one level down. `LinkPreview` is the
	   primitive (the hover delay, the safe travel into the card, the portal, the escape) and
	   what is written here is which thing it is about and what goes in the card. */
	/*
	 * What something is, while the pointer rests on it.
	 *
	 * A name with a picture beside it says almost nothing: it is the same cover whether the thing is
	 * on one file or four hundred. Opening its page to find that out costs whatever is on screen,
	 * which, on the file dialog this was built for, is the file somebody is in the middle of looking
	 * at.
	 *
	 * So: the cover, the name as a real link, and what it actually has, each number a way into that
	 * tab of its page. Nothing here is the only route to anything: every one of these links is on
	 * the page too, because a card that opens on hover is a card some people never see.
	 *
	 * ## Every entity is the same card
	 *
	 * A face is the thing somebody most wants named, but the argument does not stop at people: the
	 * file dialog's band draws a site, a collection and a photo set beside them, each a noun with a
	 * page of tabs, and a card on only some of the chips would say that some are worth knowing
	 * about and the rest are decoration. Every one of these kinds has a page, `tabsFor` knows its
	 * tabs, and `/related/<kind>/<id>` answers every one of their numbers. So the kind is a prop.
	 *
	 * ## The numbers come from the thing's own tab strip, not from the record
	 *
	 * `PersonView` carries `asset_count`, so the Files number could have been read straight off what
	 * the caller already holds. It is deliberately not: `/related/<kind>/<id>` answers EVERY tab's
	 * number from the same listings the walls draw, and mixing the two would put two populations on
	 * one card. One question, one answer, and the card agrees with the page it links to.
	 *
	 * Asked when the card OPENS and not before. Most chips are never hovered, and a request per chip
	 * on a file with eight people in it would be eight requests nobody asked for.
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
		/** What sort of thing this is. Decides the page, the tabs and the route the numbers come from. */
		kind: EntityKind;
		/** Which one. */
		id: string;
		name: string;
		/** The cover fields off the entity's own row, where the caller holds it. Names the cover on its
		 *  address so the picture may be kept. See `CoverOf`. Absent asks the bare address. */
		cover?: CoverOf;
		/** The picture and the name, as the caller already draws them, given the trigger's props. */
		children: import('svelte').Snippet<[{ props: Record<string, unknown> }]>;
	}

	let { kind, id, name, cover, children }: Props = $props();

	/* What a tab with no number says. The em dash the rest of the app draws for "nothing recorded":
	   a nought would be a claim, and a blank reads as a number that failed to arrive. */

	const href = $derived(pageOf(kind, id));
	/* The cover, through the address every wall draws this kind by. `Avatar` falls through to the
	   second address and then to the letter. See `entityPicture` above. */
	const picture = $derived(entityPicture(kind, id, name, cover));

	/** Null until the one request has come back. `{}` is a real answer: it failed, and the card
	 *  still draws its links with no numbers on them, which is what the tab strip does too. */
	let counts = $state<Partial<Record<RelatedKind, number>> | null>(null);

	/*
	 * Which stash-boxes know this thing, asked the same way its own page asks.
	 *
	 * NOT carried on the counts route, and that is the decision rather than an accident. That route
	 * answers ONE question: how many of each kind this entity's files reach, every number off the
	 * very listing its tab draws. And it says in its own header that there must never be a second
	 * counting path in it. Hanging a fact about provenance on it would make it two questions with
	 * one answer, and would put the related slice in the business of reading the stash-box tables,
	 * which it may not do without a seam that does not exist.
	 *
	 * `sourcesOf` is what the person's, the site's and the tag's own pages already call. Asking the
	 * same endpoint means the card and the page cannot disagree, which is the same rule the
	 * numbers above follow, applied to the other half of the card.
	 *
	 * Empty for the two kinds that have no links at all: a collection and a photo set are Sift's
	 * own arrangements and no stash-box has ever heard of them.
	 */
	let enrichedBy = $state<readonly { name: string; box: string | null }[]>([]);

	/*
	 * Who made it, asked for every kind, so the card agrees with the page it opens.
	 *
	 * `makerOf` is the one answer to that question: every one of the five kinds records who made
	 * it, and `MadeBy` carries the word, the pass and the box, so a person a download invented says
	 * so and one somebody typed says that instead. Null draws no line and means only that the row
	 * does not say. The `LINKABLE` guard below is about the stash-box marks alone.
	 */
	let madeBy = $state<Maker | null>(null);

	/** The kinds a stash-box can be asked about. The other two are never asked about links. */
	const LINKABLE: readonly EntityKind[] = ['person', 'site', 'tag'];

	/*
	 * Whether this person MAKES the edits: the mark that rides beside their name here, as it does
	 * on their own page and on their card.
	 *
	 * ASKED here rather than handed in, and that is the decision. A chip is drawn in a dozen places
	 * and most of them hold a name and an id and nothing else; a prop would mean the mark appearing
	 * on some chips and not others, for a reason nobody looking at the screen could work out. The
	 * mark either means something everywhere or it is noise.
	 *
	 * The cost is one more small request per person hovered, alongside the two this card already
	 * makes when it opens: after a 350ms rest, on a card most chips never show. A failure leaves
	 * it false, which draws nothing: a card without a badge is the ordinary card.
	 */
	let pmvCreator = $state(false);

	const tabs = $derived(tabsFor(kind, id, href, counts ?? {}));

	/* What the card is about, as one string. A derived only moves when its VALUE does, so a band
	   re-reading its rows (a new object for the same person, on every library change) does not
	   reach the effect below; reading `id` there directly would clear an answer that still holds. */
	const about = $derived(`${kind}:${id}`);

	/** Whether the card is on screen, so a change of subject under an open card asks again. */
	let showing = false;

	/* A different thing under the pointer is a different question, so whatever was answered for the
	   last one goes. Without this a wrapper reused for a second chip, which is what a keyed list
	   does when a row is removed, would show the first one's numbers under the second one's name.
	   An open card asks for the new one immediately: it would otherwise sit on bones until it was
	   closed and opened again. */
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

	/* An open card follows a change elsewhere: its counts and marks are asked again in place. */
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
					// Nothing. The badge is an addition to a card that is complete without it, and a
					// card that reported a failed request would be shouting about the wrong thing.
				});
		}
		/*
		 * WHO MADE IT, for every kind, out of the one function that answers that question.
		 *
		 * `makerOf` knows which address each kind's maker is recorded at (the stash-box answer for
		 * the three a box can name, the entity's own route for the other two) and it is asked here
		 * rather than the branch being spelt out a second time. A second spelling would leave this
		 * card silent about a collection while the collection's own page said who made it.
		 *
		 * The price, said plainly rather than buried: for a linkable kind this is a second read of
		 * `/stash-boxes/links/...`, the same address the marks below are read at. It is Sift's own
		 * table: no network, no key. It is paid only on a card somebody actually opened, and it
		 * buys the card having no copy of the per-kind branch to fall behind. Folding the two into
		 * one read means a `sourcesOf`-or-`makerOf` choice written out here, which is that copy.
		 */
		void makerOf(kind, id).then((held) => {
			if (wanted === id) madeBy = held;
		});
		if (!LINKABLE.includes(kind)) return;
		void sourcesOf(kind as LinkSubject, id).then((held) => {
			if (wanted !== id) return;
			// The name AND the box's own word: the name is what the mark says and the word is
			// what it is painted in. See `EnrichmentMarks`.
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
			<!-- The name and its marks on one line at the top of the picture rather than centred
			     against the circle, where the name would float in the middle of a 48-pixel avatar. The
			     marks go beside the name because they are about the name. -->
			<div class="title">
				<div class="said">
					<a class="name" {href}>{name}</a>
					<!-- The PMV-creator mark, in the same place it sits on their own page: immediately
					     right of the name, before the marks that say who ELSE has described them. It is
					     about the person; those are about the record. -->
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
					<!-- WHO MADE IT, directly under the title and not among its marks, as the entity's
					     own header has it (this card is the small copy of that). A mark says a box has
					     DESCRIBED this thing and this says a box INVENTED it, and the two cannot share a
					     row without one being read as the other. Beside the picture, under the name,
					     so it reads as part of what the title says rather than as a row of the card.

					     Drawn by `EnrichmentMarks` with `said="created"`, so the box wears one colour on the
					     card and on the page behind it and the tooltip carries the sentence the words carry.
					     A copy of the colouring here is a copy free to drift. -->
					<p class="made-by">
						{#if madeBy.box_name}
							<EnrichmentMarks
								said="created"
								sources={[{ via: 'stash', name: madeBy.box_name, box: madeBy.box_slug }]}
							/>
						{:else if madeBy.via && madeByGlyph(madeBy)}
							<!-- The pass's own accent mark, which is what `EnrichmentMarks` draws for a source
							     with no box to colour it: the same glyph out of the same table. A maker that
							     is a PERSON gets no glyph at all: there is nothing to say beyond the words. -->
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
			<!-- The numbers on their way. Bones rather than a row of blanks that fill in, so the card
			     does not change height under the pointer the moment the request lands. -->
			<div class="bones"><Skeleton lines={2} /></div>
		{:else}
			<!-- One pair per tab of its page, each a way into that tab. The set, the words, the glyphs
			     and the addresses are all `tabsFor`'s: a list hand-kept here would be a second answer
			     to what a thing HAS, free to fall behind the page it links to. -->
			<!-- The same row the entity cards wear (`EntityCounts`), so the hover card and the wall
			     cannot draw one number two ways. -->
			<!-- Glyphs and figures, no words: each kind is its tab's glyph and its tooltip says the
			     tab's word, exactly as on the wall's cards, so the card is not a second list of the
			     entity kinds' names. A nought draws nothing: a card of figures where some say 0 is a
			     card read for the ones that matter, and the wall's cards follow the same rule. -->
			<div class="counts-gap">
				<EntityCounts {name} cells={tabs.filter((cell) => cell.count !== 0)} />
			</div>
		{/if}
	{/snippet}
</LinkPreview>

<style>
	/* The room above the count row; the row's own look is `EntityCounts`'s. */
	.counts-gap {
		padding-block-start: var(--space-3);
	}

	/* The picture and the name, on one line. The picture keeps its own size here rather than taking
	   the card's width: `Avatar` has no size of its own and fills whatever clips it. */
	.who {
		display: flex;
		align-items: start;
		gap: var(--space-3);
	}

	/* The name and its marks share a line and the name is the part that gives: a long one is cut
	   with an ellipsis and the marks stay, because a mark that wrapped onto its own line would read
	   as a row about something else. `min-inline-size: 0` is what lets the cut happen at all: a
	   flex item's floor is its content, so without it the name pushes the marks out of the card. */
	/* The title's column beside the picture: the name and its marks, then who made it. */
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
		/* No ground of its own: `Avatar` brings one, and a second here is a box drawn by hand
		   around a component that already draws itself. */
		overflow: hidden;
	}

	/*
	 * The mark beside the name, in the ink it inherits (the header's rule says why it has no
	 * colour). `inline-flex` so a square glyph sits on the line box rather than on the text
	 * baseline beside a capital; the same rule the header draws it by.
	 */
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

	/* A link with no ground of its own, so the hover underlines rather than only changing the ink.
	   An ink-only hover is for things nothing else can say. */
	.name:hover {
		color: var(--sift-accent);
		text-decoration: underline;
	}

	/* The line that says who made it, quieter than the name above it and drawn exactly as the
	   entity's own header draws it: same gap, same ink, same size. Two spellings of one line is
	   how a card comes to disagree with the page it opens. */
	.made-by {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		padding-block-start: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The bones stand where the numbers will, so the card does not resize when they land. */
	.bones {
		padding-block-start: var(--space-3);
	}

	/* A wrapping row rather than a fixed set of columns: how many tabs a thing has is the tab
	   strip's business, and a grid written here would have to be rewritten when it changes. */

	/* Each pair is a link, so it wears a ground that steps on hover: the Light register
	   for a control that has one. */
</style>
