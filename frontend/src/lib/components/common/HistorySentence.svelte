<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'HistorySentence',
		category: 'composition',
		role: 'one History line as the server built it, its named things as ways to them, and what it opens to',
		basis: 'composes:AssetLink,Button,Chip',
		states: [
			'plain words',
			'names something',
			'a thing that has gone',
			'folded list',
			'show each',
			'a way out'
		]
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing here for a component library to own. It is runs of text,
	   links, one Button that opens the rest of a list, and the site's own `<details>`. */

	/*
	 * ONE HISTORY LINE, DRAWN FROM ITS PIECES: the same on every screen that says what happened.
	 *
	 * NOT ON THE GALLERY: it is drawn inside `HistoryRow`'s specimens, which show every state it has.
	 *
	 * The server builds every History line as pieces: plain words, and words that stand for a thing,
	 * each placed where it sits (`kernel/access/sentences.py`). A file's thread, a person's, a tag's,
	 * a Site's, a Collection's and a Photo Set's, and the Settings feed (its Decisions too) all hand the
	 * same pieces over, and this draws them. It builds NOTHING: no word table, no search for a name
	 * inside a sentence: that search would link every "d" in "Added to d".
	 *
	 * A FILE is drawn by `AssetLink` and every other thing by a plain anchor: `/asset/{id}` run as a
	 * route tears down the screen a history is drawn inside. A thing that has GONE is struck through
	 * and goes nowhere. A FOLD ("and 14 more") opens the rest of its list in place; the rest is more
	 * of the same list, so it is not put under "Show each".
	 *
	 * "Show each" is what a line STANDS FOR and does not name: the seventeen people one stash-box
	 * named in a press, in groups, each headed by the words the line counted it in, each thing the
	 * chip the file's own band draws, with its cover.
	 */
	import AssetLink from './AssetLink.svelte';
	import Button from './Button.svelte';
	import Chip from './Chip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		entityKindOf,
		hrefOf,
		hrefOfPiece,
		markOfLinkKind,
		type HistoryDetail,
		type HistoryLink,
		type HistoryPiece
	} from './history';
	import { entityPicture } from '$lib/entity/entity-picture';
	/* A line naming a setting ("You changed Log detail") links to its row, and the link opens the
	   settings panel the way every settings link in the app does (`SettingLink`) rather than as a
	   page load (`followSettingsLink`). */
	import { followSettingsLink as followSetting } from '$lib/settings-ui/settings-link';

	interface Props {
		pieces: readonly HistoryPiece[];
		/**
		 * The line as plain words, drawn only where there are no pieces: a reply from a build that
		 * sent the sentence and not its pieces. Nothing is linked then, which is the honest drawing:
		 * there is no way here to know where a name sits without searching for it.
		 */
		what?: string;
		/** What the line stands for and does not name, in groups. Absent or empty: no "Show each". */
		detail?: readonly HistoryDetail[];
		/** A line that no longer stands takes its links down with it. */
		quiet?: boolean;
		/**
		 * Where what the line is about lives OUTSIDE Sift: "Open on PMVStash" and the stash-box's
		 * own page for the scene it matched. Opened in a new tab, beside "Show each".
		 */
		away?: { label: string; href: string } | null;
	}

	let { pieces, what = '', detail = [], quiet = false, away = null }: Props = $props();

	/* The pieces, or the plain words where a reply sent none. */
	const drawn = $derived(
		pieces?.length
			? pieces
			: what
				? [{ text: what, kind: null, id: null, href: null, gone: false, rest: [], lead: '' }]
				: []
	);

	/* Whether the folded rest of the list has been opened. Forgotten with the line. */
	let opened = $state(false);
</script>

