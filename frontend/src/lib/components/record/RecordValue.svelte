<script lang="ts">
	/* One value, drawn the way its declared type says to draw it.
	 *
	 * ## Why the type decides and not the caller
	 *
	 * The same field is drawn on more than one surface (a file's length is on its record and in
	 * the player's stats overlay) and the two must not each hold their own idea of what a
	 * millisecond count looks like. The server declares the type once; this is the one place that
	 * turns a type into something to read.
	 *
	 * ## And the arithmetic is not here either
	 *
	 * A size, a length, a rate, a shape, an encoder and a bit depth are the file's own facts, and
	 * the player's panels draw them too. They come from `$lib/library/facts`, which is the only place any
	 * of them is worked out. This file decides which surface asks for what and what a blank looks
	 * like; it does not decide how many bytes are in a gigabyte.
	 *
	 * A HEIGHT is the same rule one module along. `$lib/shell/measure` turns the centimetres Sift stores
	 * into whichever system this account reads in, and the preference is read here rather than
	 * handed in as a prop: every surface that draws a record would otherwise have to carry it down,
	 * and a surface that forgot would quietly show one account's answer in somebody else's units.
	 *
	 * ## Nothing is blank
	 *
	 * A missing value is drawn as a dash rather than as an empty space. A record with gaps in it
	 * reads as a screen that failed to load; a dash says the field exists and nobody has filled it,
	 * which is a different and true thing.
	 */
	import { Chip, ChipRow } from '$lib/components/common';
	import { entityPicture } from '$lib/components/EntityPreview.svelte';
	import { linkMarks } from '$lib/entity/entity-picture';
	import { SvelteSet } from 'svelte/reactivity';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Copyable from './Copyable.svelte';
	import { constantSaid } from '$lib/entity/constant-word';
	import { countryName } from '$lib/people/countries';
	import * as facts from '$lib/library/facts';
	import { height } from '$lib/shell/measure';
	import { calendarDay, exactly, onRecord } from '$lib/shell/when';
	import { appearance } from '$lib/theme/appearance.svelte';
	import type { FieldKind, RecordLink } from '$lib/entity/records.svelte';

	interface Props {
		kind: FieldKind;
		value: unknown;
		/**
		 * What this value LEADS TO, where it names something with a page of its own.
		 *
		 * Worked out by `linkFor` from the server's declaration and handed in whole, never decided
		 * here: this file knows how a TYPE is drawn, and which fields are references is a fact about
		 * the registry. One prop rather than three, because the address and the picture are two
		 * readings of one answer and a surface holding half of it can draw a link with no face on it.
		 *
		 * Absent on nearly every value, which is why this changes how nothing else is drawn.
		 */
		link?: RecordLink | null;
		/**
		 * Pressing a value that reads as words copies it, the way the file name under the player
		 * does (`Copyable`). A value that leads somewhere (a chip, an address) stays a link: one
		 * press cannot both open a page and copy a word.
		 */
		copyable?: boolean;
	}

	let { kind, value, link = null, copyable = false }: Props = $props();

	const NOTHING = '—';

	/* The link marks that answered 404: the pack has no logo for that host, or there is no fetched
	   picture behind it, and the next one `linkMarks` offers is tried. With none left, the link is
	   drawn as its text alone. Remembered so the failed picture is taken OUT rather than left in place: an
	   `<img>` that failed still takes the square it was given, which reads as a gap before the words. */
	const unmarked = new SvelteSet<string>();

	/* One value a stash-box sent that Sift has no field for, as a line of text.
	 *
	 * Read-only and best-effort by design. These are shapes nothing here declared, so there is no
	 * kind to draw them by and no promise about what they look like: a number, a word, a list of
	 * objects. A list of objects is drawn by whichever of `name` and `id` it turns out to carry,
	 * which is what every stash-box shape here happens to use; anything else falls back to JSON,
	 * which is ugly and honest and never wrong.
	 */
	function plainly(what: unknown): string {
		if (what === null || what === undefined) return NOTHING;
		if (Array.isArray(what)) {
			const each = what.map((one) => plainly(one)).filter((one) => one !== NOTHING);
			return each.length > 0 ? each.join(', ') : NOTHING;
		}
		if (typeof what === 'object') {
			const held = what as Record<string, unknown>;
			// A nested object with a name inside it (`{ studio: { name } }`) reads as the name.
			for (const key of ['name', 'title', 'id']) {
				if (typeof held[key] === 'string') return held[key] as string;
			}
			const inner = Object.values(held).find((one) => one !== null && typeof one === 'object');
			return inner !== undefined ? plainly(inner) : JSON.stringify(what);
		}
		return String(what);
	}

	/* A day and a moment, in the one form every date in Sift takes (`$lib/shell/when`). A `date` field
	   is a CALENDAR day (a birthdate) and `calendarDay` builds it from its parts, because
	   parsing "1991-07-09" draws the eighth everywhere west of Greenwich. A `timestamp` is a
	   moment on a record, so it says the day and the time, and the hover gives the whole of it. */
	function seconds(held: unknown): number | null {
		if (typeof held === 'number') return held;
		const parsed = Date.parse(String(held));
		return Number.isNaN(parsed) ? null : parsed / 1000;
	}

	/*
	 * What to draw when the one definition has nothing to say.
	 *
	 * `$lib/library/facts` hands back nothing for a value it will not vouch for: a length of zero, a bit
	 * depth of zero, a negative size. On a record that is a blank row and gets the dash, the same as
	 * a field nobody has filled in: a zero length drawn as `0:00` claims the file is a film of no
	 * duration, when what it means is that nothing could be read from it.
	 *
	 * Anything that is not a number is drawn as it came instead. A value of the wrong shape is worth
	 * seeing: a dash would hide it, and hiding it is how a wrong column reads as an empty one.
	 */
	function drawn(written: string | null): string {
		if (written !== null) return written;
		return typeof value === 'number' ? NOTHING : String(value);
	}

	/** A list, whatever shape the caller had it in. Absent and empty are the same thing here. */
	const list = $derived(Array.isArray(value) ? value : []);

	const empty = $derived(
		value === null ||
			value === undefined ||
			value === '' ||
			(Array.isArray(value) && value.length === 0)
	);

	/** Everything that is one piece of text, worked out once. */
	const text = $derived.by(() => {
		if (empty) return NOTHING;
		switch (kind) {
			case 'bytes':
				return drawn(facts.size(value as number));
			case 'duration':
				return drawn(facts.length(value as number));
			case 'date':
				return calendarDay(String(value));
			case 'timestamp': {
				// Held as whole seconds, which is what every other surface reads it as.
				const at = seconds(value);
				return at === null ? String(value) : onRecord(at);
			}
			case 'rate':
				return drawn(facts.rate(value as number));
			case 'dimensions':
				return Array.isArray(value)
					? (facts.dimensions(value[0] as number, value[1] as number) ?? NOTHING)
					: String(value);
			case 'codec':
				// The encoder's own name, from the same table the stats panel reads. A record saying
				// `h264` beside a panel saying `H.264` would be one fact in two spellings.
				return facts.codec(String(value)) ?? String(value);
			case 'depth':
				return drawn(facts.depth(value as number));
			case 'word':
				// A box's constant as the word it stands for ("Blonde", never `BLONDE`) by the rule
				// the History line saying the same value uses (`$lib/entity/constant-word`).
				return constantSaid(value);
			case 'country':
				// The name, from the one list that holds them. An unknown code comes back as itself,
				// so a wrong value is visible rather than silently drawn as though it were empty.
				return countryName(value);
			case 'length':
				// Held in whole centimetres, read in whichever system this account chose. The
				// arithmetic is `$lib/shell/measure`'s, never this file's. See the header.
				return typeof value === 'number' ? height(value, appearance.units) : String(value);
			case 'year':
				// Deliberately not run through a number formatter: 1991 is a point in time, and one
				// that groups thousands draws it as "1,991".
				return String(value);
			case 'flag':
				// Yes or no, never the 0 and 1 the column keeps. A flag has two states and no third,
				// so it never reaches the dash above: `empty` is about a field nobody has filled in,
				// and this one is answered for everybody the moment the row exists.
				//
				// `false` and the number 0 both read as No, and so does the string "0": a value
				// that came back through a form as text must not read as Yes because a non-empty
				// string is truthy. Anything else is Yes.
				return value === false || value === 0 || value === '0' ? 'No' : 'Yes';
			default:
				return String(value);
		}
	});
