<script lang="ts">
	/* The handful of facts that sit under the name, always.
	 *
	 * ## Why this is a second component and not a mode of the readout
	 *
	 * There are two placements and they are not the same shape. The facts a record shows without
	 * being asked (the other names somebody goes by, where they can be found) belong under the
	 * heading, small and unlabelled, the way a name badge carries them. Everything else belongs in
	 * the panel beside the name, as a labelled grid, and only when somebody has asked for it.
	 *
	 * What decides which is which is NOT written here. The server marks a field as being on the
	 * record or behind the switch, and this draws the first set while `RecordView` draws the second.
	 * So a field moves between the two places by changing one word on the server, and neither screen
	 * has to be edited or can disagree with the other about where a field lives.
	 *
	 * ## Why there are no labels
	 *
	 * "Aliases" over two chips and "Links" over two icons is four words of furniture to carry two
	 * facts, directly under a heading that is already the loudest thing on the page. Chips read as
	 * other names and an icon that is a site's own mark reads as a link to that site, so the words
	 * would say what the shapes already say. They are still on the field in the registry, so the
	 * form and the panel both keep them, and every one of these still names itself to a screen
	 * reader. A fact that needs its label (a birthdate, a nationality) is not drawn here at all: it
	 * stands beside the name in `RecordFacts`, in the column the rest of the record opens into.
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
		/** The values, by field key. */
		values: Record<string, unknown>;
		/** Whose facts these are, for a screen reader. */
		label: string;
	}

	let { subject, values, label }: Props = $props();

	const shown = $derived(fields.onRecord(subject));

	/** A list, whatever shape the page had it in, and nothing at all when it is empty. */
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

	/* The address in the shortest form that still says where it goes. This is the accessible NAME of
	   the link, so it has to be the useful part rather than the whole query string: a screen reader
	   reading out a tracking parameter is worse than one reading nothing. The full address is on the
	   label beside it. */
	function hostOf(url: string): string {
		try {
			return new URL(url).hostname.replace(/^www\./, '');
		} catch {
			return url.replace(/^https?:\/\//, '');
		}
	}

	/*
	 * An other name, copied by pressing its chip.
	 *
	 * The same arrangement the file's own name has on its screen: `copyText` (never
	 * `navigator.clipboard`, which is absent on the plain-http address Sift is normally reached at),
	 * a tooltip that says Copy and then Copied, and back to Copy on its own so the next person to
	 * hover is told what pressing does. No toast unless it failed: this happened under the pointer.
	 * Which chip was copied is held by its place, since a record can hold one name twice.
	 */
	let copiedAt = $state<string | null>(null);
	let copiedFor: ReturnType<typeof setTimeout> | null = null;
	/** Long enough to be read: the file name's own. */
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

	/* The pictures that answered 404, so the next one is tried and, with none left, the plain link
	   glyph is drawn. Shared across the row: a host the pack does not know is not asked twice. */
	const failed = new SvelteSet<string>();

	/** The site's own mark for one link: the pack's by host, else the one a download fetched,
	 *  or null, and the plain link glyph stands in. See `linkMarks`, which both link surfaces read. */
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
						<!-- POSITION is part of the key: these are a record's own values, and a record
						     field can hold the same name or the same address twice. Two rows under one
						     key is a hard error rather than a row drawn wrongly. -->
						{#each listOf(one.key) as entry, at (`${at}:${textOf(entry)}`)}
							{@const place = `${one.key}:${at}`}
							<!-- Pressing a name copies it: the tooltip says Copy, then Copied. Under the
							     pointer it rises as a small surface does (`--lift-y-sm`, the Lift
							     register's pace and spring) on top of the chip's own hover layer. -->
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
						<!-- POSITION is part of the key: these are a record's own values, and a record
						     field can hold the same name or the same address twice. Two rows under one
						     key is a hard error rather than a row drawn wrongly. -->
						{#each listOf(one.key) as entry, at (`${at}:${urlOf(entry)}`)}
							{@const url = urlOf(entry)}
							{@const mark = markOf(entry)}
							<li>
								<Tooltip label={url}>
									<!--
										`noreferrer` as well as `noopener`. The first stops the page that opens
										reaching back through `window.opener`; the second stops this install's
										address being sent to the site as a Referer, which for a library on
										somebody's home network is worth not announcing.
									-->
									<a
										href={url}
										target="_blank"
										rel="noopener noreferrer external"
										aria-label={siteOf(entry) ?? hostOf(url)}
									>
										<!-- Keyed, so a failed picture's element is replaced by the next one's
										     rather than handed a new address mid-error. -->
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

	/* An other name rises a little under the pointer: transform only, so nothing beside it moves. */
	.alias {
		transition: translate var(--dur-base) var(--ease-spring);
	}

	.alias:hover,
	.alias:focus-within {
		translate: 0 var(--lift-y-sm);
	}

	/* A row of marks. They wrap rather than scroll: somebody with nine links has nine of these and
	   a horizontal scroller two rows under a heading is furniture nobody looks for. */
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
		/* The change steps over --dur-instant rather than happening between frames. */
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

	/* Square, because a site's mark is square and a rounded frame around it crops the corners of
	   the one thing the row exists to show. */
	.links img {
		inline-size: 16px;
		block-size: 16px;
		object-fit: contain;
	}
</style>
