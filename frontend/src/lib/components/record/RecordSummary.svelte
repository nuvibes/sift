<script lang="ts">
	/*
	 * The few facts under the name, unlabelled, that the server marks as on the record;
	 * `RecordView` draws the rest. Facts needing a label stand in `RecordFacts`.
	 */
	import { Chip, ChipRow } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import { linkMarks } from '$lib/entity/entity-picture';
	import { SvelteSet } from 'svelte/reactivity';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { fields, linkFor, type RecordSubject } from '$lib/entity/records.svelte';

	interface Props {
		subject: RecordSubject;
		values: Record<string, unknown>;
		label: string;
	}

	let { subject, values, label }: Props = $props();

	const shown = $derived(fields.onRecord(subject));

	function listOf(key: string): unknown[] {
		const held = values[key];
		return Array.isArray(held) ? held : [];
	}

	function textOf(one: unknown): string {
		return typeof one === 'string' ? one : String((one as { alias?: string }).alias ?? one);
	}

	function urlOf(one: unknown): string {
		return typeof one === 'string' ? one : String((one as { url?: string }).url ?? one);
	}

	function siteOf(one: unknown): string | null {
		if (typeof one === 'string') return null;
		return (one as { site_name?: string | null }).site_name ?? null;
	}

	/* The accessible NAME of the link: the host, never a tracking parameter. */
	function hostOf(url: string): string {
		try {
			return new URL(url).hostname.replace(/^www\./, '');
		} catch {
			return url.replace(/^https?:\/\//, '');
		}
	}

	/* Pressing a name copies it (`copyText`); held by its place, since a name may repeat. */
	let copiedAt = $state<string | null>(null);
	let copiedFor: ReturnType<typeof setTimeout> | null = null;
	const COPIED_MS = 1600;

	async function copyName(place: string, text: string): Promise<void> {
		if (!(await copyText(text))) {
			toasts.show("That name couldn't be copied", { tone: 'error' });
			return;
		}
		if (copiedFor) clearTimeout(copiedFor);
		copiedAt = place;
		copiedFor = setTimeout(() => (copiedAt = null), COPIED_MS);
	}

	/* Pictures that answered 404, shared across the row. */
	const failed = new SvelteSet<string>();

	/** `linkMarks`, else the plain link glyph. */
	function markOf(one: unknown): string | null {
		return linkMarks(urlOf(one), siteOf(one)).find((address) => !failed.has(address)) ?? null;
	}
</script>

{#if shown.length > 0}
	<div class="summary" aria-label={label}>
		{#each shown as one (one.key)}
			{#if one.kind === 'names'}
				{#if listOf(one.key).length > 0}
					<ChipRow label={one.label}>
						<!-- POSITION in the key: a field can hold one value twice. -->
						{#each listOf(one.key) as entry, at (`${at}:${textOf(entry)}`)}
							{@const place = `${one.key}:${at}`}
							<li class="alias">
								<Tooltip label={copiedAt === place ? 'Copied' : 'Copy'} staysOnPress>
									<Chip size="sm" tone="quiet" onselect={() => void copyName(place, textOf(entry))}>
										{textOf(entry)}
									</Chip>
								</Tooltip>
							</li>
						{/each}
					</ChipRow>
				{/if}
			{:else if one.kind === 'links'}
				{#if listOf(one.key).length > 0}
					<ul class="links" aria-label={one.label}>
						<!-- POSITION in the key: a field can hold one value twice. -->
						{#each listOf(one.key) as entry, at (`${at}:${urlOf(entry)}`)}
							{@const url = urlOf(entry)}
							{@const mark = markOf(entry)}
							<li>
								<Tooltip label={url}>
									<!--
									`noreferrer`, so this install's address is not sent to the site.
									-->
									<a
										href={url}
										target="_blank"
										rel="noopener noreferrer external"
										aria-label={siteOf(entry) ?? hostOf(url)}
									>
										<!-- Keyed, so the next picture replaces a failed one. -->
										{#if mark}
											{#key mark}
												<img
													src={mark}
													alt=""
													width="16"
													height="16"
													loading="lazy"
													decoding="async"
													onerror={() => failed.add(mark)}
												/>
											{/key}
										{:else}
											<Icon name="link" size={16} />
										{/if}
									</a>
								</Tooltip>
							</li>
						{/each}
					</ul>
				{/if}
			{/if}
		{/each}
	</div>
{/if}

<style>
	.summary {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.alias {
		transition: translate var(--dur-base) var(--ease-spring);
	}

	.alias:hover,
	.alias:focus-within {
		translate: 0 var(--lift-y-sm);
	}

	/* They wrap rather than scroll. */
	.links {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.links a {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		inline-size: 28px;
		block-size: 28px;
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		color: var(--sift-ink-2);
		text-decoration: none;
		transition:
			border-color var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	.links a:hover {
		border-color: var(--sift-accent);
		color: var(--sift-accent-text);
	}

	.links a:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.links img {
		inline-size: 16px;
		block-size: 16px;
		object-fit: contain;
	}
</style>
