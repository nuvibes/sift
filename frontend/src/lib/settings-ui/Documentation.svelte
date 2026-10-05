<script lang="ts">
	/*
	 * The docs site's pages for this version, offline, from the data `scripts/docs_pages.js` wrote.
	 * WHY NOT SHARED: heading: a docs page's own levels, a step under the title, in the page's look.
	 */
	import { onMount, tick } from 'svelte';
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import { BackButton, Button, Empty, Note, SettingLink, Tooltip } from '$lib/components/common';
	import { copySectionLink, sectionAddress } from '$lib/docs/link';
	import type { DocBlock, DocBook, DocInline, DocLink, DocMenuItem } from '$lib/docs/read';

	type DocHeading = Extract<DocBlock, { kind: 'heading' }>;
	import { settingsPath } from './sections';
	import { COPY } from './Documentation.search';

	let book = $state<DocBook | null>(null);
	let screens = $state<Record<string, string>>({});
	let failed = $state(false);
	let shown = $state('');
	let top = $state<HTMLElement>();

	onMount(() => {
		import('$lib/generated/docs/pages').then(
			(loaded) => {
				book = loaded.BOOK;
				screens = loaded.SCREENS;
			},
			() => (failed = true)
		);
	});

	/* The section pressed again, or a search result, rewrites the address: follow it. */
	$effect(() => {
		void page.state;
		shown = new URLSearchParams(location.search).get('show') ?? '';
	});

	const open = $derived(book && shown !== '' ? (book.pages[shown] ?? null) : null);

	const addressOf = (slug: string) =>
		settingsPath({ section: 'documentation', show: slug === '' ? undefined : slug });

	/** Show a page in place, keeping its address in the bar, then bring its anchor into view. */
	async function choose(slug: string, anchor?: string) {
		shown = slug;
		try {
			replaceState(addressOf(slug), page.state);
		} catch {
			// No router yet, or a test without one: the page still changes.
		}
		await tick();
		const target =
			anchor === undefined
				? top?.closest('.section-body')
				: document.getElementById(`doc-${anchor}`);
		target?.scrollIntoView({ block: 'start' });
	}

	/** A plain click opens the page here; a modified one is left to the browser. */
	function follow(event: MouseEvent, slug: string, anchor?: string) {
		if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
			return;
		}
		event.preventDefault();
		void choose(slug, anchor);
	}

	/** The menu's loose pages gathered into one list, between the groups around them. */
	function parts(menu: DocMenuItem[]): { label?: string; items: DocMenuItem[] }[] {
		const out: { label?: string; items: DocMenuItem[] }[] = [];
		for (const item of menu) {
			const last = out.at(-1);
			if ('items' in item) out.push({ label: item.label, items: item.items });
			else if (last !== undefined && last.label === undefined) last.items.push(item);
			else out.push({ items: [item] });
		}
		return out;
	}
</script>

