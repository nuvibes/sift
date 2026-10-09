<script lang="ts" module>
	import type { DownloadItem as Item } from './queue.svelte';

	/** What the detail can reach: which of the page's handlers it was handed. */
	export interface DetailCan {
		cookies?: boolean;
		retry?: boolean;
		resume?: boolean;
	}

	/** The fixes the opened detail offers for a download, by the ids the row's verbs use. */
	export function detailOffers(
		item: Item,
		sentence: string | null | undefined,
		can: DetailCan
	): Set<string> {
		const offers = new Set<string>();
		if (item.status === 'paused' && can.resume) offers.add('resume');
		const failing = item.status === 'failed' || item.status === 'blocked';
		if (failing && sentence) {
			if (item.status === 'blocked' && can.cookies && (item.site_key ?? item.site)) {
				offers.add('cookies');
			}
			if (can.retry) offers.add('retry');
		}
		return offers;
	}
</script>

<script lang="ts">
	/* Everything about one download that is not worth a column. */
	import { AssetLink, Button, Problem } from '$lib/components/common';
	import { whenText } from '$lib/components/common';
	import { copyText } from '$lib/shell/clipboard';
	import ExitAddress from '$lib/settings-ui/ExitAddress.svelte';
	import { addressPhrase } from '$lib/settings-ui/tunnels-state.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { destinationWord, type DownloadItem } from './queue.svelte';
	import { api, type ApiPath } from '$lib/api/client';
	import type { components } from '$lib/api/schema';

	interface Props {
		item: DownloadItem;
		now: number;
		/** Why it failed, in words, chosen from the error code on the server. */
		sentence?: string | null;
		/** The same failure as the machine said it, for a bug report and for a search. */
		raw?: string | null;
		oncookies?: (site: string) => void;
		onretry?: (id: string) => void;
		/** Let a held download go again, from where it stopped. */
		onresume?: (id: string) => void;
		onopen?: (assetId: string) => void;
		/** The file this produced has since been deleted. Nothing here may lead anywhere. */
		gone?: boolean;
	}

	let { item, sentence, raw, oncookies, onretry, onresume, onopen, gone = false }: Props = $props();

	/* Everything one paste put in the library, for a download that produced more than one file:
	   a gallery, a playlist. */
	type DownloadFiles = components['schemas']['DownloadFiles'];
	type DownloadFile = DownloadFiles['files'][number];
	const manyFiles = $derived(item.status === 'done' && (item.progress?.total_files ?? 0) > 1);
	let files = $state<DownloadFile[] | null>(null);
	$effect(() => {
		if (!manyFiles) return;
		const id = item.id;
		api.get<DownloadFiles>(`/downloads/${id}/files` as ApiPath).then(
			(page) => {
				files = page.files;
			},
			() => {
				files = [];
			}
		);
	});

	const failing = $derived(item.status === 'failed' || item.status === 'blocked');

	const offers = $derived(
		detailOffers(item, sentence, {
			cookies: oncookies !== undefined,
			retry: onretry !== undefined,
			resume: onresume !== undefined
		})
	);

	/** Held by somebody, with what was fetched still on disk. */
	const held = $derived(item.status === 'paused');

	/* What the failure COST, which the sentence does not say. */
	const consequence = $derived(
		item.status === 'blocked'
			? 'Nothing will be downloaded from this Site until Sift has cookies for it.'
			: "Nothing was downloaded. There's no file in your library from this link."
	);

	const shownUrl = $derived(item.shown_url ?? item.url ?? '');

	/* WHOSE IP IT USED: your own, or a tunnel's. One label and its value. */
	const tunnel = $derived(item.via && item.via.toLowerCase() !== 'direct' ? item.via : null);

	/* The download folder, by name and by path: the server's answer, `folder` on the row. */
	const folder = $derived(item.folder ?? null);

	const named = $derived(item.filename ?? item.remembered_filename ?? '');

	/* What the folder line is called: "Saving to" while it is saving, "Saved to" once it landed,
	   "Save to" otherwise. */
	const toFolder = $derived(destinationWord(item.status));

	function copyDetails() {
		const lines = [sentence, raw, item.url].filter(Boolean).join('\n');
		void copyText(lines).then((landed) => {
			if (landed) toasts.show('Copied', { tone: 'success' });
		});
	}
