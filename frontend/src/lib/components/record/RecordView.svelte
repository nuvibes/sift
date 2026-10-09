<script lang="ts">
	/*
	 * What a thing IS, drawn from the server's one description of its fields, empty ones as a dash.
	 * Not named `Record`, which would shadow TypeScript's own type.
	 */
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { fields, linkFor, type RecordSubject } from '$lib/entity/records.svelte';

	interface Props {
		subject: RecordSubject;
		values: Record<string, unknown>;
		/** `more`, the panel beside the name; `all` where no summary sits above it. */
		showing?: 'more' | 'all';
		label: string;
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
	/* `auto-fill`, not `auto-fit`, which would stretch two facts across a wide window. */
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

	dd {
		margin: 0;
		min-inline-size: 0;
	}
</style>
