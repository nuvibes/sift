<script lang="ts">
	/* Where a download starts: one box, one button, and the line saying which way it goes out.
	 *
	 * ## The choices are in the page's Options
	 *
	 * The repeats and galleries switches and Download folder are rows of the page's Options menu
	 * (`DownloadOptions`), choices FOR THIS PASTE that start from the settings. What is under the
	 * box is `DownloadChoices`: which way out the pasted Site takes.
	 *
	 * ## The button is the box's height, always
	 *
	 * The box and the button beside it are one control row: both start at the control height and
	 * the row stretches the button to whatever height the box has, so a list pasted into the box
	 * (which grows with its lines where the browser can size a box by its content) never leaves a
	 * short button beside a tall box. On a window too narrow for both on one line the button is
	 * the box's width instead, under it.
	 *
	 * ## Why the Site pills
	 *
	 * A pasted address either is a site Sift has a record for or it is not, and that is the
	 * single most useful thing to know BEFORE pressing Download: it is what decides whether the
	 * file gets filed under whoever posted it. It costs nothing to say: the list of sites is
	 * already fetched for the sentence in the empty state.
	 *
	 * ## With no download folder set, the first paste ASKS
	 *
	 * "Not set: each download asks" is what the folder row says in Settings and under Options,
	 * Download folder, and this is where it is kept. A paste that has nowhere to land (no folder
	 * chosen under Download folder, no default, and no folder of its own for the pasted Site) is not
	 * sent: the box says so and the page opens its Options on Download folder. Sent anyway, the server would take the link,
	 * fetch the whole file and only then refuse it for want of a folder: a download spent to
	 * learn a setting. A folder is never chosen for somebody here; the question is only ever
	 * asked.
	 */
	import { onMount, tick } from 'svelte';
	import { Button, Chip, ChipRow, Field, TextArea } from '$lib/components/common';
	import { Destinations } from '$lib/library/destinations.svelte';
	import { filesSaid } from '$lib/entity/entity-counts';
	import type { components } from '$lib/api/schema';
	import { lines, type BulkPreview } from './queue.svelte';
	import DownloadChoices from './DownloadChoices.svelte';

	type SupportedSite = components['schemas']['SupportedSite'];

	interface Props {
		/** What is in the box. Bound, because the page empties it when a paste is accepted. */
		value: string;
		busy: boolean;
		/** The refusal to show under the box, from the queue. */
		error: string | undefined;
		/** Every site Sift has a record for, for the pills. Empty until the read lands. */
		sites: readonly SupportedSite[];
		/** What a playlist or channel address turned out to hold, once asked. Null until then. */
		found: BulkPreview | null;
		/** Drawn for a window too narrow to keep the box and its button on one line. See `narrow`. */
		narrow: boolean;
		onsubmit: () => void;
		/** Go ahead with everything behind the address. */
		onall: () => void;
		/** Queue the one link that was pasted, and nothing else behind it. */
		onjustone: () => void;
		/** Where the next download goes: '' for the default folder, else a folder id. Chosen under
		 *  the page's Options, Download folder; read here to know whether a paste has anywhere to go. */
		dest?: string;
		/** A paste had nowhere to go: the page opens its Options, where Download folder is. */
		onask?: () => void;
	}

	let {
		value = $bindable(),
		busy,
		error,
		sites,
		found,
		narrow,
		onsubmit,
		onall,
		onjustone,
		dest = '',
		onask
	}: Props = $props();

	const pasted = $derived(lines(value));

	/* The button counts what it will do. "Download" over three links says nothing about the third
	   one, and the count is the only warning somebody gets that a stray line came along with the
	   paste. */
	const label = $derived(pasted.length > 1 ? `Download ${pasted.length}` : 'Download');

	/** The site a pasted address belongs to, by host, or nothing where Sift has no record.
	 *
	 *  Matched against the same `hosts` the server matches on, rather than on a name in the
	 *  address: a second rule here would be a second answer to "which site is this", and the two
	 *  would disagree the first time one of them learned a host. */
	function siteOf(address: string): SupportedSite | undefined {
		let host: string;
		try {
			host = new URL(address).hostname.replace(/^www\./, '').toLowerCase();
		} catch {
			return undefined;
		}
		return sites.find((site) =>
			site.hosts.some((known) => host === known || host.endsWith(`.${known}`))
		);
	}

	/** One pill per site the paste mentions, in the order they first appear. */
	const recognised = $derived.by(() => {
		const seen = new Map<string, string>();
		for (const line of pasted) {
			const site = siteOf(line);
			if (site && !seen.has(site.key)) seen.set(site.key, site.name);
		}
		return [...seen].map(([key, name]) => ({ key, name }));
	});

	/* The box itself, so the page can put the cursor in it.
	 *
	 * A paste anywhere on the screen fills this box, and a box that fills without taking the cursor
	 * leaves somebody looking at a link they cannot correct without reaching for it. Found through
	 * the form rather than kept as a second binding: `Field` mints the control's id and `TextArea`
	 * owns the element, so the form is the one thing this file holds that certainly contains it. */
	let form: HTMLFormElement | undefined = $state();

	export function focus(): void {
		form?.querySelector('textarea')?.focus();
	}

	/* Where a download may go and which folder applies when none is chosen: the same module the
	   Download folder rows under Options read, so the two cannot disagree about whether a folder is set. */
	const where = new Destinations();
	onMount(() => void where.load());

	/** Whether any pasted line is from a Site Sift does not recognize: that one follows the
	 *  default alone, since no Site's own folder can speak for it. */
	const unrecognized = $derived(pasted.some((line) => siteOf(line) === undefined));

	/** The question, once a paste had nowhere to go. Cleared the moment a folder is chosen. */
	let asking = $state<string | undefined>(undefined);
	$effect(() => {
		if (dest !== '') asking = undefined;
	});

	async function send(): Promise<void> {
		const keys = recognised.map((one) => one.key);
		if (pasted.length === 0 || !where.hasNowhereFor(dest, keys, unrecognized)) {
			asking = undefined;
			onsubmit();
			return;
		}
		if (where.folders.length === 0) {
			// No folder to offer at all, so there is no chooser to send somebody to.
			asking = 'Sift has no folder to save a download in yet. Add a folder to your library first.';
			return;
		}
		asking =
			'No download folder is set. Choose one for this download under Options, Download folder.';
		await tick();
		onask?.();
	}
