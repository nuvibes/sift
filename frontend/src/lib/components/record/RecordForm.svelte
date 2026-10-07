<script lang="ts">
	/* Every field of a record, edited together, saved once.
	 *
	 * ## Why one save and not one per field
	 *
	 * A save beside the name, a save beside the details, an add beside the other names and an add
	 * beside the links would be four buttons, four things to remember to press, and no way to
	 * change your mind about any of them once pressed. Somebody editing a record is doing ONE thing, so it
	 * takes one Save and one Cancel, and until Save nothing has happened.
	 *
	 * That means the lists are edited here too. Adding a name puts it in a draft rather than on the
	 * person; removing one takes it out of the draft. Cancel throws the whole draft away, which is
	 * what Cancel has to mean if it is going to mean anything.
	 *
	 * ## Why the fields come from the registry
	 *
	 * Same reason the read view does: so the form and the record cannot disagree about what a field
	 * is called or which fields there are. Adding a field on the server puts it in both.
	 *
	 * ## What it does not edit
	 *
	 * Anything the server declares as not editable: what a file measures, when it was added, how
	 * long it runs. Those are drawn as they read, so the form is still the whole record rather than
	 * a subset somebody has to go somewhere else to see.
	 */
	import { tick, untrack } from 'svelte';
	import { Button, Field, Problem } from '$lib/components/common';
	import FieldEditor from '$lib/components/record/FieldEditor.svelte';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import {
		fields,
		linkFor,
		refusedField,
		saveProblem,
		type FieldDescription,
		type RecordSubject
	} from '$lib/entity/records.svelte';

	interface Props {
		subject: RecordSubject;
		/** The stored values, by field key. Copied into a draft; never written to. */
		values: Record<string, unknown>;
		/** Save the draft. Anything that throws leaves the form up with what was typed still in it,
		 *  under one flat sentence, the caller's own (a `SaveRefused`), or the route's refusal in
		 *  words. See `saveProblem`. */
		onsave: (draft: Record<string, unknown>) => Promise<void>;
		oncancel: () => void;
		/** Names the form for a screen reader. */
		label: string;
		/**
		 * The form's own id, so a Save can be drawn OUTSIDE it.
		 *
		 * `<button form="...">` is how a submit button reaches a form it is not inside, and it is
		 * what lets the header put Save beside Cancel rather than only at the far end of a record
		 * long enough to push it off the screen. The same shape `RecordGrid` uses, for the same
		 * reason: the alternative is lifting the draft into the page, which would put the record's
		 * state in two places.
		 */
		formId?: string;
	}

	let { subject, values, onsave, oncancel, label, formId }: Props = $props();

	/** Unique to this form, so two records open together cannot both claim the same label id. */
	const uid = $props.id();

	const shown = $derived(fields.drawn(subject));

	/* The draft, seeded ONCE from the stored values and never re-seeded.
	 *
	 * Deliberately not derived from `values`. The page re-fetches while this form is open (a
	 * library change, a tag written elsewhere) and a draft that followed those would throw away
	 * whatever somebody had half typed. The form is closed by Save or by Cancel, and either way a
	 * fresh one is built from what the page holds then.
	 *
	 * `untrack` says that in the code rather than only in this comment: without it Svelte warns that
	 * the initialiser captures the first value only, which is precisely the intent, and a warning
	 * left standing because it happens to be wrong here is a warning nobody reads next time.
	 */
	let draft = $state<Record<string, unknown>>(untrack(() => ({ ...values })));

	let busy = $state(false);
	let problem = $state<string | undefined>();
	/** The field the refusal is about, where the route named one on this form: said beside it. */
	let problemAt = $state<string | undefined>();
	/** Where the refusal is said: beside its field, or under the last one. */
	let said = $state<HTMLElement | null>(null);

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true;
		problem = undefined;
		problemAt = undefined;
		try {
			await onsave(cleaned($state.snapshot(draft)));
		} catch (failure) {
			// One flat sentence for anything that went wrong, and the words written for the reader
			// where there are some: the caller's own, or the route's refusal. See `saveProblem`.
			problem = saveProblem(failure);
			const at = refusedField(failure);
			problemAt = at && shown.some((one) => one.key === at) ? at : undefined;
			/* Brought into view: the Save pressed may be the one at the top of the page, a long
			   record away from the sentence saying why nothing changed. */
			await tick();
			said?.scrollIntoView?.({ block: 'nearest' });
		} finally {
			busy = false;
		}
	}

	/* The draft as it should be STORED, rather than as it was typed.
	 *
	 * Every entry is a box, so an entry can be emptied, and an empty other-name is not a name
	 * somebody wanted, it is a row they were finished with. Blanks are dropped and the rest are
	 * trimmed, here rather than in each page's save, so the four screens cannot come to disagree
	 * about what an empty box means.
	 */
	function cleaned(held: Record<string, unknown>): Record<string, unknown> {
		const out: Record<string, unknown> = { ...held };
		for (const one of shown) {
			if (!isList(one)) continue;
			const list = Array.isArray(out[one.key]) ? (out[one.key] as unknown[]) : [];
			out[one.key] = list.map((entry) => asText(entry).trim()).filter(Boolean);
		}
		return out;
	}

	/** A list field is edited as boxes with an adder; everything else takes one control. Used only
	 *  to decide what `cleaned` has to tidy: the editing itself is `FieldEditor`'s. */
	function isList(one: FieldDescription): boolean {
		return one.kind === 'names' || one.kind === 'links';
	}

	/** A list entry as text, whichever shape it arrived in: a bare name, or a link with a url. */
	function asText(one: unknown): string {
		if (typeof one === 'string') return one;
		const held = one as { url?: string; alias?: string; name?: string };
		return held.url ?? held.alias ?? held.name ?? String(one);
	}
