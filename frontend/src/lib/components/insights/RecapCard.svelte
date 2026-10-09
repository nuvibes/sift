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
	 * ONE CARD, at four sizes: `story` the deck's card (as wide as its holder, 9:16), `hero` the
	 * same filling the window's height, `tile` a board's tile filling its holder both ways (laid
	 * out by its `shape`), `micro` the figure and its sentence on a panel. Story and hero count
	 * their type in the card's own width (`--story-*`), a tile in its own box (`--tile-*`), so the
	 * saved picture is this card painted again at 1080 wide (`share-card.ts`).
	 *
	 * A card is a headline of a few words, the heavy number, and a line of context, over a picture
	 * of what it counts: a face, a podium of faces, a Site's mark, the file itself, the hours as a
	 * skyline, the shares as a stack, the days as squares, or a year's own drawing (`cards/`). Its
	 * ground is the person's own pictures blurred under the family's shade (`Ground`), its colour
	 * its family's swatch nearest the top file's (`cards/family.ts`). It builds in two beats: the
	 * picture and the headline, then the number counting up and its context; `data-built` says
	 * when the second has landed, which the painter waits for. Reduced motion lands both at once.
	 *
	 * ## A locked tile
	 *
	 * `hidden` is a card that is there and says nothing (the vault is locked, the placeholder mode
	 * on, and what it would say names something hidden). It draws NONE of what it was handed,
	 * picture and ground included: the server sends it empty, and this is the second lock on the
	 * same door. A card that names something hidden while the vault is OPEN wears the mark a
	 * hidden thing wears everywhere, and is never saved as a picture (`savable`).
	 */
	import { untrack } from 'svelte';

	import Icon from '$lib/components/Icon.svelte';
	import FigureCard from '$lib/components/charts/FigureCard.svelte';
	import RankedList from '$lib/components/charts/RankedList.svelte';
	import { Avatar, HistorySentence } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { figureWords, saidOf, timeWords, wordsOf } from '$lib/components/insights/figures';
	import { KIND_WORDS } from '$lib/components/insights/words';
	import Days from '$lib/components/insights/cards/Days.svelte';
	import { DAYS_MOST, designOf, type Design } from '$lib/components/insights/cards/design';
	import Ground from '$lib/components/insights/cards/Ground.svelte';
	import Podium from '$lib/components/insights/cards/Podium.svelte';
	import Shares from '$lib/components/insights/cards/Shares.svelte';
	import Skyline from '$lib/components/insights/cards/Skyline.svelte';
	import Tally from '$lib/components/insights/cards/Tally.svelte';
	import Wall, { type WallShape } from '$lib/components/insights/cards/Wall.svelte';
	import BeforeAfter from '$lib/components/insights/cards/year/BeforeAfter.svelte';
	import Closing from '$lib/components/insights/cards/year/Closing.svelte';
	import FirstLast from '$lib/components/insights/cards/year/FirstLast.svelte';
	import Heat from '$lib/components/insights/cards/year/Heatmap.svelte';
	import Mosaic from '$lib/components/insights/cards/year/Mosaic.svelte';
	import Race from '$lib/components/insights/cards/year/Race.svelte';
	import SessionSteps from '$lib/components/insights/cards/year/SessionPath.svelte';
	import { familyOf, groundOf, swatchOf, type Family } from '$lib/components/insights/cards/family';
	import type { SessionPath } from '$lib/components/insights/cards/kinds';
	import type { RecapCard } from '$lib/library/recaps.svelte';
	import { keyWords } from '$lib/components/insights/cards/voice';
	import { arrive, motion } from '$lib/shell/motion.svelte';

	type NamedRow = components['schemas']['NamedRow'];
	type HistoryPiece = components['schemas']['HistoryPiece'];
	type Designed = RecapCard;
	export type Shape = '1x1' | '2x1' | '2x2' | '4x2' | '6x2';

	interface Props {
		card: RecapCard;
		/** The recap's name, at the head of the card: "Your week". */
		heading?: string;
		/** Where this card falls in the recap, at the head: "3 of 11". */
		place?: string;
		/** The span the recap covers, at the foot, so a card kept as a picture says which week. */
		foot?: string;
		size?: 'micro' | 'story' | 'hero' | 'tile';
		/** A tile's size in the board's units, columns by rows. */
		shape?: Shape;
		/** The longest session's pages, for the card that says how one session went. */
		session?: SessionPath | null;
		/** The pictures of the card's ground: a deck's (`groundOf`) or a period's; absent, its own. */
		ground?: readonly string[] | null;
		/** The colour family; absent, the kind's. */
		family?: Family;
		/** The top file's hue, in OKLCH degrees; absent, the card's own or the family's middle. */
		accent?: number | null;
		/** Build in two beats as it arrives; off for a card laid out only to be painted. */
		build?: boolean;
	}

	let {
		card,
		heading = '',
		place = '',
		foot = '',
		size = 'story',
		shape = '2x2',
		session = null,
		ground = null,
		family,
		accent,
		build = true
	}: Props = $props();

	const designed = $derived(card as Designed);
	const kin = $derived(family ?? familyOf(card.kind));
	const swatch = $derived(swatchOf(kin, accent ?? designed.accent_hue));
	const roomy = $derived(size !== 'micro' && !(size === 'tile' && shape === '1x1'));

	/* The kind whose picture is a drawing of its own. */
	const own = $derived.by(() => {
		if (!roomy) return null;
		const { kind } = card;
		if (kind === 'mosaic' && card.rows.length > 0) return kind;
		if (kind === 'first_last' && card.rows.length > 0) return kind;
		if ((kind === 'before_after' || kind === 'race') && card.chart) return kind;
		if (kind === 'heatmap' && card.calendar && card.calendar.days.length > DAYS_MOST) return kind;
		if (kind === 'closing' && card.figures.length > 0) return kind;
		if (kind === 'session' && session && session.steps.length > 0) return kind;
		return null;
	});

	const rows = $derived(
		(card.rows ?? []).map((row) => ({ ...row, said: saidOf(row.said, row.value, row.unit) }))
	);
	/* A tile one row tall lays its faces along one line. */
	const flat = $derived(size === 'tile' && (shape === '2x1' || shape === '1x1'));
	const covered = $derived(rows.some((row) => row.cover));

	/* A Site's picture is its mark, drawn whole (the top Site, or a card whose sentence names one);
	   a song's art is square. */
	const mark = $derived(
		card.kind === 'top_site' || card.statement.find((piece) => piece.kind !== null)?.kind === 'site'
	);
	const square = $derived(card.kind === 'top_song');

	/* What the card's picture is: a year's own drawing, or one read from what it carries. */
	const design = $derived<Design | null>(
		roomy ? (own ? 'own' : designOf(card, mark, square)) : null
	);

	/* The hours' bars by the time each began; any other bars by their own names. */
	const towers = $derived(
		(card.chart?.bars ?? []).map((bar) => ({
			when:
				card.chart?.bars.length === 24 && /^\d{2}$/.test(bar.label)
					? timeWords(Number(bar.label) * 60)
					: bar.label,
			value: bar.parts.reduce((sum, part) => sum + part.value, 0)
		}))
	);
	const barWords = $derived(
		wordsOf(
			(card.chart?.bars ?? []).map((bar) => ({
				value: bar.parts.reduce((sum, part) => sum + part.value, 0),
				said: bar.said
			})),
			card.chart?.unit ?? 'ms'
		)
	);
	const partWords = $derived(wordsOf(card.chart?.bars[0]?.parts ?? [], card.chart?.unit ?? 'ms'));

	const pictures = $derived(card.hidden || size === 'micro' ? [] : (ground ?? groundOf([card])));

	/* The voice's lines where the card carries them; the fact sentence is then the definition. */
	const headline = $derived(designed.headline ?? null);
	const context = $derived(designed.context ?? null);
	const voiced = $derived(Boolean(context && context.length > 0));

	/* Past this many letters a sentence is set a step smaller, so it fits beside a picture. */
	const LONG = 90;
	const long = $derived(card.statement.reduce((sum, piece) => sum + piece.text.length, 0) > LONG);
	/* Whose picture the cover is: the thing the sentence names, for the letter drawn when it has none. */
	const subject = $derived(
		card.statement.find((piece) => piece.kind !== null)?.text ?? card.figure?.label ?? ''
	);
	const figureSaid = $derived(
		card.figure ? saidOf(card.figure.said, card.figure.value, card.figure.unit) : ''
	);
	/* A number wider than the card is set a step smaller, then another. */
	const reach = $derived(figureSaid.length > 8 ? 'far' : figureSaid.length > 5 ? 'wide' : '');

	/* THE TWO BEATS: the picture and the headline, then the number and its context. */
	const BEAT_MS = 450;
	let beat = $state(untrack(() => (build && size !== 'micro' && !motion.reduced ? 1 : 2)));
	$effect(() => {
		if (beat !== 1) return;
		const wait = setTimeout(() => (beat = 2), BEAT_MS);
		return () => clearTimeout(wait);
	});
