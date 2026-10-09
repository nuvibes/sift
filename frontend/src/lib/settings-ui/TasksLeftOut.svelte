<script lang="ts">
	/* The files a product gave up on, on a page of their own: each by its name, which opens it, with
	   why in Sift's words (the wall's own `left_out` filter answers both). The press is the caller's:
	   the count on an Import task's row, the pill on an Activity row. */
	import type { Snippet } from 'svelte';
	import type { components } from '$lib/api/schema';
	import { api } from '$lib/api/client';
	import { Button, DataRow, DataRows, Problem, Skeleton } from '$lib/components/common';
	import type { ButtonTone } from '$lib/components/common';
	import { leftOutWall } from '$lib/library/left-out';
	import { openAssetInstead } from '$lib/player/asset-view';
	import { drilldown } from './drilldown.svelte';
	import { COPY } from './Importing.search';

	type Page = components['schemas']['AssetPageResponse'];
	type File = { product: string; id: string; name: string; why: string };

	interface Props {
		/** Each product whose files are listed, with its name for the line under a file. */
		products: readonly { key: string; label: string }[];
		/** What the page is about: a product, or a task of several. */
		title: string;
		tone?: ButtonTone;
		children: Snippet;
	}

	let { products, title, tone = 'link', children }: Props = $props();

	/* LIVE: nothing moves it (read each time the page opens; the count that opens it is re-read) */
	/* Enough to read down; the rest are on the wall the filter draws, a press away. */
	const SHOWN = 100;

	let files = $state<File[] | null>(null);
	let more = $state<{ key: string; total: number }[]>([]);
	let failed = $state(false);

	async function open(): Promise<void> {
		drilldown.open(COPY.leftOutPage(title), page, COPY.leftOutDoor);
		[files, more, failed] = [null, [], false];
		try {
			const pages = await Promise.all(
				products.map((one) =>
					api.get<Page>('/assets', { query: { left_out: one.key, limit: SHOWN } })
				)
			);
			files = pages.flatMap((answer, at) =>
				answer.items.map((file) => ({
					product: products[at].key,
					id: file.id,
					name: file.original_filename ?? COPY.aFile,
					why: lineOf(products[at].label, file.left_out)
				}))
			);
			more = pages
				.map((answer, at) => ({ key: products[at].key, total: answer.total }))
				.filter((one) => one.total > SHOWN);
		} catch {
			failed = true;
		}
	}

	function lineOf(label: string, why: string | null | undefined): string {
		const said = why ?? COPY.noReason;
		return products.length > 1 ? `${label}: ${said}` : said;
	}
</script>

<Button {tone} size="small" onclick={() => void open()}>{@render children()}</Button>

{#snippet page()}
	{#if failed}
		<Problem message={COPY.leftOutCannotLoad} />
	{:else if files === null}
		<Skeleton lines={3} />
	{:else}
		<DataRows items={files} key={(one: File) => `${one.product}:${one.id}`} label={title} edges>
			{#snippet row(one: File)}
				<DataRow>
					<span class="subject">
						<a
							class="what"
							href="/asset/{one.id}"
							onclick={(event) => openAssetInstead(event, one.id)}>{one.name}</a
						>
						<span class="why">{one.why}</span>
					</span>
				</DataRow>
			{/snippet}
		</DataRows>
		{#each more as one (one.key)}
			<p class="more"><a href={leftOutWall(one.key)}>{COPY.leftOutAll(one.total)}</a></p>
		{/each}
	{/if}
{/snippet}

<style>
	/* The Task Queue's failed row: the file, and why under it, one step down. */
	.subject {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.what {
		color: var(--sift-ink);
		font: var(--text-body);
		overflow-wrap: anywhere;
		text-decoration: none;
	}

	.what:hover,
	.what:focus-visible {
		text-decoration: underline;
	}

	.why {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.more {
		margin: var(--space-3) 0 0;
		font: var(--text-body-sm);
	}
</style>
