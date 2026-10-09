<script lang="ts" module>
	/**
	 * Where a person's waiting faces came from, as the one line under them, while Sift knows them
	 * only by starter pictures (see `onstarters`).
	 */
	export function startersSay(boxes: readonly string[]): string {
		return `Found with the starter pictures from ${boxes.length > 0 ? boxes.join(' and ') : 'a stash-box'}.`;
	}
</script>

<script lang="ts">
	/*
	 * How reliably Sift recognizes one person in this library, and what it rests on.
	 *
	 * The bar is the server's rate: of her faces Sift found, the share it named without asking, his
	 * Yes and No answers counted. Under it, what the pictures it compares with are, by where they
	 * came from. Under the floor of pictures there is no rate worth drawing, and the words say so.
	 *
	 * Absent entirely when recognition is off or nobody has ever been recognized here, for the
	 * reason the appearances strip is: most installs never turn faces on, and a bar reading zero
	 * over a feature that was never switched on reports a fault where there is none.
	 */
	import {
		recognitionOf,
		referenceVerdict,
		type ReferenceStrengths,
		type Strength
	} from '$lib/people/faces.svelte';
	import { untrack } from 'svelte';
	import type { components } from '$lib/api/schema';
	import { primedOr, readingAbout } from '$lib/entity/subject.svelte';
	import { Meter, Tooltip } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		personId: string;
		/** The person's name, for the verdict's words; "them" without one. */
		name?: string | null;
		/** Bump to re-read. Agreeing to a suggestion is what moves this number, and the control that
		 *  does it sits directly below, so the bar has to notice. */
		refresh?: number;
		/**
		 * The whole screen's reading, for a WALL of people rather than one person's page.
		 *
		 * One request per card is right on a page about one person. A wall of twenty-four cards
		 * asking it twenty-four times is an N+1 read, so a caller that has already read every
		 * person's count in one request hands it over, and this makes no request at all.
		 *
		 * The verdict is still one rule rather than two: the wall's reading carries each person's
		 * token from the server, and the words below are the only thing this file knows about what
		 * a band means.
		 */
		strengths?: ReferenceStrengths | null;
		/**
		 * Told which stash-boxes this person's STARTER pictures came from, or null where there are
		 * none. A starter is one of a stash-box's photos of somebody with no confirmed face: Sift
		 * asks about them from it, never names them on its own, and drops it at the first face
		 * somebody confirms. The bar measures what Sift knows them by and a starter adds nothing to
		 * that (the server never counts one in `references`), so the page says it as one line under
		 * the faces those pictures found, not under this bar.
		 */
		onstarters?: (boxes: readonly string[] | null) => void;
	}

	let { personId, name = null, refresh = 0, strengths = null, onstarters }: Props = $props();

	/** What the bar rests on, as the server sends it. */
	type Basis = components['schemas']['StrengthBasis'];
	type Reading = Strength & { rate?: number | null; basis?: Basis };
	type Wall = ReferenceStrengths & {
		rates?: Record<string, number>;
		basis?: Record<string, Basis>;
	};

	/** Whether the numbers were handed over. One reading, so no branch can disagree with another. */
	const handed = $derived(strengths !== null);

	/* Kept on screen while it is re-read. See `readingAbout`. The whole block is drawn behind an
	   `{#if}` on it, so a reading that cleared itself at the top of its own effect would take the
	   block out of the page and put it back on every library write. */
	const reading = readingAbout<Reading | null>(
		() => personId,
		async (id) => (handed ? null : await primedOr('recognition', id, () => recognitionOf(id))),
		null,
		() => refresh
	);
	/* The handed reading, put into the shape this draws. The verdict token is the server's on both
	   paths: a band worked out here from the count would let a card disagree with the page it
	   opens. */
	const wall = $derived(strengths as Wall | null);
	const fromWall = $derived<Reading | null>(
		wall === null
			? null
			: {
					references: wall.people[personId] ?? 0,
					// The wall's reading carries no starter pictures; the page's own read does, and
					// it is what `onstarters` tells (a starter is never counted as a reference).
					starters: 0,
					starters_from: [],
					starters_retired: 0,
					target: wall.target,
					floor: wall.floor,
					strong: wall.strong,
					rate: wall.rates?.[personId] ?? null,
					basis: wall.basis?.[personId],
					fraction: wall.rates?.[personId] ?? 0,
					verdict: referenceVerdict(personId, wall)
				}
	);
	const strength = $derived(fromWall ?? reading.value);

	/* From the person's own reading only: a wall hands over counts, which carry no starters. */
	$effect(() => {
		const now = strength;
		const boxes = now && (now.starters ?? 0) > 0 ? (now.starters_from ?? []) : null;
		untrack(() => onstarters?.(boxes));
	});

	/*
	 * Under the bar: the verdict in words, then one short line of what the numbers mean for this
	 * person. No advice: the band sits above a wall of files and does not scroll.
	 *
	 * The verdict is the server's token and every number is the server's, so nothing here can drift
	 * from the curve behind them. "Them" throughout: Sift is not told anybody's pronouns.
	 */
	function wordsFor(first: string): Record<string, string> {
		return {
			none: `Sift can't identify ${first} yet`,
			few: `Sift needs a few more faces of ${first}`,
			unseen: `Sift hasn't found ${first} in your files yet`,
			weak: `Sift can't identify ${first} reliably yet`,
			fair: `Sift can now identify ${first} reasonably well`,
			good: `Sift identifies ${first} reliably`,
			strong: `Sift identifies ${first} reliably`
		};
	}
	/* Their first name where the caller knows it ("Sift identifies Ada reliably"); "them"
	   where it does not. */
	const first = $derived(name?.trim().split(/\s+/)[0] || 'them');
	const WORDS = $derived(wordsFor(first));

	function many(count: number, one: string, more: string): string {
		return `${counted(count)} ${count === 1 ? one : more}`;
	}

	/** What the bar rests on, by where each picture came from: "From 23 imported fingerprints and
	 *  15 faces you confirmed." */
	function basisOf(now: Reading): string {
		const basis = now.basis;
		if (!basis) return `From ${many(now.references, 'picture', 'pictures')}.`;
		const parts = [
			basis.imported > 0
				? many(basis.imported, 'imported fingerprint', 'imported fingerprints')
				: '',
			basis.confirmed > 0 ? many(basis.confirmed, 'face you confirmed', 'faces you confirmed') : '',
			basis.learned > 0 ? many(basis.learned, 'face Sift recognized', 'faces Sift recognized') : ''
		].filter(Boolean);
		const said =
			parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}` : parts[0];
		const turned =
			basis.turned > 0
				? ` ${many(basis.turned, 'face', 'faces')} facing away kept for them, never matched.`
				: '';
		return `From ${said ?? many(now.references, 'picture', 'pictures')}.${turned}`;
	}

	/** The bar's tooltip: what the share is, and the floor under which there is none. */
	function rateSaid(now: Reading): string {
		const floor = `Drawn once Sift has ${many(now.floor, 'picture', 'pictures')} of them.`;
		if (now.rate === null || now.rate === undefined) return floor;
		return `${Math.round(now.rate * 100)}% of their faces named without asking, counting your answers. ${floor}`;
	}
</script>

{#if strength && strength.strong > 0 && !(strength.verdict === 'none' && strength.references === 0)}
	<div class="block">
		<!-- The shared meter (a reading, not a task: it goes down when a reference is taken away).
		     The primitive emits the role and the range, and this draws only the fill. -->
		<Tooltip label={rateSaid(strength)} wide stretch>
			<Meter value={strength.rate ?? 0} max={1} label="How reliably Sift can recognize this person">
				{#snippet fill(fraction)}
					<!--
					The colour is the band, and the band is the server's word for this person.

					Each band has its own short gradient: red into orange while there is not enough
					to recognize anybody, orange into yellow through the fair band, yellow into
					green through the good one, and green once it is strong. A single gradient
					across the track would be a scale of its own, colouring a position along the
					track rather than the verdict; per band, the colour and the sentence under the
					bar are one fact, and movement inside a band still reads as movement.

					The band comes down as `verdict`, the server's token; nothing here bands a count.

					The length is the fraction, clipped rather than scaled: a gradient scaled into
					the filled width would reach its far end at every value, so every bar would
					finish on its band's brightest colour however short.
				-->
					<div
						class="spectrum"
						data-band={strength.verdict}
						style:clip-path={`inset(0 ${(1 - fraction) * 100}% 0 0)`}
					></div>
				{/snippet}
			</Meter>
		</Tooltip>
		<!-- The verdict wears the person-check glyph in the band's colour, green where Sift identifies
		     them; its basis sits tight beneath it. -->
		<div class="said">
			<p class="count" data-band={strength.verdict}>
				<Icon name="person_check" size={16} /><span class="words"
					>{WORDS[strength.verdict] ?? WORDS.none}</span
				>
			</p>
			<p class="basis">{basisOf(strength)}</p>
		</div>
	</div>
{/if}

<style>
	/*
	 * A bar and two short lines, and deliberately nothing more: the why of the numbers is in
	 * Settings beside the face recognition switch. This band does not scroll.
	 */
	.block {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The verdict and what it rests on, one unit under the bar. */
	.said {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	/*
	 * The fill, coloured by the band it is in (see the snippet for why it is not one run).
	 *
	 * The one place in this app where red does not mean a failure: this is a position on a scale
	 * from bad to good, not a report that something broke.
	 *
	 * The three colours named are the app's semantic tokens, so the bar wears the same red, amber
	 * and green as every other statement about how something is doing. Orange is mixed from the two
	 * it sits between, in OKLCH, the space the app mixes non-token colours in; a plain sRGB mix of
	 * red and amber comes out muddy and darker than both.
	 *
	 * The red is `--sift-bad-text`, not `--sift-bad`. Against this bar's track (`--sift-surface-4`,
	 * what `Meter` draws), `--sift-bad` comes to 2.88:1 on midnight, 2.76:1 on graphite and 2.41:1
	 * on chrome, under the 3:1 floor for a meaningful graphic in every base. `--sift-bad-text` is
	 * the same red solved to be read on a surface (3.63 / 4.15 / 3.63). The orange is mixed from it
	 * and clears comfortably (4.74:1 at the worst). All three bases are dark
	 * (`scripts/check_dark_only.js`); chrome has the lightest track.
	 *
	 * The rule with no attribute is the floor band, which an unheard-of token falls back to: a
	 * client one release behind a server with a new band draws "not enough yet", and the colour
	 * must agree with the words.
	 */
	.spectrum {
		--band-orange: color-mix(in oklch, var(--sift-bad-text), var(--sift-warn));
		block-size: 100%;
		border-radius: 999px;
		background: linear-gradient(90deg, var(--sift-bad-text), var(--band-orange));
		transition: clip-path var(--dur-base) var(--ease);
	}

	/* Enough to recognize somebody, and not much more. */
	.spectrum[data-band='fair'] {
		background: linear-gradient(90deg, var(--band-orange), var(--sift-warn));
	}

	/* Working well, on the way to dependable. */
	.spectrum[data-band='good'] {
		background: linear-gradient(90deg, var(--sift-warn), var(--sift-ok));
	}

	/*
	 * Dependable. Green throughout, from a darker green of the same hue to the full token.
	 *
	 * It mixes towards `--sift-ok-bg`, not the track. OKLCH interpolates hue along the short arc,
	 * so mixing `--sift-ok` (hue 155) towards the blue-grey track (hue 268) lands on a teal.
	 * `--sift-ok-bg` is the same green solved as a dark tint, at hue 156-159 in all three bases, so
	 * the mix keeps the hue and drops only the lightness. The start lands at hue 155-157 and
	 * lightness 0.61 in every base and reads 3.71:1 on midnight's track, 3.71:1 on graphite's and
	 * 3.24:1 on chrome's, over the 3:1 floor. 72% is the first step with a quarter point of room on
	 * the lightest track; 70% comes to 3.12:1 there.
	 *
	 * The other three bands run 23 -> 53 (red into orange), 53 -> 83 (orange into yellow) and 83 ->
	 * 155 (yellow into green): nothing on the bar comes near blue.
	 */
	.spectrum[data-band='strong'] {
		background: linear-gradient(
			90deg,
			color-mix(in oklch, var(--sift-ok) 72%, var(--sift-ok-bg)),
			var(--sift-ok)
		);
	}

	/* The verdict in words. */
	.count {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.count {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.words {
		min-inline-size: 0;
	}

	/* The numbers behind the verdict, as a caption tight under it. */
	.basis {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The glyph climbs the bar's own colours with the verdict: the band's orange for fair, Sift's
	   yellow for good, the green for strong; quiet ink below that. */
	.count {
		--band-orange: color-mix(in oklch, var(--sift-bad-text), var(--sift-warn));
	}

	.count > :global(.icon) {
		color: var(--sift-ink-3);
	}

	.count[data-band='fair'] > :global(.icon) {
		color: var(--band-orange);
	}

	.count[data-band='good'] > :global(.icon) {
		color: var(--sift-warn);
	}

	.count[data-band='strong'] > :global(.icon) {
		color: var(--sift-ok);
	}
</style>