</script>

{#snippet name(row: NamedRow)}
	<HistorySentence pieces={[row.piece]} />
{/snippet}

{#snippet portrait(row: NamedRow)}
	{#if row.cover !== null}
		<Avatar src={row.cover} name={row.piece.text} decorative lazy />
	{/if}
{/snippet}

<!-- A line of the voice: a plain piece with its numbers in the heavier weight, a name as the way
     to the thing it names. -->
{#snippet line(said: HistoryPiece[])}
	{#each said as one, at (at)}{#if one.kind === null && !one.href && one.rest.length === 0}{#each keyWords(one.text) as part, index (index)}{#if part.key}<strong
						>{part.text}</strong
					>{:else}{part.text}{/if}{/each}{:else}<HistorySentence pieces={[one]} />{/if}{/each}
{/snippet}

{#snippet picture()}
	{#if own === 'mosaic'}
		<Mosaic {rows} />
	{:else if own === 'first_last'}
		<FirstLast {rows} figure={card.figure} />
	{:else if own === 'before_after' && card.chart}
		<BeforeAfter chart={card.chart} {rows} />
	{:else if own === 'race' && card.chart}
		<Race {rows} chart={card.chart} />
	{:else if own === 'heatmap' && card.calendar}
		<Heat calendar={card.calendar} label={heading || KIND_WORDS.all} />
	{:else if own === 'closing'}
		<Closing figures={card.figures} cover={card.cover} />
	{:else if own === 'session' && session}
		<SessionSteps path={session} />
	{:else if design === 'wall' && designed.wall}
		<div class="picture"><Wall wall={designed.wall} {rows} /></div>
	{:else if design === 'podium'}
		<Podium {rows} {flat} />
	{:else if design === 'marks'}
		<Podium {rows} marks {flat} />
	{:else if design === 'days' && card.calendar}
		<Days
			days={card.calendar.days}
			format={wordsOf(card.calendar.days, card.calendar.unit)}
			label={heading || KIND_WORDS.all}
		/>
	{:else if design === 'skyline'}
		<Skyline bars={towers} format={barWords} label={KIND_WORDS.all} />
	{:else if design === 'shares' && card.chart}
		<Shares parts={card.chart.bars[0].parts} format={partWords} />
	{:else if design === 'calendar' && card.calendar}
		<Heat calendar={card.calendar} label={heading || KIND_WORDS.all} />
	{:else if design === 'list'}
		<RankedList {rows} {name} cover={covered ? portrait : undefined} most={5} lead={covered} />
	{:else if card.cover}
		<!-- Decorative: the sentence names what it is a picture of. Through `Avatar`, so a thing
		     with no picture draws its letter rather than a broken-picture mark. -->
		<div class="picture is-{design}">
			<div class="fit framed">
				<Avatar
					src={card.cover}
					name={subject}
					shape={design === 'picture' ? 'portrait' : 'face'}
					mark={design === 'mark'}
					bare={design === 'mark'}
					decorative
				/>
			</div>
		</div>
	{/if}
{/snippet}

<article
	class="recap-card {size}"
	class:hidden-card={card.hidden}
	data-card={card.kind}
	data-family={kin}
	data-swatch={swatch}
	data-shape={size === 'tile' ? shape : undefined}
	data-design={design}
	data-built={beat === 2}
	data-savable={savable(card)}
>
	{#if pictures.length > 0}
		<Ground {pictures} />
	{/if}
	<div class="inside">
		{#if (size === 'story' || size === 'hero') && (heading || place)}
			<header class="head">
				<span>{heading}</span>
				<span class="place">{place}</span>
			</header>
		{:else if size === 'tile' && shape !== '1x1' && heading}
			<!-- A tile's own title is its heading: the board has none over it. -->
			<header class="head"><span>{heading}</span></header>
		{/if}
		{#if card.hidden}
			<!-- The glyph is a child of the blurred layer's box, so it is painted on top and stays sharp.
			     The same size the wall's locked tile uses: here too the mark IS the picture. -->
			<div class="locked">
				<Icon name="visibility_off" size={34} filled label="Hidden" />
			</div>
		{:else}
			<div class="card" class:pictured={design !== null}>
				{#if design !== null}
					<div class="art">{@render picture()}</div>
				{/if}
				<div class="words" class:quiet={!card.figure && !(headline && headline.length > 0)}>
					{#if headline && headline.length > 0 && size !== 'micro'}
						<p class="headline">{@render line(headline)}</p>
					{/if}
					{#key beat}
						<div class="later" class:waiting={beat === 1} in:arrive={{ y: 12, pace: 'slow' }}>
							{#if card.figure && own !== 'first_last'}
								<div class="number {size === 'micro' ? '' : reach}">
									<FigureCard
										label={card.figure.label}
										value={card.figure.value}
										format={(value) =>
											value === card.figure?.value
												? figureSaid
												: figureWords(value, card.figure?.unit ?? 'count')}
										counts={beat === 2 && card.figure.unit !== 'minute_of_day'}
										size={size === 'micro' ? 'small' : 'hero'}
										tone="lit"
										ground={false}
									/>
								</div>
							{/if}
							{#if voiced && context}
								<p class="context">{@render line(context)}</p>
							{/if}
							<p
								class="said"
								class:defines={voiced}
								class:captioned={card.figure !== null && card.figure !== undefined}
								class:long
							>
								<HistorySentence pieces={card.statement} />
							</p>
							{#if roomy && card.figures.length > 0 && own !== 'closing'}
								<Tally figures={card.figures} />
							{/if}
						</div>
					{/key}
					{#if card.hidden_things.length > 0}
						<p class="mark">
							<Icon name="visibility_off" size={16} filled />
							Names something hidden
						</p>
					{/if}
				</div>
			</div>
		{/if}
		{#if foot && (size === 'story' || size === 'hero')}
			<footer class="foot">
				<span>{foot}</span>
				<span class="brand">Sift</span>
			</footer>
		{/if}
	</div>
</article>

<style>
	/* The ground is the family's run, deepest at the foot; the pictures, when there are any, lie
	   over it under its shade (`Ground`). The edge is an outline, which takes no room. The run is
	   the accent's tokens restated in the family's hue, so everything inside reads one name. */
	.recap-card {
		--family-hue: var(--family-viewing-b);
		--sift-accent-shade-1: color-mix(in oklch, var(--family-hue) 46%, black);
		--sift-accent-shade-2: color-mix(in oklch, var(--family-hue) 24%, black);
		--sift-accent-tint-1: color-mix(in oklch, var(--family-hue) 42%, white);
		--sift-accent-tint-2: color-mix(in oklch, var(--family-hue) 18%, white);
		--card-face: var(--story-face);
		--card-face-small: var(--story-face-small);
		position: relative;
		display: grid;
		isolation: isolate;
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

	.recap-card[data-family='viewing'][data-swatch='a'] {
		--family-hue: var(--family-viewing-a);
	}

	.recap-card[data-family='viewing'][data-swatch='c'] {
		--family-hue: var(--family-viewing-c);
	}

	.recap-card[data-family='people'][data-swatch='a'] {
		--family-hue: var(--family-people-a);
	}

	.recap-card[data-family='people'][data-swatch='b'] {
		--family-hue: var(--family-people-b);
	}

	.recap-card[data-family='people'][data-swatch='c'] {
		--family-hue: var(--family-people-c);
	}

	.recap-card[data-family='sites'][data-swatch='a'] {
		--family-hue: var(--family-sites-a);
	}

	.recap-card[data-family='sites'][data-swatch='b'] {
		--family-hue: var(--family-sites-b);
	}

	.recap-card[data-family='sites'][data-swatch='c'] {
		--family-hue: var(--family-sites-c);
	}

	.recap-card[data-family='organizing'][data-swatch='a'] {
		--family-hue: var(--family-organizing-a);
	}

	.recap-card[data-family='organizing'][data-swatch='b'] {
		--family-hue: var(--family-organizing-b);
	}

	.recap-card[data-family='organizing'][data-swatch='c'] {
		--family-hue: var(--family-organizing-c);
	}

	.recap-card[data-family='theater'][data-swatch='a'] {
		--family-hue: var(--family-theater-a);
	}

	.recap-card[data-family='theater'][data-swatch='b'] {
		--family-hue: var(--family-theater-b);
	}

	.recap-card[data-family='theater'][data-swatch='c'] {
		--family-hue: var(--family-theater-c);
	}

	.recap-card[data-family='downloads'][data-swatch='a'] {
		--family-hue: var(--family-downloads-a);
	}

	.recap-card[data-family='downloads'][data-swatch='b'] {
		--family-hue: var(--family-downloads-b);
	}

	.recap-card[data-family='downloads'][data-swatch='c'] {
		--family-hue: var(--family-downloads-c);
	}

	.recap-card[data-family='alongside'][data-swatch='a'] {
		--family-hue: var(--family-alongside-a);
	}

	.recap-card[data-family='alongside'][data-swatch='b'] {
		--family-hue: var(--family-alongside-b);
	}

	.recap-card[data-family='alongside'][data-swatch='c'] {
		--family-hue: var(--family-alongside-c);
	}

	/* Stood up the way a phone is held: as wide as its holder, 9:16. A container, so what is
	   inside is counted in its width. */
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

	/* A board's tile: its holder's box both ways, its type counted in that box. */
	.tile {
		--card-face: var(--tile-face);
		--card-face-small: var(--tile-face-small);
		container-type: size;
		inline-size: 100%;
		block-size: 100%;
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

	/* Over the ground, which is a layer of its own. */
	.inside {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-block-size: 0;
		padding: var(--space-4);
	}

	/* A hidden card keeps none of the period's colour: a plain card, with the card's light and its
	   edge under the transparent border (see `--sift-card`). */
	.hidden-card {
		border: 1px solid transparent;
		outline-color: transparent;
		background: var(--sift-card);
	}

	.head,
	.foot {
		display: flex;
		justify-content: space-between;
		gap: var(--space-2);
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
	}

	.place {
		font-variant-numeric: tabular-nums;
	}

	/* The brand, small in the corner, as a picture of the card carries it. */
	.brand {
		font: var(--text-label);
		font-family: var(--font-display);
		font-weight: 800;
		letter-spacing: normal;
		text-transform: none;
		color: var(--sift-accent-tint-1);
	}

	.card {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: flex-end;
		gap: var(--space-5);
		min-block-size: 0;
	}

	/* A card of words alone stands them in the middle of the card, not at its foot. */
	.card:not(.pictured) {
		justify-content: center;
	}

	/* The picture takes the room the words leave. */
	.art {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-block-size: 0;
		min-inline-size: 0;
	}

	.picture {
		display: grid;
		flex: 1;
		grid-template-rows: minmax(0, 1fr);
		place-items: center;
		min-block-size: 0;
	}

	/* A face, a mark and a song's art are squares across most of the card's width; the file
	   itself fills the room, bleeding nearly to the card's edges. */
	.fit {
		inline-size: 72%;
		max-block-size: 100%;
		aspect-ratio: 1;
	}

	.is-mark .fit {
		inline-size: 56%;
	}

	.is-picture .fit {
		inline-size: 100%;
		block-size: 100%;
		aspect-ratio: auto;
	}

	.fit > :global(.avatar) {
		aspect-ratio: auto;
		block-size: 100%;
	}

	.framed {
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-3);
	}

	/* A mark is drawn whole on the card's own ground, with no frame round it. */
	.is-mark .framed {
		overflow: visible;
		box-shadow: none;
	}

	/* A person's face is round, ringed in the family's ink. */
	.is-face .framed {
		border-radius: 50%;
		box-shadow:
			0 0 0 calc(var(--card-face) / 40) var(--sift-accent-tint-1),
			var(--elev-3);
	}

	.words {
		display: flex;
		flex: none;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
		--text-figure: var(--story-text-hero);
	}

	.later {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* The first beat holds the second's room, so nothing moves when it lands. */
	.later.waiting {
		visibility: hidden;
	}

	/* The headline: a few words over the number, the card's setup. */
	.headline {
		margin: 0;
		font: var(--story-text-headline);
		text-wrap: balance;
		color: var(--sift-ink);
	}

	/* The number is the card: heavy, in the family's ink, a step smaller as it grows longer. */
	.number.wide {
		--text-figure: var(--story-text-hero-wide);
	}

	.number.far {
		--text-figure: var(--story-text-hero-far);
	}

	/* The context: its key words (the numbers, the names) in the heavier weight. */
	.context {
		margin: 0;
		font: var(--text-h3);
		font-weight: 500;
		text-wrap: pretty;
		color: var(--sift-ink);
	}

	.context :global(a),
	.context :global(strong) {
		font-weight: 700;
	}

	/* A card with no figure says its sentence large; under a figure, the sentence is its reading;
	   a long one a step smaller either way. Under the voice it is the small definition. */
	/* A file's name has no spaces to break at: it breaks anywhere rather than past the card. */
	.headline,
	.context,
	.said {
		overflow-wrap: anywhere;
	}

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

	.said.defines {
		font: var(--text-label);
		letter-spacing: normal;
		color: var(--sift-ink-2);
	}

	.micro .card {
		justify-content: flex-start;
		gap: var(--space-2);
	}

	.micro .said {
		font: var(--text-h2);
	}

	.micro .said.captioned {
		font: var(--text-body-sm);
		letter-spacing: normal;
		color: var(--sift-ink-2);
	}

	/* A TILE, by its shape: a square tile the number alone, a wide one the words beside the
	   picture, a tall one the picture over the words. */
	.tile .words {
		--text-figure: var(--tile-text-hero);
	}

	.tile .headline {
		font: var(--tile-text-headline);
	}

	.tile[data-shape='2x1'] {
		--card-face: var(--tile-face-flat);
		--card-face-small: var(--tile-face-flat-small);
	}

	.tile .art {
		--days-high: var(--tile-days-high);
		--podium-gap: var(--space-2);
		--podium-lead-gap: var(--space-1);
	}

	/* A tile's sentence stops at its room: a long name breaks anywhere, and past its lines it is
	   cut (the Stats a tile opens says it whole). Under a figure it has three, alone five. */
	.tile .said {
		display: -webkit-box;
		overflow: hidden;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 5;
		line-clamp: 5;
	}

	.tile .said.captioned {
		-webkit-line-clamp: 3;
		line-clamp: 3;
	}

	/* A short tile with no figure is its picture alone. */
	.tile[data-shape='2x1'] .words.quiet {
		display: none;
	}

	/* Under a file or a podium the sentence is a band of its own: the name on one line, cut with
	   an ellipsis; two lines under faces. */
	.tile[data-design='picture'] .said {
		-webkit-line-clamp: 1;
		line-clamp: 1;
	}

	.tile[data-design='podium'] .said,
	.tile[data-design='marks'] .said {
		-webkit-line-clamp: 2;
		line-clamp: 2;
	}

	/* Nothing in a tile is drawn past its own box, its heading included. */
	.tile .card {
		overflow: hidden;
	}

	/* A short wide tile: the figure a step smaller, its other figures left to Stats. */
	.tile[data-shape='2x1'] .words,
	.tile[data-shape='2x1'] .number.number {
		--text-figure: var(--tile-text-hero-far);
	}

	.tile[data-shape='2x1'] .tally {
		display: none;
	}

	/* A wide tile with no picture stands its words in the middle, the figure large. */
	.tile[data-shape='4x2'] .card:not(.pictured) .words,
	.tile[data-shape='6x2'] .card:not(.pictured) .words {
		--text-figure: var(--tile-text-hero-lone);
		align-items: center;
		justify-content: center;
		text-align: center;
	}

	.tile[data-shape='4x2'] .card:not(.pictured) .number:is(.wide, .far),
	.tile[data-shape='6x2'] .card:not(.pictured) .number:is(.wide, .far) {
		--text-figure: var(--tile-text-hero-lone-far);
	}

	/* A file with no picture yet: its letter small in the corner, clear of the words. */
	.tile[data-design='picture'] .fit :global(.monogram) {
		place-items: start end;
		padding: var(--space-4);
		font: var(--text-display-lg);
	}

	.tile .number.wide {
		--text-figure: var(--tile-text-hero-wide);
	}

	.tile .number.far {
		--text-figure: var(--tile-text-hero-far);
	}

	.tile[data-shape='4x2'] .card,
	.tile[data-shape='6x2'] .card,
	.tile[data-shape='2x1'] .card {
		flex-direction: row-reverse;
		align-items: stretch;
	}

	.tile[data-shape='4x2'] .words,
	.tile[data-shape='6x2'] .words,
	.tile[data-shape='2x1'] .words {
		flex: 1;
		justify-content: flex-end;
	}

	.tile[data-shape='2x1'] .said,
	.tile[data-shape='1x1'] .said {
		display: none;
	}

	.tile[data-shape='1x1'] .card {
		justify-content: flex-end;
	}

	/* The file itself fills its tile, the words over its foot on the family's shade. */
	.tile[data-design='picture'] .art {
		position: absolute;
		inset: 0;
	}

	.tile[data-design='picture'] .picture,
	.tile[data-design='picture'] .fit {
		inline-size: 100%;
		block-size: 100%;
		max-inline-size: none;
		aspect-ratio: auto;
	}

	.tile[data-design='picture'] .framed {
		border-radius: 0;
	}

	.tile[data-design='picture'] .fit :global(.avatar) {
		aspect-ratio: auto;
		block-size: 100%;
	}

	/* The file to the tile's edges: the tile keeps no padding of its own, its words and title do. */
	.tile[data-design='picture'] > .inside {
		padding: 0;
	}

	.tile[data-design='picture'] .head {
		position: relative;
		padding: var(--space-4) var(--space-4) 0;
	}

	.tile[data-design='picture'] .words {
		position: relative;
		padding: var(--space-4);
		background-color: color-mix(
			in srgb,
			var(--sift-accent-shade-2) var(--card-ground-scrim),
			transparent
		);
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