</script>

{#if empty}
	<!-- An empty list is nothing, exactly as an empty box is. Drawn as chips with no chips in them
	     it would be a blank space beside a label, which reads as a row that failed to load rather than
	     as one nobody has filled in. -->
	<span class="unset">{NOTHING}</span>
{:else if link}
	<!--
		A value that names something, drawn as the way in to it.

		Before every other branch, because where the row leads matters more than the type it is
		stored as: a site's "Part of" is a `text` field, and the network it names is a page with a
		cover, a wall and a record. As plain text it would be a word with nothing to press.

		The same chip the file dialog draws its site under, with the same picture rule: the chosen
		cover, else the mark Sift holds for that site, else the letter. A chip and not a bare link,
		because this is a noun with a page, the one case a chip may be a link.
	-->
	<Chip tone="quiet" href={link.href} picture={entityPicture(link.kind, link.id, text)}>
		{text}
	</Chip>
{:else if kind === 'names'}
	<!-- The `<li>` is the caller's: `ChipRow` is a real list, and a `<ul>` whose children are spans
	     announces "list, 0 items" to a screen reader while looking perfectly correct. -->
	<ChipRow label="Values">
		<!-- POSITION is part of every key in this file, and it is not belt-and-braces.
		     A record's values are whatever a stash-box sent: the list is untyped, two entries
		     can be the same string, and an entry that is an OBJECT without the id the key reads
		     falls back to `String(one)`, which is "[object Object]" for every one of them. Both
		     are a duplicate key, and a duplicate key is a hard error rather than a wrong row. -->
		{#each list as one, at (`${at}:${String(one)}`)}<li><Chip>{String(one)}</Chip></li>{/each}
	</ChipRow>
{:else if kind === 'tags'}
	<ChipRow label="Tags">
		{#each list as one, at (`${at}:${String((one as { id?: string }).id ?? one)}`)}
			<li>
				<Chip href="/tags/{(one as { id: string }).id}">{(one as { name: string }).name}</Chip>
			</li>
		{/each}
	</ChipRow>
{:else if kind === 'links'}
	<ul class="links">
		{#each list as one, at (`${at}:${String((one as { id?: string; url?: string }).id ?? one)}`)}
			{@const url = String((one as { url?: string }).url ?? one)}
			{@const site = (one as { site_name?: string | null }).site_name ?? null}
			{@const mark = linkMarks(url, site).find((address) => !unmarked.has(address))}
			<li>
				<!--
					`noreferrer` as well as `noopener`. The first stops the page that opens reaching back
					through `window.opener`; the second stops this install's address being sent to the site
					as a Referer, which for a library on somebody's home network is worth not announcing.

					The site's own mark before the words, at their height, where the pack that ships with
					Sift has one for the host, else the one a download fetched for its site (`linkMarks`,
					the same answer the row under a name reads): a row of addresses reads as a row of SITES.
					Decorative (the words beside it already say which site) and gone again the moment
					it fails, so a host the pack does not know is the plain text it always was.
				-->
				<a href={url} target="_blank" rel="noopener noreferrer external">
					{#if mark}<img
							class="mark"
							src={mark}
							alt=""
							loading="lazy"
							decoding="async"
							onerror={() => unmarked.add(mark)}
						/>{/if}{site ?? url.replace(/^https?:\/\//, '')}
				</a>
			</li>
		{/each}
	</ul>
{:else if kind === 'link'}
	<!--
		One address, drawn like one entry in a links list: the same two `rel` tokens for the same
		reasons, and the scheme stripped from what is read, because a person recognises a site by
		its name, not by `https://`.

		A `<span>` rather than a bare `<a>` so the address can wrap: this is the field somebody came
		to read and copy, and a long one on a single line runs off the side of the record.

		The whole address, through the app's own tooltip rather than the browser's `title`. The line
		below is clipped (a download link is often two hundred characters of signature and expiry,
		and what anybody reads is the host and the start of the path), so the whole must be
		available somewhere. `title` is drawn by the operating system in its own typeface, shape and
		delay, and a gate refuses it.

		`aria-label` alongside, because the tooltip is visual and a screen reader should still be
		given the address rather than the shortened line.

		`shrinks`, because the line below is cut rather than wrapped: without it this wrapper
		resolves a floor of the whole address and the clipped line never reaches the width at which
		an ellipsis happens. It is the caller's flag because only the caller knows its child can
		give ground; see `Tooltip`.
	-->
	<Tooltip label={String(value)} placement="top" shrinks>
		<span class="one-link" aria-label={String(value)}>
			<a href={String(value)} target="_blank" rel="noopener noreferrer external">
				{String(value).replace(/^https?:\/\//, '')}
			</a>
		</span>
	</Tooltip>
{:else if kind === 'sources'}
	<!--
		Where the rest of the record came from. Read-only by declaration, so there is nothing to press
		here: keeping a link, asking again and forgetting one are all done from the look-up sheet,
		which is where the decision to make one was taken. Controls inside a value would put three
		buttons into every surface that draws a record.
	-->
	<ul class="sources">
		{#each list as one, at (`${at}:${String((one as { box_id?: string }).box_id ?? one)}`)}
			{@const held = one as {
				box_name?: string;
				fetched_at?: number;
				record?: { extra?: Record<string, unknown> };
			}}
			{@const said = Object.entries(held.record?.extra ?? {})}
			<li>
				<span class="where">{held.box_name ?? 'A stash-box'}</span>
				{#if held.fetched_at}
					<Tooltip label={exactly(held.fetched_at)}>
						<span class="when">asked {onRecord(held.fetched_at, { inline: true })}</span>
					</Tooltip>
				{/if}
				<!--
					What that box said and Sift has no field for.

					Under ITS name rather than under a heading of its own, because that is what makes
					it readable: three boxes will disagree about how many scenes they hold and when
					they last edited their row, and a merged list of those would be three answers to
					one question with nothing saying which was whose.

					Their key, unchanged, and this is the only place in the app where that is true.
					Everything else a stash-box says is translated at the adapter; a value with no
					field of Sift's has no word of Sift's, and inventing one would invent a meaning.
				-->
				{#if said.length > 0}
					<dl class="said">
						{#each said as [key, what] (key)}
							<div>
								<dt>{key}</dt>
								<dd>{plainly(what)}</dd>
							</div>
						{/each}
					</dl>
				{/if}
			</li>
		{/each}
	</ul>
{:else if kind === 'paragraph'}
	<p class="paragraph">{@render words(text)}</p>
{:else if kind === 'filename' || kind === 'path' || kind === 'word' || kind === 'codec'}
	<span class="exact">{@render words(text)}</span>
{:else if kind === 'timestamp' && seconds(value) !== null && !copyable}
	<!-- A moment on a record says its day; the hover gives the whole moment. Where the value copies
	     itself the tooltip is the copy's, so the day is what is drawn and what is copied. -->
	<Tooltip label={exactly(seconds(value) ?? 0)}>
		<span class="plain">{text}</span>
	</Tooltip>
{:else}
	<span class="plain">{@render words(text)}</span>
{/if}

<!-- The words of a value, pressable to copy where the caller asked for that. -->
{#snippet words(said: string)}
	{#if copyable}<Copyable text={said} />{:else}{said}{/if}
{/snippet}

<style>
	.unset,
	.plain,
	.paragraph,
	.exact {
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* An UNSET VALUE (a field nobody has filled in) and not an empty state, which is why it is
	   not called `.nothing`. Said quietly: a dash as loud as a value reads as a value. */
	.unset {
		color: var(--sift-ink-3);
	}

	/* Several lines somebody typed. Their own line breaks are kept: a paragraph re-flowed into one
	   run of text is not what was written. */
	.paragraph {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}

	/* A filename, a path, a codec. The data face, which does not re-order a right-to-left segment
	   and does not join characters into ligatures, both of which change what a filename says. */
	.exact {
		font: var(--text-data);
		overflow-wrap: anywhere;
	}

	.links {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	/* The colour and the hover are `app.css`'s, for every link in the app at once, so the download
	   link a person came to read and the list two rows up wear the same ink. Left here is the one
	   thing that is about THIS list: an address has no spaces in it and has to be allowed to break. */
	.links a {
		overflow-wrap: anywhere;
	}

	/* The site's mark, as tall as the words and on their line.

	   `1lh` is the line the words sit on, so the mark is the text's height in whichever face and size
	   the row is drawn at: a fixed pixel size would be right at one text size and wrong at the
	   others. `contain` because a logo is drawn to its own edges and cropping it cuts the logo; a
	   pack picture is square, so nothing is lost either way. A small gap after it, the same the
	   sources list uses between its words.

	   The mark sits at the START of an address that may wrap (`overflow-wrap: anywhere` above), so it
	   is an inline box on the first line rather than a flex item beside a column of text: a flex row
	   would make the address a block of its own and change where it breaks. */
	.links .mark {
		display: inline-block;
		inline-size: 1lh;
		block-size: 1lh;
		margin-inline-end: var(--space-1);
		vertical-align: top;
		object-fit: contain;
	}

	.sources {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	/* What a box said that Sift has no field for. Quieter than the record around it and indented
	   under the box that said it, because it is a footnote to that line rather than a second
	   record: the reader has already decided to look at provenance by the time they are here. */
	.said {
		margin: var(--space-1) 0 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
		gap: var(--space-1) var(--space-4);
	}

	.said dt {
		font: var(--text-micro);
		color: var(--sift-ink-3);
	}

	.said dd {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		overflow-wrap: anywhere;
	}

	.sources li {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.where {
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* When it last answered, said quietly. It is provenance about the row above it rather than a
	   fact of its own, and drawn at the same weight it reads as a second value. */
	.when {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* One address, allowed to wrap. `anywhere` and not `break-word`: an address has no spaces in it,
	   so the softer rule finds nowhere to break and the line runs off the edge anyway. */
	/*
	 * ONE line, clipped, and that is a layout decision as much as a reading one.
	 *
	 * A download address carries a signature and an expiry and runs to a couple of hundred
	 * characters. Wrapped, it would take four lines of a record cell, and pressing Edit would
	 * still move the page: a value that wraps over four lines becomes a box that is one line tall,
	 * so the row would shrink and everything under it come up. Nothing is lost: the whole address
	 * is the `href` and the tooltip.
	 */
	.one-link {
		display: block;
		min-inline-size: 0;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
