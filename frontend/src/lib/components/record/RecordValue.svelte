<script lang="ts">
	/*
	 * One value, drawn the way its declared type says. The arithmetic is `$lib/library/facts` and
	 * `$lib/shell/measure`; the units preference is read here so no surface can forget it. A
	 * missing value is a dash, never a blank.
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
		 * Where it names something with a page of its own (`linkFor`); the address and the picture
		 * together.
		 */
		link?: RecordLink | null;
		/** A value that leads somewhere stays a link: one press cannot open and copy. */
		copyable?: boolean;
	}

	let { kind, value, link = null, copyable = false }: Props = $props();

	const NOTHING = '—';

	/* Link marks that answered 404, taken out so a failed `<img>` leaves no gap. */
	const unmarked = new SvelteSet<string>();

	/*
	 * A value a stash-box sent with no field here: by `name` or `id`, else JSON, which is honest.
	 */
	function plainly(what: unknown): string {
		if (what === null || what === undefined) return NOTHING;
		if (Array.isArray(what)) {
			const each = what.map((one) => plainly(one)).filter((one) => one !== NOTHING);
			return each.length > 0 ? each.join(', ') : NOTHING;
		}
		if (typeof what === 'object') {
			const held = what as Record<string, unknown>;
			for (const key of ['name', 'title', 'id']) {
				if (typeof held[key] === 'string') return held[key] as string;
			}
			const inner = Object.values(held).find((one) => one !== null && typeof one === 'object');
			return inner !== undefined ? plainly(inner) : JSON.stringify(what);
		}
		return String(what);
	}

	/* A `date` is a CALENDAR day (`calendarDay`); a `timestamp` a moment, held in seconds. */
	function seconds(held: unknown): number | null {
		if (typeof held === 'number') return held;
		const parsed = Date.parse(String(held));
		return Number.isNaN(parsed) ? null : parsed / 1000;
	}

	/* A dash where `facts` will not vouch for a number; anything else is drawn as it came. */
	function drawn(written: string | null): string {
		if (written !== null) return written;
		return typeof value === 'number' ? NOTHING : String(value);
	}

	const list = $derived(Array.isArray(value) ? value : []);

	const empty = $derived(
		value === null ||
			value === undefined ||
			value === '' ||
			(Array.isArray(value) && value.length === 0)
	);

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
				// From the table the stats panel reads, so one fact has one spelling.
				return facts.codec(String(value)) ?? String(value);
			case 'depth':
				return drawn(facts.depth(value as number));
			default:
				return wordedText();
		}
	});

	function wordedText(): string {
		switch (kind) {
			case 'word':
				// "Blonde", never `BLONDE` (`$lib/entity/constant-word`).
				return constantSaid(value);
			case 'country':
				// An unknown code comes back as itself, so a wrong value shows.
				return countryName(value);
			case 'length':
				return typeof value === 'number' ? height(value, appearance.units) : String(value);
			case 'year':
				// Not a number formatter, which would draw 1991 as "1,991".
				return String(value);
			case 'flag':
				// A flag is answered for everybody, never the dash; `false`, 0 and "0" are No.
				return value === false || value === 0 || value === '0' ? 'No' : 'Yes';
			default:
				return String(value);
		}
	}
</script>

{#if empty}
	<!-- An empty list is nothing, as an empty box is. -->
	<span class="unset">{NOTHING}</span>
{:else if link}
	<!--
	First: where the row leads matters more than its stored type. A chip, a noun with a page.
	-->
	<Chip tone="quiet" href={link.href} picture={entityPicture(link.kind, link.id, text)}>
		{text}
	</Chip>
{:else if kind === 'names'}
	<!-- The `<li>` is the caller's: `ChipRow` is a real list. -->
	<ChipRow label="Values">
		<!--
		POSITION in every key: a stash-box's list may repeat a string, and a duplicate key is a hard
		error.
		-->
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
				`noreferrer` keeps this install's address from the site. The site's mark before the
				words,
				decorative and dropped when it fails (`linkMarks`).
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
	One address, wrapping inside a `<span>`; the whole of it in the app's tooltip (`title` is
	refused)
	and `aria-label`. `shrinks`, so the clipped line can reach its ellipsis.
	-->
	<Tooltip label={String(value)} placement="top" shrinks>
		<span class="one-link" aria-label={String(value)}>
			<a href={String(value)} target="_blank" rel="noopener noreferrer external">
				{String(value).replace(/^https?:\/\//, '')}
			</a>
		</span>
	</Tooltip>
{:else if kind === 'sources'}
	<!-- Read-only by declaration: the look-up sheet is where links are kept and forgotten. -->
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
				Under ITS box's name and key, untranslated: a value with no field of Sift's has no
				word of Sift's.
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
	<Tooltip label={exactly(seconds(value) ?? 0)}>
		<span class="plain">{text}</span>
	</Tooltip>
{:else}
	<span class="plain">{@render words(text)}</span>
{/if}

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

	/* An UNSET VALUE, said quietly. */
	.unset {
		color: var(--sift-ink-3);
	}

	.paragraph {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}

	/* The data face: no ligatures, no reordering of a filename. */
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

	/* Ink and hover are `app.css`'s; an address has no spaces, so it may break anywhere. */
	.links a {
		overflow-wrap: anywhere;
	}

	/*
	 * The site's mark, `1lh` tall, inline on the first line so the address still wraps as before.
	 */
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

	/* A footnote to the box's line, quieter and indented. */
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

	.when {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* ONE line, clipped: a wrapped download address would move the page on Edit. */
	.one-link {
		display: block;
		min-inline-size: 0;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