<!-- A thing inside a "Show each" group, as the chip the file dialog's band draws it as. -->
{#snippet entry(one: HistoryLink)}
	{@const href = hrefOf(one)}
	{@const kind = entityKindOf(one.kind)}
	{@const picture = kind ? entityPicture(kind, one.id, one.name) : undefined}
	{#if one.kind === 'asset' && href && !one.gone}
		<Chip tone="quiet" {picture}><AssetLink id={one.id}>{one.name}</AssetLink></Chip>
	{:else if href && !one.gone}
		<Chip tone="quiet" {href} {picture}>{one.name}</Chip>
	{:else}
		<Chip tone="quiet" {picture}>{one.name}</Chip>
	{/if}
{/snippet}

<!-- The runs of a line, and the rest of a folded list by the same rule. On one line on purpose: a
     line break between the runs would be a space in the words. -->
{#snippet runs(list: readonly HistoryPiece[])}{#each list as piece, index (index)}{@const href =
			quiet ? null : hrefOfPiece(piece)}{#if piece.rest.length > 0}{#if opened}{@render runs(
					piece.rest
				)}{:else}{piece.lead}<Button
					tone="link"
					size="small"
					aria-expanded="false"
					onclick={() => (opened = true)}>{piece.text}</Button
				>{/if}{:else if piece.kind !== null && piece.gone}<s class="gone">{piece.text}</s
			>{:else if piece.kind === 'asset' && href}<AssetLink id={piece.id ?? ''}
				>{piece.text}</AssetLink
			>{:else if href}<a class="named" {href} onclick={(event) => followSetting(event, href)}
				>{piece.text}</a
			>{:else}{piece.text}{/if}{/each}{/snippet}

<span class="line" class:quiet>{@render runs(drawn)}</span>
{#if detail.length > 0}
	<details class="each">
		<summary>Show each</summary>
		{#each detail as group, index (index)}
			{@const mark = markOfLinkKind(group.kind)}
			<div class="group">
				<p class="heading">
					{#if mark}
						<span class="kind"><Icon name={mark} size={16} /></span>
					{/if}
					{group.words}
				</p>
				<ul>
					{#each group.entries as one, at (at)}
						<li>{@render entry(one)}</li>
					{/each}
				</ul>
			</div>
		{/each}
	</details>
{/if}
{#if away && !quiet}
	<!-- Somebody else's page, so a new tab: `noopener` so it cannot reach back into this one,
	     `noreferrer` so the address of this library is not handed to the box. -->
	<a class="away" href={away.href} target="_blank" rel="noopener noreferrer external"
		>{away.label}</a
	>
{/if}

<style>
	/* A name in a line, drawn as the way to the thing it names: the accent's TEXT colour at rest,
	   underlined under the pointer and the keyboard. */
	.named {
		color: var(--sift-accent-text);
		text-decoration: none;
		text-underline-offset: 2px;
		border-radius: var(--radius-sm);
		transition: color var(--dur-instant) var(--ease);
	}

	.named:hover,
	.named:focus-visible {
		text-decoration: underline;
	}

	/* A thing that has gone: struck through by the element, quiet ink, never a link. */
	.gone {
		color: var(--sift-ink-3);
	}

	/* A line that was undone takes its links down with it. */
	.line.quiet .named {
		color: inherit;
	}

	.each {
		display: block;
		margin-block-start: var(--space-1);
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.each summary {
		cursor: pointer;
		border-radius: var(--radius-sm);
		color: var(--sift-ink-2);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition:
			color var(--dur-instant) var(--ease),
			text-decoration-color var(--dur-instant) var(--ease);
	}

	.each summary:hover {
		color: var(--hover-ink);
		text-decoration-color: currentColor;
	}

	/* The way out, in the quiet label face "Show each" wears: a second thing the line opens to. */
	.away {
		display: inline-block;
		margin-block-start: var(--space-1);
		border-radius: var(--radius-sm);
		font: var(--text-label);
		color: var(--sift-ink-2);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition:
			color var(--dur-instant) var(--ease),
			text-decoration-color var(--dur-instant) var(--ease);
	}

	.away:hover,
	.away:focus-visible {
		color: var(--hover-ink);
		text-decoration-color: currentColor;
	}

	.group {
		margin-block-start: var(--space-2);
	}

	.group + .group {
		margin-block-start: var(--space-3);
	}

	.heading {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		color: var(--sift-ink-3);
	}

	.kind {
		display: flex;
		align-items: center;
	}

	.each ul {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin: var(--space-1) 0 0;
		padding: 0;
		list-style: none;
	}
</style>
