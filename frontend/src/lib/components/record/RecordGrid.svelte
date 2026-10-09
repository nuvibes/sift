<script lang="ts">
	/*
	 * A file's record, edited where it is read: the same grid in both modes, every cell reserving a
	 * control's height, so nothing moves on Edit. Both layouts draw `FieldEditor`.
	 */
	import { tick, untrack, type Snippet } from 'svelte';
	import { Button, EditMarks, Problem } from '$lib/components/common';
	import FieldEditor from '$lib/components/record/FieldEditor.svelte';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import {
		appliesToKind,
		fields,
		filledIn,
		inGroup,
		linkFor,
		saveProblem,
		type FieldDescription,
		type FieldGroup,
		type RecordSubject
	} from '$lib/entity/records.svelte';

	interface Props {
		subject: RecordSubject;
		values: Record<string, unknown>;
		editing?: boolean;
		/** Anything that throws leaves the boxes up with what was typed. */
		onsave?: (draft: Record<string, unknown>) => Promise<void>;
		oncancel?: () => void;
		label: string;
		/** So Save and Cancel can be drawn outside it (`<button form>`), where Edit was. */
		formId?: string;
		/**
		 * Draw only filled fields while READ: a file's thirty-odd machine facts would be mostly
		 * dashes. Every field shows in edit mode, so Edit grows the record; the caller lifts its
		 * cap too.
		 */
		onlyFilled?: boolean;
		/** Which half to draw, by the registry's `group`; absent draws both. */
		group?: FieldGroup;
		/**
		 * An empty field draws "Add", opening the form on that row; the opposite answer to
		 * `onlyFilled`.
		 */
		onadd?: (key: string) => void;
		/** Read once per opening. */
		focusField?: string | null;
		/** A still's measured half carries facts false of a photograph (`appliesToKind`). */
		mediaType?: string | null;
		/**
		 * A line under one field's value while read, like where a song name came from
		 * (`MusicSource`).
		 */
		under?: Snippet<[FieldDescription]>;
	}

	let {
		subject,
		values,
		editing = false,
		onsave,
		oncancel,
		label,
		formId,
		onlyFilled = false,
		group,
		onadd,
		focusField = null,
		mediaType = null,
		under
	}: Props = $props();

	const uid = $props.id();

	/* `filledIn` is the registry module's, so a tab's count and its pane agree. */

	const all = $derived(
		fields
			.drawn(subject)
			.filter((one) => group === undefined || inGroup(one, group))
			.filter((one) => appliesToKind(one, mediaType))
			.filter((one) => !onlyFilled || editing || filledIn(values[one.key]))
	);

	/* Short facts first, the wide ones last across the width: a paragraph's box is four lines. */
	const WIDE = ['paragraph', 'names', 'links', 'tags', 'accounts', 'sources'];
	const shown = $derived(all.filter((one) => !WIDE.includes(one.kind)));

	/* The wide half with the paragraph LAST, the one thing with no bound on its height. */
	const wide = $derived(
		all
			.filter((one) => WIDE.includes(one.kind))
			.toSorted((a, b) => Number(a.kind === 'paragraph') - Number(b.kind === 'paragraph'))
	);

	let busy = $state(false);
	let problem = $state<string | undefined>();

	/* Seeded when editing STARTS and never again, so a re-fetch cannot throw away typing. */
	let draft = $state<Record<string, unknown>>({});
	let open = false;
	$effect(() => {
		if (editing && !open) {
			open = true;
			draft = untrack(() => ({ ...values }));
			problem = undefined;
		} else if (!editing) {
			open = false;
		}
	});

	/* The pressed row's box, focused once per opening, keyed on `editing`. */
	let focused = false;
	$effect(() => {
		if (!editing) {
			focused = false;
			return;
		}
		if (focused) return;
		focused = true;
		const key = untrack(() => focusField);
		if (!key) return;
		const box = document.getElementById(`${uid}-${key}-box`);
		// A machine fact has no box.
		if (box instanceof HTMLElement) box.focus();
	});

	/* Blank list entries dropped, the rest trimmed. */
	function cleaned(held: Record<string, unknown>): Record<string, unknown> {
		const out: Record<string, unknown> = { ...held };
		for (const one of all) {
			if (one.kind !== 'names' && one.kind !== 'links') continue;
			if (!(one.key in out)) continue;
			const list = Array.isArray(out[one.key]) ? (out[one.key] as unknown[]) : [];
			out[one.key] = list.map((entry) => String(entry ?? '').trim()).filter(Boolean);
		}
		return out;
	}

	/* ONE FIELD alone, from "Add": its tick saves that field only, never the rest as loaded. */
	let alone = $state<string | null>(null);
	let aloneValue = $state<unknown>(null);

	async function openAlone(key: string) {
		alone = key;
		aloneValue = values[key] ?? null;
		problem = undefined;
		await tick();
		const box = document.getElementById(`${uid}-${key}-box`);
		if (box instanceof HTMLElement) box.focus();
	}

	function closeAlone() {
		alone = null;
		problem = undefined;
	}

	async function keepAlone() {
		if (busy || !onsave || alone === null) return;
		busy = true;
		problem = undefined;
		try {
			await onsave(cleaned({ [alone]: $state.snapshot(aloneValue) }));
			alone = null;
		} catch (failure) {
			problem = saveProblem(failure);
		} finally {
			busy = false;
		}
	}

	/* Enter keeps and Escape leaves; not Enter in a paragraph or a list. */
	function aloneKeys(event: KeyboardEvent, one: FieldDescription) {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			closeAlone();
		} else if (event.key === 'Enter' && !WIDE.includes(one.kind)) {
			event.preventDefault();
			void keepAlone();
		}
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (busy || !onsave || !editing) return;
		busy = true;
		problem = undefined;
		try {
			await onsave(cleaned($state.snapshot(draft)));
		} catch (failure) {
			// One sentence; a route's refusal (a rename on disk) arrives as itself. See
			// `saveProblem`.
			problem = saveProblem(failure);
		} finally {
			busy = false;
		}
	}
