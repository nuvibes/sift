<script lang="ts">
	/* What a thing IS, drawn from the one description of its fields.
	 *
	 * Named `RecordView` rather than `Record`, and that is not a preference: `Record<K, V>` is one of
	 * TypeScript's own built-in types and is used all over this codebase. A component called `Record`
	 * shadows it inside every file that imports one, and the error when it does ("Record is not
	 * generic") points at the innocent line rather than at the import.
	 *
	 * ## Why this is generated rather than written per screen
	 *
	 * A person, a site, a photo set and a file all have a record, and the same field turns up on more
	 * than one of them. Written out per screen they drift: one says "Notes" and another says
	 * "Details", one shows a length in seconds and another in milliseconds, and nothing on either
	 * screen says which is right. The server declares each field once (its name in plain language,
	 * its type, whether it belongs on the record without asking) and this renders that.
	 *
	 * ## Why a grid of facts and not a list of rows
	 *
	 * A two-column list, label left and value right, is the shape a settings pane uses. Across the
	 * top of a page that is a label at one edge and its value at the other with a hand's
	 * width of nothing between them, and it uses a column of the screen to say six things. A record
	 * is a handful of short facts, so they read as facts: a small dim label with its value under it,
	 * flowing across whatever width there is.
	 *
	 * ## Why it draws the fields BEHIND the switch and not all of them
	 *
	 * The record has two placements, not one. What a thing is called by, and where it can be found,
	 * sit under the heading permanently; the rest is here, in the panel, and only when somebody has
	 * asked for it. Which field is in which place is the server's declaration, so a field moves
	 * between them by changing one word there, and the two surfaces cannot disagree or double up.
	 *
	 * ## Rows with nothing in them
	 *
	 * Drawn, with a dash. A record that hides its empty fields changes shape depending on how much
	 * somebody has filled in, so the same person looks like a different kind of thing before and
	 * after being edited, and there is nowhere to see what could be filled in.
	 */
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { fields, linkFor, type RecordSubject } from '$lib/entity/records.svelte';

	interface Props {
		/** Which kind of thing this is, which is what decides the fields and their order. */
		subject: RecordSubject;
		/** The values, by field key. A key with nothing against it draws as a dash. */
		values: Record<string, unknown>;
		/**
		 * Which half of the record this is drawing.
		 *
		 * `more` is the panel beside the name, and it is the default because that is the only place
		 * this component is drawn now: the fields marked as being on the record without asking are
		 * under the heading, in `RecordSummary`, and repeating them here would spend the panel
		 * saying what is already on screen two inches away.
		 *
		 * `all` is for a surface with no summary above it: a file's record, opened on its own.
		 */
		showing?: 'more' | 'all';
		/** Names the record for a screen reader: whose record this is. */
		label: string;
		/** Where a field came from, by key, said on the value's hover. See `givenBy`. */
		given?: Readonly<Record<string, string>>;
	}

	let { subject, values, showing = 'more', label, given = {} }: Props = $props();

	const shown = $derived(showing === 'all' ? fields.drawn(subject) : fields.behindMore(subject));
</script>

{#if shown.length > 0}
	<dl class="record" aria-label={label}>
		{#each shown as one (one.key)}
			<div class="fact">
				<dt>{one.label}</dt>
				<dd>
					{#if given[one.key]}
						<Tooltip label={given[one.key]} placement="top" shrinks>
							<RecordValue kind={one.kind} value={values[one.key]} link={linkFor(one, values)} />
						</Tooltip>
					{:else}
						<RecordValue kind={one.kind} value={values[one.key]} link={linkFor(one, values)} />
					{/if}
				</dd>
			</div>
		{/each}
	</dl>
{/if}

<style>
	/*
	 * Columns that fill whatever width there is, each wide enough for a name or a link to sit on one
	 * line. `auto-fill` rather than `auto-fit`: with two facts and a wide window `auto-fit` stretches
	 * them across the whole thing, which puts a two-word value in a column the width of a wall.
	 */
	.record {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
		gap: var(--space-4) var(--space-6);
		margin: 0;
	}

	.fact {
		min-inline-size: 0;
	}

	/* The label, quiet and small. It is the thing you read past to get to the value, not the thing
	   you read, so it is the dimmer of the two, which is the opposite of a settings pane. */
	dt {
		font: var(--text-label);
		color: var(--sift-ink-3);
		margin-block-end: var(--space-1);
	}

	dd {
		margin: 0;
		min-inline-size: 0;
	}
</style>