</script>

<form
	bind:this={form}
	onsubmit={(event) => {
		event.preventDefault();
		void send();
	}}
>
	<Field label="Paste a link" error={error ?? asking}>
		{#snippet control({ id, describedBy, invalid })}
			<div class="row" class:narrow>
				<!-- A textarea rather than an input, so it takes a list as well as one link: it starts
				     one control row tall, beside its button, and grows with what is pasted (see the
				     style below). An input's `type="url"` refuses anything with a newline in it,
				     which is exactly what a pasted list is, so the browser would reject the paste
				     before it was sent. -->
				<TextArea
					{id}
					rows={1}
					class="link"
					{invalid}
					bind:value
					{describedBy}
					placeholder={'https://\u2026'}
				/>
				<!-- The box's width as well as its height where the two stack (`full`): the button
				     under a box is the width of the thing it acts on. -->
				<Button
					type="submit"
					tone="primary"
					icon="download"
					class="send"
					full={narrow}
					disabled={busy}>{label}</Button
				>
			</div>
		{/snippet}
	</Field>
</form>

{#if recognised.length > 0}
	<!-- In a box of its own for the room above it: the row itself carries no margin. -->
	<div class="pasted-sites">
		<ChipRow gap="tight" label="Sites this paste is from">
			{#each recognised as site (site.key)}
				<li><Chip size="sm" tone="quiet">{site.name}</Chip></li>
			{/each}
		</ChipRow>
	</div>
{/if}

{#if found}
	<!-- Taking a creator's whole channel is a decision, so the number comes first and nothing is
	     queued until it has been seen. Asking the site fetches no media. -->
	<div class="found" role="status">
		<p class="count">
			{filesSaid(found.count)} on {found.site}.
			<!-- The cap is the SERVER's, sent with the answer: a number written here would be a
			     second copy of a limit the server enforces, and the day one of them moved the
			     screen would go on promising the old one. -->
			{#if found.truncated}Up to {found.limit} at a time.{/if}
		</p>
		<div class="answers">
			<Button tone="primary" size="small" icon="download" disabled={busy} onclick={onall}
				>Download all</Button
			>
			<Button size="small" icon="download" disabled={busy} onclick={onjustone}
				>Download only this one</Button
			>
		</div>
	</div>
{/if}

<div class="choices">
	<!-- The Sites the pills name are the Sites the connection line speaks for: one reading of the
	     paste, so the pill and the line cannot name different Sites. -->
	<DownloadChoices pastedSites={recognised} />
</div>

<style>
	/* The whole width of the region it heads. No cap: a cap makes it cramped. */
	.row {
		display: flex;
		/* The button the box's height, whatever height the box has: the row stretches it. */
		align-items: stretch;
		gap: var(--space-2);
	}

	/* Under the box rather than beside it, on a window that cannot hold both.
	 *
	 * Not a wrap: a wrapped button would sit at the left of its own line, and every verb in this
	 * application sits on the right of the row it belongs to. A column puts the box and the button
	 * each across the whole width, which is the one arrangement at this size where neither is too
	 * narrow to read, and the button is then the width of the thing it acts on, which is what a
	 * phone-sized layout does with a primary verb. The width itself is `narrow.svelte.ts`'s. */
	.row.narrow {
		flex-direction: column;
		align-items: stretch;
	}

	/* How the paste box shares the row with the button beside it, and nothing else. The focus ring
	   is the global `:focus-visible` rule's, not restated here. `:global`, because the box is
	   `TextArea`'s own element, compiled in that file's scope. */
	.row :global(.link) {
		flex: 1;
	}

	/*
	 * ONE HEIGHT FOR THE BOX AND ITS BUTTON: the control height, the token every field and press
	 * in a row is built from (a finger's height on a phone, where app.css raises it). The box does
	 * not start at the two-line height app.css gives every text area, which would leave a button
	 * half its height beside it; it grows with what is pasted where the browser can size a
	 * box by its content, up to four control heights, and the row above takes the button with it.
	 * No hand resize: a box dragged taller by its corner is the one height nothing else follows.
	 * `:global`, because the box is `TextArea`'s element and the button `Button`'s.
	 */
	.row :global(.link),
	.row :global(.send) {
		min-block-size: var(--control-height);
	}

	.row :global(.link) {
		block-size: var(--control-height);
		max-block-size: calc(var(--control-height) * 4);
		resize: none;
	}

	@supports (field-sizing: content) {
		.row :global(.link) {
			block-size: auto;
			field-sizing: content;
		}
	}

	.row :global(.send) {
		block-size: auto;
	}

	/* The Sites the paste names stand clear of the box they read, as a field's help does. */
	.pasted-sites {
		margin-block-start: var(--space-2);
	}

	.found {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin-block-start: var(--space-3);
	}

	.count {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.answers {
		display: flex;
		gap: var(--space-2);
		margin-inline-start: auto;
	}

	/* Where the settings sit under the box: the room between them and it, and the room before the
	   queue. What they look like is `DownloadChoices`'s. */
	.choices {
		margin-block: var(--space-3) var(--space-5);
	}
</style>