{#snippet runs(inline: DocInline[])}
	{#each inline as run, at (at)}
		{#if typeof run === 'string'}
			{run}
		{:else if run.kind === 'strong'}
			<strong>{run.text}</strong>
		{:else if run.kind === 'code'}
			<code>{run.text}</code>
		{:else if run.kind === 'link'}
			{@render link(run.text, run.to)}
		{/if}
	{/each}
{/snippet}

{#snippet link(text: string, to: DocLink)}
	{#if to.kind === 'setting'}
		<SettingLink section={to.section} setting={to.key}>{text}</SettingLink>
	{:else if to.kind === 'page'}
		<a href={addressOf(to.slug)} onclick={(event) => follow(event, to.slug, to.anchor)}>{text}</a>
	{:else if to.kind === 'place'}
		<a href={to.path}>{text}</a>
	{:else}
		<a href={to.href} target="_blank" rel="noopener noreferrer external">{text}</a>
	{/if}
{/snippet}

{#snippet also(anchors: string[] | undefined)}
	{#each (anchors ?? []).slice(1) as anchor (anchor)}<span id="doc-{anchor}"></span>{/each}
{/snippet}

{#snippet heading(block: DocHeading)}
	{@render also(block.anchors)}{@render runs(block.inline)}
	<span class="copy">
		<Tooltip label="Copy link">
			<Button
				tone="ghost"
				size="small"
				icon="link"
				aria-label="Copy link"
				onclick={() =>
					void copySectionLink(sectionAddress(addressOf(shown), block.anchors[0], location.origin))}
			/>
		</Tooltip>
	</span>
{/snippet}

{#snippet blocks(list: DocBlock[])}
	{#each list as block, at (at)}
		{#if block.kind === 'heading' && block.level === 2}
			<h3 class="doc-heading" data-level="2" id="doc-{block.anchors[0]}">
				{@render heading(block)}
			</h3>
		{:else if block.kind === 'heading'}
			<h4 class="doc-heading" data-level="3" id="doc-{block.anchors[0]}">
				{@render heading(block)}
			</h4>
		{:else if block.kind === 'paragraph'}
			<p id={block.anchors ? `doc-${block.anchors[0]}` : undefined}>
				{@render also(block.anchors)}{@render runs(block.inline)}
			</p>
		{:else if block.kind === 'list'}
			<svelte:element this={block.ordered ? 'ol' : 'ul'}>
				{#each block.items as item, row (row)}
					<li id={item.anchors ? `doc-${item.anchors[0]}` : undefined}>
						{@render also(item.anchors)}{@render runs(item.inline)}
					</li>
				{/each}
			</svelte:element>
		{:else if block.kind === 'note'}
			<div class="doc-note"><Note>{@render runs(block.inline)}</Note></div>
		{:else if block.kind === 'code'}
			<pre><code>{block.text}</code></pre>
		{:else if block.kind === 'image'}
			<img class="screen" src={screens[block.screen]} alt={block.alt} loading="lazy" />
		{:else}
			<table>
				<thead>
					<tr>
						{#each block.head as cell, column (column)}<th>{@render runs(cell)}</th>{/each}
					</tr>
				</thead>
				<tbody>
					{#each block.rows as cells, row (row)}
						<tr>
							{#each cells as cell, column (column)}<td>{@render runs(cell)}</td>{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	{/each}
{/snippet}

{#snippet contents(whole: DocBook)}
	{@render blocks(whole.home)}
	<nav class="doc-menu" aria-label={COPY.contents}>
		{#each parts(whole.menu) as part, at (at)}
			{#if part.label}<h2 class="doc-heading" data-level="2">{part.label}</h2>{/if}
			{@render entries(part.items)}
		{/each}
	</nav>
{/snippet}

{#snippet entries(items: DocMenuItem[])}
	<ul>
		{#each items as item, at (at)}
			{#if 'items' in item}
				<li class="group">
					<h3 class="doc-heading" data-level="3">{item.label}</h3>
					{@render entries(item.items)}
				</li>
			{:else}
				<li>
					<a href={addressOf(item.slug)} onclick={(event) => follow(event, item.slug)}
						>{item.title}</a
					>
				</li>
			{/if}
		{/each}
	</ul>
{/snippet}

<div class="docs" bind:this={top}>
	{#if failed}
		<Empty scope="block">{COPY.failed}</Empty>
	{:else if book !== null && open !== null}
		<BackButton label={COPY.contents} onback={() => void choose('')} />
		<h2 class="doc-title">{open.title}</h2>
		{@render blocks(open.blocks)}
	{:else if book !== null}
		{#if shown !== ''}<Empty scope="block">{COPY.missing}</Empty>{/if}
		{@render contents(book)}
	{/if}
</div>

<style>
	.docs {
		overflow-wrap: anywhere;
	}

	.docs :global([id^='doc-']) {
		scroll-margin-block-start: var(--space-6);
	}

	.doc-title,
	.doc-heading {
		max-inline-size: var(--reading-measure);
		letter-spacing: normal;
		text-wrap: wrap;
		color: var(--sift-ink);
	}

	.doc-title {
		margin: var(--space-4) 0 var(--space-3);
		font: var(--text-display);
	}

	.doc-heading {
		margin: var(--space-6) 0 var(--space-2);
	}

	/* A zero-height box, so the press never makes the heading's line taller. */
	.copy {
		display: inline-flex;
		align-items: center;
		block-size: 0;
		vertical-align: middle;
		margin-inline-start: var(--space-1);
	}

	.doc-heading[data-level='2'] {
		font: var(--text-h2);
	}

	.doc-heading[data-level='3'] {
		margin-block-start: var(--space-4);
		font: var(--text-h3);
	}

	p,
	ul,
	ol,
	pre,
	table,
	.doc-note {
		margin-block: 0 var(--space-3);
	}

	ul,
	ol {
		padding-inline-start: var(--space-5);
		max-inline-size: var(--reading-measure);
	}

	li + li {
		margin-block-start: var(--space-1);
	}

	.doc-menu ul {
		list-style: none;
		padding-inline-start: 0;
	}

	.doc-menu .group > ul {
		padding-inline-start: var(--space-4);
	}

	pre {
		white-space: pre-wrap;
		padding: var(--space-3);
		background: var(--sift-card-fill);
	}

	.screen {
		display: block;
		max-inline-size: 100%;
		margin-block: var(--space-2) var(--space-4);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
	}

	table {
		border-collapse: collapse;
	}

	th,
	td {
		padding: var(--space-2) var(--space-3);
		border-block-end: 1px solid var(--sift-line);
		text-align: start;
		vertical-align: top;
	}

	th {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}
</style>
