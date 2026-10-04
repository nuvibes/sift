<script lang="ts">
	/*
	 * WHICH ENTRY in each stash-box this person, Site or tag is: the box by its own name, the id it
	 * files them under, and a press through to the box's own page for them.
	 *
	 * ## Why a section of its own, and why here
	 *
	 * The links are drawn in two other places and neither answers the question. The header's
	 * marks name the box on hover and say nothing about which entry; the Look up sheet lists them
	 * behind a menu row. Stash draws each one on the entity's own page, and somebody moving from it
	 * looks there. So they are rows in the record's facts column, under the labelled facts and on
	 * the same two columns, so a box's name stands where a fact's label stands and its id where a
	 * value does.
	 *
	 * The `Stash-boxes` FIELD behind the "show every field" switch is a different question and stays:
	 * when each box last answered, and what it said that Sift has no field for. That is provenance
	 * somebody goes looking for; this is the join itself, which is on show whenever there is one.
	 *
	 * ## Who sees what
	 *
	 * The list is read from `GET /api/stash-boxes/links/...`, the one stash-box route open to every
	 * signed-in viewer, because it reaches no network and spends no key. So the rows are drawn for
	 * everybody who can see the page. Remove is an admin's alone, as it is on the server
	 * (`DELETE` on the same address is admin-only), and a guest's rows carry no hover action.
	 *
	 * ## Where the press goes
	 *
	 * The page's address is the SERVER's (`page_url` on the link), never built here. A creator on a
	 * box whose studios are the creators is a studio there, which is a fact about the box this
	 * screen never holds. Where the server has no address the id is drawn as text: a link to the
	 * wrong page is worse than none.
	 *
	 * ## The links come in, they are not read here
	 *
	 * The page already reads them for the header's marks, in the same answer as who made the row
	 * (`sourcesOf`). A second read here could be told something different from the first, and the
	 * page's own marks would disagree with its own list. Forgetting asks the page to read again.
	 */
	import { FACT_COLUMNS } from '$lib/components/entity/RecordFacts.svelte';
	import {
		Button,
		ConfirmDialog,
		DataRow,
		DataRows,
		SectionHeading,
		Tooltip
	} from '$lib/components/common';
	import {
		forgetLink,
		problemFrom,
		type LinkSubject,
		type StashBoxLink
	} from '$lib/entity/enrich.svelte';
	import { fields } from '$lib/entity/records.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	interface Props {
		/** Which kind of thing the page is about. */
		subject: LinkSubject;
		/** Its id. */
		id: string;
		/** Its name, for the question Remove asks and for a screen reader. */
		name: string;
		/** What the page read: every stash-box that has been agreed to know it. */
		links: readonly StashBoxLink[];
		/** Whether Remove is offered. An admin's alone, like the route behind it. */
		mayForget?: boolean;
		/** Read the links again, after one has been forgotten. */
		onforgot?: () => void | Promise<void>;
	}

	let { subject, id, name, links, mayForget = false, onforgot }: Props = $props();

	/* One glyph button, laid over the row's end while it is hovered, standing in a track only on a
	   touch screen. */
	const ACTIONS = 'var(--control-height-sm)';

	let asking = $state<StashBoxLink | null>(null);
	let open = $state(false);
	let busy = $state('');

	/**
	 * What this box filled in that the record still holds as it gave it, as one line: "Filled in
	 * birthdate and height". The same `gave` the record's own hover reads, so the band and the hover
	 * cannot disagree about where a value came from. Nothing when it gave nothing that still holds.
	 */
	function filledIn(one: StashBoxLink): string {
		const named = (one.gave ?? [])
			.map((key) => fields.one(subject, key)?.label.toLowerCase())
			.filter((label): label is string => Boolean(label));
		if (named.length === 0) return '';
		const list =
			named.length === 1
				? named[0]
				: `${named.slice(0, -1).join(', ')} and ${named[named.length - 1]}`;
		return `Filled in ${list}`;
	}

	async function forget(one: StashBoxLink): Promise<void> {
		if (busy) return;
		busy = one.box_id;
		try {
			await forgetLink(subject, id, one.box_id);
			await onforgot?.();
		} catch (error) {
			toasts.show(problemFrom(error), { tone: 'error' });
		} finally {
			busy = '';
		}
	}
</script>

{#if links.length > 0}
	<!-- A heading, because a box's name in a label's place reads as one more fact: the band's
	     heading says what the rows under it are. -->
	<div class="ids">
		<SectionHeading band>Stash-boxes</SectionHeading>
		<DataRows
			items={links}
			key={(one) => one.box_id}
			label="Stash-boxes that know {name}"
			columns={FACT_COLUMNS}
			actions={mayForget ? ACTIONS : undefined}
			edges
		>
			{#snippet row(one)}
				<DataRow
					compact
					cells={{ label: box, value: entry }}
					actions={mayForget ? forgetting : undefined}
				/>
				{#snippet box()}<span class="box">{one.box_name}</span>{/snippet}
				{#snippet entry()}
					{#if one.page_url}
						<!-- The id is what is read; the page it opens is on the label that appears on
					     hover, the way a record's other links say where they go. -->
						<Tooltip label={one.page_url} placement="top" shrinks>
							<a class="id" href={one.page_url} target="_blank" rel="noopener noreferrer external"
								>{one.remote_id}</a
							>
						</Tooltip>
					{:else}
						<span class="id">{one.remote_id}</span>
					{/if}
					{#if filledIn(one)}
						<span class="gave">{filledIn(one)}</span>
					{/if}
				{/snippet}
				{#snippet forgetting()}
					<Tooltip label="Remove">
						<Button
							tone="ghost"
							size="small"
							icon="close"
							busy={busy === one.box_id}
							aria-label="Remove the link to {one.box_name}"
							onclick={() => {
								asking = one;
								open = true;
							}}
						/>
					</Tooltip>
				{/snippet}
			{/snippet}
		</DataRows>
	</div>
{/if}

<!-- Remove asks first: it is one press on a glyph laid over the row, and a link taken back is only
     made again by looking the entry up once more. The Look up sheet's own press for this does not
     ask, because the sheet is opened to decide exactly this. -->
{#if mayForget}
	<ConfirmDialog
		bind:open
		title="Remove the {asking?.box_name ?? 'stash-box'} link from {name}?"
		consequence="Everything it filled in stays. To link it again, use Enrich."
		confirmLabel="Remove"
		onconfirm={() => {
			if (asking) void forget(asking);
			asking = null;
		}}
	/>
{/if}

<style>
	.ids {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The box's name stands where a fact's label stands, and reads like one: it says which box, the
	   id beside it is the fact. */
	.box {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	/* An id is an exact string somebody may copy or compare, so the data face; it has no spaces, so
	   it breaks anywhere rather than running out of its column. */
	.id {
		font: var(--text-data);
		overflow-wrap: anywhere;
	}

	span.id {
		color: var(--sift-ink-2);
	}

	/* What the box gave, under its id: read past like a label, on a line of its own. */
	.gave {
		display: block;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