</script>

<!-- One cell's contents, for both halves of the grid. -->
{#snippet cell(one: FieldDescription)}
	{#if editing && one.editable}
		<FieldEditor
			field={one}
			id="{uid}-{one.key}-box"
			value={draft[one.key]}
			onchange={(next) => (draft[one.key] = next)}
		/>
	{:else if !editing && alone === one.key}
		<!-- svelte-ignore a11y_no_static_element_interactions: the keys are the box's own, caught on the way out -->
		<div class="alone" onkeydown={(event) => aloneKeys(event, one)}>
			<EditMarks
				wide={WIDE.includes(one.kind)}
				{busy}
				oncancel={closeAlone}
				onkeep={() => void keepAlone()}
				cancelLabel="Keep what was in {one.label}"
				keepLabel="Save {one.label}"
			>
				<FieldEditor
					field={one}
					id="{uid}-{one.key}-box"
					value={aloneValue}
					onchange={(next) => (aloneValue = next)}
				/>
			</EditMarks>
		</div>
	{:else if onadd && one.editable && !filledIn(values[one.key])}
		<!--
		"Add" where the value would be, in the `link` tone, announced with the field's name.
		-->
		<Button
			tone="link"
			aria-label="Add {one.label}"
			onclick={() => (onsave ? void openAlone(one.key) : onadd(one.key))}>Add</Button
		>
	{:else}
		<!-- Not editable on the server: drawn as it reads in both modes. -->
		<RecordValue
			kind={one.kind}
			value={editing ? draft[one.key] : values[one.key]}
			link={linkFor(one, editing ? draft : values)}
			copyable={!editing}
		/>
	{/if}
{/snippet}

{#if all.length > 0}
	<form id={formId} onsubmit={save} aria-label={editing ? `Editing ${label}` : label}>
		<dl class="record">
			{#each shown as one (one.key)}
				<div class="fact">
					<dt id="{uid}-{one.key}">
						<label for={editing && one.editable ? `${uid}-${one.key}-box` : undefined}>
							{one.label}
						</label>
					</dt>
					<!-- One `dd` in both modes, or the cell would move. -->
					<dd>
						{@render cell(one)}{#if under && !editing}{@render under(one)}{/if}
					</dd>
				</div>
			{/each}
		</dl>

		{#if wide.length > 0}
			<dl class="wide">
				{#each wide as one (one.key)}
					<div class="fact">
						<dt id="{uid}-{one.key}">
							<label for={editing && one.editable ? `${uid}-${one.key}-box` : undefined}>
								{one.label}
							</label>
						</dt>
						<dd>
							{@render cell(one)}{#if under && !editing}{@render under(one)}{/if}
						</dd>
					</div>
				{/each}
			</dl>
		{/if}

		<!-- Only the message: Save and Cancel are the page's, through `form=`. -->
		{#if editing || alone !== null}<Problem message={problem} />{/if}
	</form>
{/if}

<style>
	/* `auto-fill`, and identical to the read-only record's, so the grid never changes on Edit. */
	.record {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
		gap: var(--space-4) var(--space-6);
		margin: 0;
	}

	.fact {
		min-inline-size: 0;
	}

	dt {
		font: var(--text-label);
		color: var(--sift-ink-3);
		margin-block-end: var(--space-1);
	}

	dt label {
		font: inherit;
		color: inherit;
	}

	/* A control's height reserved in BOTH modes, centred, so nothing moves on Edit. */
	dd {
		margin: 0;
		min-inline-size: 0;
		min-block-size: 2.25rem;
		display: grid;
		align-content: center;
	}

	/* The "Add" link starts under its label rather than stretched and centred. */
	dd > :global(.link) {
		justify-self: start;
	}

	/*
	 * An offset, not a negative margin, which would shrink the tooltip wrapper and break the words.
	 */
	dd :global(.copyable) {
		justify-self: start;
		position: relative;
		inset-inline-start: calc(-1 * var(--space-1));
	}

	.alone {
		min-inline-size: 0;
	}

	/* The same grid as the facts above. */
	.wide {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
		gap: var(--space-4) var(--space-6);
		margin: var(--space-4) 0 0;
		align-items: end;
	}

	/* No reserved height: nothing is under them but the Save row. */
	.wide dd {
		min-block-size: 0;
		display: block;
	}

	/* Fill the cell; a textarea otherwise sizes itself from `cols`. */
	.wide dd :global(textarea),
	.wide dd :global(input) {
		inline-size: 100%;
		box-sizing: border-box;
	}
</style>