</script>

<form id={formId} class="form" onsubmit={save} aria-label={label}>
	<div class="fields">
		{#each shown as one (one.key)}
			<div class="field">
				{#if !one.editable}
					<!-- Drawn as it reads. A box around a file's size invites an edit nothing can
					     honour, and leaving the field out entirely would make the form a subset of
					     the record somebody has to close it to see. -->
					<span class="label">{one.label}</span>
					<div class="fixed">
						<RecordValue kind={one.kind} value={draft[one.key]} link={linkFor(one, draft)} />
					</div>
				{:else if one.kind === 'tags'}
					<!-- Not wrapped in `Field`. That draws a `<label for=...>` and hands its control an
					     id to wear, and the tag editor is a row of chips with an adder rather than one
					     box, so the label would point at nothing, which is worse for a screen reader
					     than no label at all. It names itself instead. -->
					<span class="label" id="{uid}-{one.key}">{one.label}</span>
					<div class="tags" role="group" aria-labelledby="{uid}-{one.key}">
						<FieldEditor
							field={one}
							value={draft[one.key]}
							onchange={(next) => (draft[one.key] = next)}
						/>
					</div>
					{#if one.help}<p class="help">{one.help}</p>{/if}
				{:else}
					<Field label={one.label} help={one.help ?? undefined}>
						{#snippet control({ id, describedBy })}
							<FieldEditor
								field={one}
								{id}
								{describedBy}
								value={draft[one.key]}
								onchange={(next) => (draft[one.key] = next)}
								self={typeof values.name === 'string' ? values.name : undefined}
							/>
						{/snippet}
					</Field>
				{/if}
				{#if problemAt === one.key}
					<div bind:this={said}><Problem message={problem} /></div>
				{/if}
			</div>
		{/each}
	</div>

	{#if !problemAt}
		<div bind:this={said}><Problem message={problem} /></div>
	{/if}

	<!-- One Save and one Cancel, at the end, for the whole record. Until Save nothing has changed.

	     On the right, and in the order the top of the screen draws them: Cancel then Save, with the
	     primary at the far edge, so the two ends of one edit offer the same pair the same way
	     round. Buttons and actions sit on the right of a row or a card. -->
	<div class="close">
		<Button type="button" onclick={oncancel} disabled={busy}>Cancel</Button>
		<Button type="submit" tone="primary" icon="save" {busy}>Save</Button>
	</div>
</form>

<style>
	.form {
		display: flex;
		flex-direction: column;
		gap: var(--space-6);
	}

	/* ONE column, and every control the same width as every other.
	 *
	 * A filling grid would put a name and a tag row in an 18rem column while a paragraph and a list
	 * of links took the whole width, so a form of five fields would draw boxes at two different
	 * lengths with nothing meaning anything by the difference. A form is read top to bottom and the
	 * left edge of every box lining up with the one above it is what makes that possible.
	 */
	.fields {
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}

	.field {
		min-inline-size: 0;
	}

	/* The same weight and colour `Field` gives its own label, so a field this form labels by hand
	   sits at exactly the height of one it does not. */
	.label {
		display: block;
		font: var(--text-label);
		color: var(--sift-ink-2);
		margin-block-end: var(--space-2);
	}

	.help {
		margin: var(--space-2) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* A field the server says is not editable. Shown at the same size as the rest so the form reads
	   as one thing rather than as boxes with gaps between them. */
	.fixed {
		padding-block: var(--space-2);
	}

	/* The group the hand-written label belongs to. The tag editor names and dresses itself, so all
	   this needs is a floor of nothing: it is a wrapping row of chips, and a column that cannot
	   shrink pushes the form wider than the panel it sits in. */
	.tags {
		min-inline-size: 0;
	}

	.close {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-3);
		padding-block-start: var(--space-4);
		border-block-start: 1px solid var(--sift-line);
	}
</style>
