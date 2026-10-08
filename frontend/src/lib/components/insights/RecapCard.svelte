<script lang="ts" module>
	import type { RecapCard as Card } from '$lib/library/recaps.svelte';

	/** Whether this card may be saved as a picture: never a locked tile, never one that names
	 *  something hidden, and only a card with something to say. */
	export function savable(card: Card): boolean {
		return !card.hidden && card.hidden_things.length === 0 && card.statement.length > 0;
	}
</script>

<script lang="ts">
	/*
	 * ONE CARD OF A RECAP, stood up as a story card: the recap's name and where this card falls in
	 * it at the head, then the picture, the ranked list or the ring the card carries, then its one
	 * large figure and its one sentence. Nothing it was not handed.
	 *
	 * ## One drawing, three sizes
	 *
	 * `story` is the deck's card: as wide as its holder, 9:16. `hero` is the same card filling the
	 * window's height. Both count their type and room in the card's own width (`--story-*` in
	 * `app.css`), so the card is one drawing at any width and the saved picture is this card
	 * painted again at 1080 wide (`share-card.ts`). `micro` is the figure, its label and the
	 * sentence on a panel, for a grid. The card's content fits the card: the list stops at five,
	 * the picture and the ring take the room the words leave, and a long sentence is set a step
	 * smaller.
	 *
	 * The figure is the glance ("41 h") and the sentence is the reading of it, so the figure is set
	 * in the figure size and the sentence under it in display type: one statement a card, the way a
	 * year in review is read a card at a time. The figure is a `FigureCard`, the one drawing of a
	 * figure that counts up, so it rises into place as its card arrives. It is worded by the server
	 * (`said`); the sentence arrives as PIECES and `HistorySentence` draws them, so a person or a
	 * Site a card names is a way to them, and nothing here builds a word. The top person's card
	 * carries her portrait; the top five are a ranked list led by the first; the favourite time is
	 * the day's twenty-four hours as a ring.
	 *
	 * The ground is the accent's run, deepest at the foot (`--sift-accent-shade-1` to `-2`), with
	 * the figure in its tint: the accent's own colour at more than one step, never another hue, and
	 * the picture the only other colour on the card.
	 *
	 * ## A locked tile
	 *
	 * `hidden` is a card that is there and says nothing: the vault is locked, the placeholder mode is
	 * on, and what the card would say names something hidden. It is drawn as the tile a hidden file
	 * is drawn as on every wall (the ground blurred, the one mark sharp on top) and it draws NONE
	 * of what it was handed, even if a statement came with it: the server sends it empty, and this is
	 * the second lock on the same door. Which cards are absent, which are tiles and which are drawn is
	 * the server's call and never this file's (in the Show nothing mode a hidden card simply is not
	 * in the answer).
	 *
	 * A card that names something hidden while the vault is OPEN carries its ids in `hidden_things`,
	 * and wears the mark a hidden thing wears everywhere else, so nobody is surprised when the card
	 * is gone after the vault shuts.
	 *
	 * ## A picture of the card
	 *
	 * A recap is read a card at a time, and a card is made to be kept, as a year in review's card
	 * is. A picture of one is still not a way to carry the library out of the window: it is taken
	 * by the person pressing for it, of a card on their own screen, through the one door every
	 * screenshot in Sift takes (`$lib/player/snapshot`'s `deliver`: the clipboard, their library or
	 * a download, as their setting says); never of a locked tile, and never of a card that names
	 * something hidden (`savable`). The page offers the press; this component only says whether
	 * the card may be saved.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import FigureCard from '$lib/components/charts/FigureCard.svelte';
	import HourRing from '$lib/components/charts/HourRing.svelte';
	import RankedList from '$lib/components/charts/RankedList.svelte';
	import { Avatar, HistorySentence } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { figureWords, saidOf, timeWords, wordsOf } from '$lib/components/insights/figures';
	import { KIND_WORDS } from '$lib/components/insights/words';
	import type { RecapCard } from '$lib/library/recaps.svelte';

	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		card: RecapCard;
		/** The recap's name, at the head of the card: "Your week". */
		heading?: string;
		/** Where this card falls in the recap, at the head: "3 of 11". */
		place?: string;
		/** The span the recap covers, at the foot, so a card kept as a picture says which week. */
		foot?: string;
		size?: 'micro' | 'story' | 'hero';
	}

	let { card, heading = '', place = '', foot = '', size = 'story' }: Props = $props();

	/* Past this many letters a sentence is set a step smaller, so it fits beside a picture. */
	const LONG = 90;
	const long = $derived(card.statement.reduce((sum, piece) => sum + piece.text.length, 0) > LONG);
	/* A Site's picture is its mark, drawn whole on its own ground (the top Site, or a card whose
	   sentence names a Site, as a rediscovered one); a song's art is square. */
	const mark = $derived(
		card.kind === 'top_site' || card.statement.find((piece) => piece.kind !== null)?.kind === 'site'
	);
	const square = $derived(card.kind === 'top_song');

	const rows = $derived(
		(card.rows ?? []).map((row) => ({ ...row, said: saidOf(row.said, row.value, row.unit) }))
	);
	const hours = $derived(
		card.chart ? card.chart.bars.map((bar) => bar.parts.reduce((sum, p) => sum + p.value, 0)) : []
	);
	const hourWords = $derived(
		wordsOf(
			(card.chart?.bars ?? []).map((bar) => ({
				value: bar.parts.reduce((sum, p) => sum + p.value, 0),
				said: bar.said
			})),
			'ms'
		)
	);
	/* Whose picture the cover is: the thing the sentence names, for the letter drawn when it has none. */
	const subject = $derived(
		card.statement.find((piece) => piece.kind !== null)?.text ?? card.figure?.label ?? ''
	);
	/* The favourite hour, for the middle of the ring: the one with the most, the earliest on a tie. */
	const favourite = $derived(hours.indexOf(Math.max(...hours)));
