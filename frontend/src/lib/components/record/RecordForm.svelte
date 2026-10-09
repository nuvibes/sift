<script lang="ts">
	/*
	 * Every field of a record edited together and saved once: the lists too, in the draft, so
	 * Cancel throws the lot away. Fields come from the registry; what is not editable is drawn as
	 * it reads.
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
		values: Record<string, unknown>;
		/** Anything that throws leaves the form up with one flat sentence (`saveProblem`). */
		onsave: (draft: Record<string, unknown>) => Promise<void>;
		oncancel: () => void;
		label: string;
		/** So a Save can be drawn outside it (`<button form>`), as `RecordGrid` does. */
		formId?: string;
	}

	let { subject, values, onsave, oncancel, label, formId }: Props = $props();

	/** Unique, so two records open together do not share label ids. */
	const uid = $props.id();

	const shown = $derived(fields.drawn(subject));

	/* Seeded ONCE and never re-seeded (hence `untrack`): a re-fetch must not throw away typing. */
	let draft = $state<Record<string, unknown>>(untrack(() => ({ ...values })));

	let busy = $state(false);
	let problem = $state<string | undefined>();
	let problemAt = $state<string | undefined>();
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
			problem = saveProblem(failure);
			const at = refusedField(failure);
			problemAt = at && shown.some((one) => one.key === at) ? at : undefined;
			/* Brought into view: the Save pressed may be a long record away. */
			await tick();
			said?.scrollIntoView?.({ block: 'nearest' });
		} finally {
			busy = false;
		}
	}

	/* Blank entries dropped, the rest trimmed, here rather than in each page's save. */
	function cleaned(held: Record<string, unknown>): Record<string, unknown> {
		const out: Record<string, unknown> = { ...held };
		for (const one of shown) {
			if (!isList(one)) continue;
			const list = Array.isArray(out[one.key]) ? (out[one.key] as unknown[]) : [];
			out[one.key] = list.map((entry) => asText(entry).trim()).filter(Boolean);
		}
		return out;
	}

	/** Only to decide what `cleaned` tidies; the editing is `FieldEditor`'s. */
	function isList(one: FieldDescription): boolean {
		return one.kind === 'names' || one.kind === 'links';
	}

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
					<!-- Not editable: drawn as it reads, so the form is still the whole record. -->
					<span class="label">{one.label}</span>
					<div class="fixed">
						<RecordValue kind={one.kind} value={draft[one.key]} link={linkFor(one, draft)} />
					</div>
				{:else if one.kind === 'tags'}
					<!--
					Not in `Field`: its `<label for>` would point at nothing in a row of chips.
					-->
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

	<!-- One Save and one Cancel for the record, Cancel then Save at the end, as at the top. -->
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

	/* ONE column, every control one width, so the left edges line up. */
	.fields {
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}

	.field {
		min-inline-size: 0;
	}

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

	.fixed {
		padding-block: var(--space-2);
	}

	/* A floor of nothing, so the row of chips can shrink. */
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