</script>

<div class="detail">
	{#if held}
		<!-- A held download, and the one question it raises answered before anything else. -->
		<div class="held">
			<p class="consequence">Paused. What is downloaded so far is kept.</p>
			<div class="fixes">
				{#if offers.has('resume')}
					<Button size="small" tone="primary" icon="play_arrow" onclick={() => onresume?.(item.id)}
						>Resume</Button
					>
				{/if}
			</div>
		</div>
	{/if}

	{#if failing && sentence}
		<div class="failure">
			<!-- The app's own failure line, so a download that failed looks like every other thing
			     in Sift that failed rather than like this screen's idea of one. -->
			<Problem message={sentence} />
			<p class="consequence">{consequence}</p>
			{#if raw}
				<!-- Shown, not hidden: it is what a bug report needs and what a search matches on
				     when the wording of the sentence above changes. -->
				<p class="raw">{raw}</p>
			{/if}
			<div class="fixes">
				{#if offers.has('cookies')}
					<Button
						size="small"
						tone="primary"
						icon="cookie"
						onclick={() => oncookies?.(item.site_key ?? item.site ?? '')}
					>
						Add cookies
					</Button>
				{/if}
				{#if offers.has('retry')}
					<Button size="small" tone="secondary" icon="sync" onclick={() => onretry?.(item.id)}>
						Try again
					</Button>
				{/if}
				<Button size="small" tone="quiet" onclick={copyDetails}>Copy the details</Button>
			</div>
		</div>
	{/if}

	<dl class="facts">
		{#if shownUrl}
			<dt>Link</dt>
			<dd>
				{#if gone || !item.url}
					<!--
						Not a link once the file is gone: the address is what it WAS fetched from, and
						offering to open it reads as offering the file back.
					-->
					<span class="address gone">{shownUrl}</span>
				{:else}
					<!-- The address, as it was pasted, and it opens. -->
					<a
						class="address"
						href={item.url}
						target="_blank"
						rel="noreferrer noopener"
						aria-label="Open this address in a new tab">{shownUrl}</a
					>
				{/if}
			</dd>
		{/if}

		<dt>Site</dt>
		<dd>
			{#if item.site_id && !gone}
				<a class="plain" href={`/sites/${item.site_id}`}>{item.site ?? item.site_name}</a>
			{:else}
				{item.site ?? item.site_name ?? 'Not recognized'}
			{/if}
		</dd>

		{#if tunnel}
			<!-- Two facts, each under its own true label: the tunnel by name, and the address of the
			     SERVER it went out through. Under the tunnel's name alone that address would read as
			     the address the site saw, which a provider may send out of a different machine. -->
			<dt>Tunnel</dt>
			<dd>{tunnel}</dd>
			{#if item.via_address}
				<dt>Tunnel server</dt>
				<dd>
					<span class="via">
						<ExitAddress address={item.via_address} what={addressPhrase(tunnel, 'then')} />
					</span>
				</dd>
			{/if}
		{:else}
			<dt>IP used</dt>
			<dd>{item.via ? 'Your own' : 'Not recorded'}</dd>
		{/if}

		<!-- Past tense once there is a file there, present tense only while bytes are moving, and
		     the folder it WOULD use for everything else: a failed or waiting download is not
		     saving anything. -->
		<dt>{toFolder}</dt>
		<dd>
			{#if folder}
				<!-- The name, then where it is on disk: two folders can share a name, and the path is
				     what somebody types into their own file manager to go and look. -->
				<span class="folder">{folder.name}</span>
				<span class="where">{folder.path}</span>
			{:else}
				No download folder
			{/if}
		</dd>

		<dt>Added</dt>
		<dd>{whenText(item.created_at)}</dd>

		{#if manyFiles && files && files.length > 0}
			<dt>Files</dt>
			<dd>
				<ul class="files">
					{#each files as file (file.asset_id)}
						<li>
							{#if onopen}
								<Button tone="link" onclick={() => onopen?.(file.asset_id)}
									>{file.filename ?? file.asset_id}</Button
								>
							{:else}
								<AssetLink id={file.asset_id}>{file.filename ?? file.asset_id}</AssetLink>
							{/if}
						</li>
					{/each}
				</ul>
			</dd>
		{/if}

		{#if named}
			<dt>File</dt>
			<dd>
				{#if item.asset_id && !gone}
					<!-- Over THIS page, through `AssetLink`. -->
					{#if onopen}
						<Button tone="link" onclick={() => onopen?.(item.asset_id ?? '')}>{named}</Button>
					{:else}
						<AssetLink id={item.asset_id}>{named}</AssetLink>
					{/if}
				{:else}
					<span class="gone">{named}</span>
				{/if}
			</dd>
		{/if}
	</dl>
</div>

<style>
	/* Indented past the mark so it reads as belonging to the row above it rather than as a row
	   of its own. */
	.detail {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin-block-end: var(--space-2);
		padding: var(--space-3) var(--space-4);
		/* The card's light (see `--sift-card`). It has no border, so the fill is the whole of it. */
		background: var(--sift-card);
		border-radius: var(--radius-md);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.failure,
	.held {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.consequence,
	.raw {
		margin: 0;
	}

	.raw {
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/* Buttons on the right of what they are about, which is this card. */
	.fixes {
		display: flex;
		justify-content: flex-end;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	/* Term and value in two columns, so every value starts in the same place and the pairs read
	   as pairs rather than as a run of sentences. */
	.facts {
		display: grid;
		grid-template-columns: max-content 1fr;
		/* On one line of print: the term is the smaller type, so aligned by its top it would sit
		   above the value it names. */
		align-items: baseline;
		gap: var(--space-1) var(--space-3);
		margin: 0;
	}

	.facts dt {
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	.facts dd {
		margin: 0;
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}

	/* The tunnel's name and the server it went out through, side by side and wrapping as a pair:
	   the address never splits, it is one control. */
	.via {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/* The folder's name on its own line and its path under it, quieter: the name is what is
	   read, the path is what tells two of one name apart. */
	.folder,
	.where {
		display: block;
	}

	.where {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* A file that is gone: struck through and never a link. There is nothing at the end of it. */
	.gone {
		text-decoration: line-through;
		color: var(--sift-ink-3);
	}

	/* The ink steps over --dur-instant rather than snapping. */
	.address {
		color: var(--sift-ink-3);
		text-decoration: none;
		transition: color var(--dur-instant) var(--ease);
	}

	.address:hover {
		color: var(--sift-accent);
		text-decoration: underline;
	}

	.address:focus-visible,
	.plain:focus-visible {
		outline: none;
		border-radius: var(--radius-sm);
		box-shadow: var(--focus-ring);
	}

	.plain {
		color: inherit;
		text-decoration: none;
		transition: color var(--dur-instant) var(--ease);
	}

	.plain:hover {
		color: var(--sift-ink);
		text-decoration: underline;
	}
	.files {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: var(--space-1);
	}

	/* A downloaded name is often one unbroken token (a site's id and an extension), and at a
	   phone's width it would run past the detail's edge; it breaks anywhere to stay in the box. */
	.files :global(:is(.btn.link, a)) {
		overflow-wrap: anywhere;
		text-align: start;
	}

	/* On a phone the album's files are a list of rows a finger tall: packed close, each name's
	   reach would run into the next name and a press between two would open the lower one. */
	@media (max-width: 767px) {
		.files li {
			display: flex;
			align-items: center;
			min-block-size: var(--touch-target);
		}

		/* And the list stands clear of the File link under it, whose reach is a ring too. */
		.files {
			margin-block-end: var(--space-3);
		}
	}
</style>