</script>

{#snippet name(row: NamedRow)}
	<HistorySentence pieces={[row.piece]} />
{/snippet}

{#snippet portrait(row: NamedRow)}
	{#if row.cover !== null}
		<Avatar src={row.cover} name={row.piece.text} decorative lazy />
	{/if}
{/snippet}

{#snippet hour()}
	<span class="favourite">{timeWords(favourite * 60)}</span>
{/snippet}

<article
	class="recap-card {size}"
	class:hidden-card={card.hidden}
	data-card={card.kind}
	data-savable={savable(card)}
>
	<div class="inside">
		{#if size !== 'micro' && (heading || place)}
			<header class="head">
				<span>{heading}</span>
				<span class="place">{place}</span>
			</header>
		{/if}
		{#if card.hidden}
			<!-- The glyph is a child of the blurred layer's box, so it is painted on top and stays sharp.
			     The same size the wall's locked tile uses: here too the mark IS the picture. -->
			<div class="locked">
				<Icon name="visibility_off" size={34} filled label="Hidden" />
			</div>
		{:else}
			<div
				class="card"
				class:pictured={size !== 'micro' && (card.cover !== null || hours.length > 0)}
				class:ringed={size !== 'micro' && hours.length > 0}
			>
				<!-- A micro card is the figure and its sentence: the picture, list and ring are the story's. -->
				{#if size === 'micro'}{:else if hours.length > 0}
					<div class="picture">
						<div class="fit square">
							<HourRing {hours} format={hourWords} label={KIND_WORDS.all} middle={hour} />
						</div>
					</div>
				{:else if card.cover}
					<!-- Decorative: the sentence names what it is a picture of. Through `Avatar`, so a
					     thing with no picture draws its letter rather than a broken-picture mark. -->
					<div class="picture cover" class:square>
						<div class="fit framed" class:square>
							<Avatar
								src={card.cover}
								name={subject}
								shape={square ? 'face' : 'portrait'}
								{mark}
								decorative
							/>
						</div>
					</div>
				{/if}
				{#if size !== 'micro' && rows.length > 0}
					<RankedList {rows} {name} cover={portrait} most={5} lead />
				{/if}
				<div class="words">
					{#if card.figure}
						<FigureCard
							label={card.figure.label}
							value={card.figure.value}
							format={(value) =>
								value === card.figure?.value
									? saidOf(card.figure.said, value, card.figure.unit)
									: figureWords(value, card.figure?.unit ?? 'count')}
							counts={card.figure.unit !== 'minute_of_day'}
							size={size === 'micro' ? 'small' : 'hero'}
							tone="lit"
							ground={false}
						/>
					{/if}
					<p
						class="said"
						class:captioned={card.figure !== null && card.figure !== undefined}
						class:long
					>
						<HistorySentence pieces={card.statement} />
					</p>
					{#if card.hidden_things.length > 0}
						<p class="mark">
							<Icon name="visibility_off" size={16} filled />
							Names something hidden
						</p>
					{/if}
				</div>
			</div>
		{/if}
		{#if foot && size !== 'micro'}
			<footer class="foot">{foot}</footer>
		{/if}
	</div>
</article>

<style>
	/* The ground is the accent's run on every size. The edge is an outline, which takes no room, so
	   the card's own width is all the container's and a story pixel is a 360th of it. */
	.recap-card {
		display: grid;
		overflow: hidden;
		outline: 1px solid var(--sift-line);
		outline-offset: -1px;
		border-radius: var(--radius-lg);
		background-image:
			radial-gradient(120% 60% at 20% 0%, var(--sift-accent-shade-1), transparent 70%),
			linear-gradient(180deg, var(--sift-accent-shade-1), var(--sift-accent-shade-2));
		box-shadow: var(--elev-3);
		color: var(--sift-ink);
	}

	/* Stood up the way a phone is held: as wide as its holder, 9:16, its height the ratio's, so a
	   card of one line and a card of a list are the same card. A container, so what is inside is
	   counted in its width. */
	.story {
		container-type: inline-size;
		inline-size: 100%;
		aspect-ratio: 9 / 16;
	}

	/* The same card filling the window's height. */
	.hero {
		container-type: inline-size;
		/* The stage it stands on gives the room, up to the saved picture's own width. */
		inline-size: min(100%, calc(var(--story-width) * 3));
		aspect-ratio: 9 / 16;
		margin-inline: auto;
	}

	/* Inside a story or hero card the page's sizes are the card's: the same at 360 wide, and in
	   proportion at any other width. */
	.story > .inside,
	.hero > .inside {
		--space-1: var(--story-space-1);
		--space-2: var(--story-space-2);
		--space-3: var(--story-space-3);
		--space-4: var(--story-space-4);
		--space-5: var(--story-space-5);
		--space-6: var(--story-space-6);
		--space-8: var(--story-space-8);
		--space-10: var(--story-space-10);
		--radius-sm: var(--story-radius-sm);
		--radius-md: var(--story-radius-md);
		--text-figure: var(--story-text-figure);
		--text-display: var(--story-text-display);
		--text-h2: var(--story-text-h2);
		--text-h3: var(--story-text-h3);
		--text-body-sm: var(--story-text-body-sm);
		--text-label: var(--story-text-label);
		--text-micro: var(--story-text-micro);
		--list-cover: var(--story-list-cover);
		--lead-cover: var(--story-lead-cover);
		--chart-mark-gap: var(--story-chart-mark-gap);
		--page-measure: var(--story-page-measure);
		padding: var(--space-6);
		gap: var(--space-4);
	}

	.inside {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-block-size: 0;
		padding: var(--space-4);
	}

	/* A hidden card keeps none of the period's colour: it is a plain card, with the card's light and
	   its edge under the transparent border (see `--sift-card`). */
	.hidden-card {
		border: 1px solid transparent;
		outline-color: transparent;
		background: var(--sift-card);
	}

	.head,
	.foot {
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
	}

	.head {
		display: flex;
		justify-content: space-between;
		gap: var(--space-2);
	}

	.place {
		font-variant-numeric: tabular-nums;
	}

	.card {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: flex-end;
		gap: var(--space-5);
		min-block-size: 0;
	}

	/* The picture or the ring takes the room the words leave, and is fitted inside it whole. */
	.picture {
		display: grid;
		flex: 1;
		grid-template-rows: minmax(0, 1fr);
		place-items: center;
		min-block-size: 0;
	}

	/* The ring stands a little off the words, as a picture does by its own frame. */
	.ringed .picture {
		padding-block: var(--space-2);
	}

	/* A cover asks for most of the card's width, at its own shape, and gives way to the words. */
	.cover {
		flex: 0 1 calc(62cqi * 4 / 3);
	}

	.cover.square {
		flex-basis: 62cqi;
	}

	/* As tall as the room, as wide as that at the picture's shape, never wider than the card. */
	.fit {
		block-size: 100%;
		max-inline-size: 100%;
		aspect-ratio: 3 / 4;
	}

	.fit.square {
		aspect-ratio: 1;
	}

	/* The ring is as wide as the frame and centred in its height. */
	.ringed .fit {
		display: grid;
		align-items: center;
	}

	.framed {
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	/* A card of words alone stands them in the middle of the card, not at its foot. */
	.card:not(.pictured) {
		justify-content: center;
	}

	.micro .card {
		justify-content: flex-start;
		gap: var(--space-2);
	}

	/* The ring's middle on a story card is narrower than on the page: the time in heading type. */
	.favourite {
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-accent-tint-1);
	}

	.words {
		display: flex;
		flex: none;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* A card with no figure says its sentence large; under a figure, the sentence is its reading;
	   a long one a step smaller either way. */
	.said {
		margin: 0;
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		text-wrap: balance;
		color: var(--sift-ink);
	}

	.said.captioned,
	.said.long {
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
	}

	.said.captioned.long {
		font: var(--text-h3);
	}

	.micro .said {
		font: var(--text-h2);
	}

	.micro .said.captioned {
		font: var(--text-body-sm);
		letter-spacing: normal;
		color: var(--sift-ink-2);
	}

	.mark {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/* The locked tile, in the tokens a hidden tile on the walls is drawn with (`Tile.svelte`): the
	   ground it always has, blurred, as a layer UNDER the glyph so the glyph stays sharp. */
	.locked {
		position: relative;
		display: grid;
		flex: 1;
		place-items: center;
		min-block-size: var(--space-16);
		overflow: hidden;
		border-radius: var(--radius-md);
		color: var(--sift-ink-3);
	}

	.locked::before {
		content: '';
		position: absolute;
		inset: 0;
		background: var(--sift-surface-3);
		filter: blur(var(--blur-veil));
	}

	.locked > :global(*) {
		position: relative;
	}
</style>
