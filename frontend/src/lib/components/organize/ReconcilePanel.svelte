<script lang="ts">
	/* Where a stash-box disagrees, one field at a time: yours (which stays if nothing is pressed)
	   beside theirs, as a table at the history thread's row height. */
	import { Button, Chip, Problem } from '$lib/components/common';
	import { decided } from '$lib/organize/organize.svelte';
	import { fields } from '$lib/entity/records.svelte';
	import {
		boxesOf,
		disagreementsOf,
		problemFrom,
		settle,
		type Disagreement
	} from '$lib/entity/reconcile.svelte';

	interface Props {
		/** Which record: the questions are asked only where they can be answered. */
		subject: string;
		localId: string;
		/** Told when the list changes, so the line above it can say how many and which boxes. */
		onchange?: (waiting: number, boxes: string[]) => void;
		/**
		 * Told when a press wrote a field (or that was undone), so the page re-reads the record.
		 */
		onwritten?: () => void;
	}

	let { subject, localId, onchange, onwritten }: Props = $props();

	/* This record's rows only; a library-wide read would cost seconds per page view. */
	let items = $state<Disagreement[]>([]);
	let failed = $state<string | null>(null);

	/* Rows whose answer is in flight, so a read already on its way cannot bring them back. */
	const sending = new Set<string>();

	/* Which read is current, so a slow answer for a record left behind is dropped. */
	let generation = 0;

	$effect(() => {
		void load();
	});

	function key(one: Disagreement): string {
		return `${one.subject}:${one.local_id}:${one.box_id}:${one.key}`;
	}

	async function load() {
		const mine = ++generation;
		failed = null;
		try {
			const found = await disagreementsOf(subject, localId);
			if (mine !== generation) return;
			items = found.filter((one) => !sending.has(key(one)));
		} catch (error) {
			if (mine !== generation) return;
			// The rows go with the failure.
			items = [];
			failed = problemFrom(error);
		}
		// Told in every case, so the counts above never outlive a failed read.
		onchange?.(items.length, boxesOf(items));
	}

	/* One press: the row leaves immediately and returns only if the server refuses. */
	function choose(one: Disagreement, takeTheirs: boolean) {
		const id = key(one);
		if (sending.has(id)) return;
		sending.add(id);
		const at = items.findIndex((row) => key(row) === id);
		items = items.filter((row) => key(row) !== id);
		failed = null;
		onchange?.(items.length, boxesOf(items));
		void send(one, takeTheirs, at);
	}

	async function send(one: Disagreement, takeTheirs: boolean, at: number) {
		const id = key(one);
		try {
			const done = await settle(one, takeTheirs);
			const wrote = (done.fields ?? 0) > 0;
			if (wrote) onwritten?.();
			const said = done.pieces?.length ? done.pieces : done.said || saidOf(one, takeTheirs);
			decided(said, done.decision_id ?? null, {
				after: async () => {
					await load();
					if (wrote) onwritten?.();
				}
			});
			// Read again behind it: taking one answer can change another box's row.
			await load();
		} catch (error) {
			// Back where it stood, only if this record is still on screen.
			if (one.subject === subject && one.local_id === localId) {
				if (!items.some((row) => key(row) === id)) {
					const place = at < 0 ? items.length : Math.min(at, items.length);
					items = [...items.slice(0, place), one, ...items.slice(place)];
				}
				failed = problemFrom(error);
				onchange?.(items.length, boxesOf(items));
			}
		} finally {
			sending.delete(id);
		}
	}

	/** The toast's words when the server sends no `said`: the field, both values and the box. */
	function saidOf(one: Disagreement, takeTheirs: boolean): string {
		const field = labelOf(one).toLowerCase();
		return takeTheirs
			? `Took ${one.box_name}'s ${field} for ${one.name}: ${theirsOf(one)}, where you had ${mineOf(one)}`
			: `Kept your ${field} for ${one.name}: ${mineOf(one)}, not ${one.box_name}'s ${theirsOf(one)}`;
	}

	/** What one field is called, from the registry every record screen reads. */
	function labelOf(one: Disagreement): string {
		return fields.one(one.subject, one.key)?.label ?? one.key;
	}

	/* Each side in the server's words, as History says it; the raw value only where it has none. */
	function mineOf(one: Disagreement): string {
		return one.mine_said ?? short(one.mine);
	}

	function theirsOf(one: Disagreement): string {
		return one.theirs_said ?? short(one.theirs);
	}

	/** A value as one short line; nothing is said in words, never a blank half. */
	function short(value: unknown): string {
		if (value === null || value === undefined || value === '') return 'nothing';
		if (Array.isArray(value)) return value.map((one) => String(one)).join(', ') || 'nothing';
		return String(value);
	}
</script>

<section class="pile" aria-label="Where a stash-box disagrees">
	<!-- No heading of its own: the line above already says what these are. -->
	<Problem message={failed} />

	<!-- Nothing drawn while waiting: most records have no box, so a skeleton would lie. -->

	{#if items.length === 0}
		<!-- Nothing at all when nothing disagrees. -->
	{:else}
		<!-- A real table: columns line up, and "Yours" and "Theirs" are said once. -->

		<table>
			<thead>
				<tr>
					<th scope="col">Field</th>
					<th scope="col">Stash-box</th>
					<!-- Yours first because it is the one that stays if nothing is pressed. -->
					<th scope="col">Yours</th>
					<th scope="col">Theirs</th>
					<!-- The answers column needs no word: both buttons say what they do. -->
					<th scope="col"></th>
				</tr>
			</thead>
			<tbody>
				{#each items as one (key(one))}
					<tr>
						<th scope="row">{labelOf(one)}</th>
						<td><Chip size="sm" tone="quiet" inert>{one.box_name}</Chip></td>
						<td class="value">{mineOf(one)}</td>
						<td class="value">{theirsOf(one)}</td>
						<!--
						Keeping yours is the quiet one: it writes nothing. Small, at a history row's
						height.
						-->
						<td class="acts">
							<div class="both">
								<Button type="button" tone="quiet" size="small" onclick={() => choose(one, false)}>
									Keep yours
								</Button>
								<Button type="button" tone="primary" size="small" onclick={() => choose(one, true)}>
									Take theirs
								</Button>
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</section>

<style>
	/* The block in the History tab's side column; the column and the fold are EntityHistory's. */
	.pile {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* Collapsed borders make one hairline; auto layout gives the slack to the value columns. */
	table {
		inline-size: 100%;
		border-collapse: collapse;
	}

	/*
	 * The vertical inset makes a 50px row with a small button; end padding only, flush both ends.
	 */
	th,
	td {
		padding-block: var(--space-3);
		padding-inline-end: var(--space-3);
		text-align: start;
		vertical-align: middle;
		/* Only the two value cells wrap (below). */
		white-space: nowrap;
	}

	th:last-child,
	td:last-child {
		padding-inline-end: 0;
	}

	thead th {
		font: var(--text-label);
		color: var(--sift-ink-3);
		padding-block: var(--space-1);
	}

	/* Hairlines under the header and between rows, on the cells, and no box around. */
	thead tr > * {
		border-block-end: 1px solid var(--sift-line);
	}

	tbody tr + tr > * {
		border-block-start: 1px solid var(--sift-line);
	}

	/* The field's name, quiet beside the answers. */
	tbody th {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Both answers in one size, wrapping anywhere inside their own cells. */
	.value {
		color: var(--sift-ink);
		font: var(--text-body);
		white-space: normal;
		overflow-wrap: anywhere;
	}

	/* The answers at the row's end; a flex td would leave the table's alignment. */
	.acts {
		text-align: end;
	}

	.both {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
