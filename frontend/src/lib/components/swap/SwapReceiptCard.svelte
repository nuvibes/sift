<script lang="ts">
	/*
	 * A swap's receipt: the manifest signed. The same card is the Done screen and what History opens
	 * for the swap later, read from what this side kept when the swap ended.
	 *
	 * From the top: the heavy number and its context ("1,065 files landed, 30 GB, in 21 min"),
	 * from whom and how; a row per group in the order Sift files things, with how each was filed and
	 * its state at the end; what was not taken and why; where it landed; and at the foot Open the
	 * folder and Undo this swap. After an Undo the card says what was removed, what it kept and
	 * why, and the rows read as taken back; files another program held open keep the Undo.
	 *
	 * One clock: the time beside the number runs from the first byte to the last, less the time
	 * cut off, and the average is over it; the wait before the first file is its own line.
	 */
	import { Badge, Button, DataRow, DataRows, Note, Panel, Problem } from '$lib/components/common';
	import type { Column } from '$lib/components/common/DataRows.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import { iconOf, pageOf, type EntityKind } from '$lib/entity/related.svelte';
	import { size } from '$lib/library/facts';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import {
		backWords,
		contextWords,
		filedWords,
		groupName,
		headlineWords,
		howWords,
		notTakenWords,
		undoneWords,
		waitWords,
		type ReceiptGroup,
		type ReceiptKind,
		type SwapReceipt
	} from './receipt';

	interface Props {
		receipt: SwapReceipt;
		/** Undo this swap, asked first by the caller; absent, no Undo. */
		onundo?: () => void;
		/** Open the folder it landed in; absent, no press. */
		onopen?: () => void;
		/** Waiting on the Undo. */
		undoing?: boolean;
		/** Where the Undo was refused, said under its press. */
		problem?: string | null;
	}

	let { receipt, onundo, onopen, undoing = false, problem = null }: Props = $props();

	/* The order Sift files things in, which the rows keep. */
	const ORDER: readonly ReceiptKind[] = [
		'person',
		'site',
		'collection',
		'photo_set',
		'songs',
		'loose'
	];

	/* The kinds with a page of their own, by the entity they are. */
	const ENTITY: Partial<Record<ReceiptKind, EntityKind>> = {
		person: 'person',
		site: 'site',
		collection: 'collection',
		photo_set: 'photo_set'
	};

	function markOf(kind: ReceiptKind): IconName {
		const entity = ENTITY[kind];
		if (entity) return iconOf(entity);
		return kind === 'songs' ? iconOf('song') : 'article';
	}

	function hrefOf(group: ReceiptGroup): string | null {
		const entity = ENTITY[group.kind];
		return entity && group.id && !receipt.undo ? pageOf(entity, group.id) : null;
	}

	const rows = $derived(
		[...receipt.groups].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind))
	);
	const noun = $derived(receipt.two_way ? 'exchange' : 'swap');
	const notTaken = $derived(notTakenWords(receipt));
	const back = $derived(backWords(receipt));
	const wait = $derived(waitWords(receipt));
	/* Again after an Undo that met files in use: the server says when (`can_undo`). */
	const canUndo = $derived(receipt.can_undo && onundo !== undefined);

	const COLUMNS: readonly Column[] = [
		{ id: 'what', width: 'minmax(0, 1fr)' },
		{ id: 'files', width: '6rem', align: 'end' },
		{ id: 'state', width: '7rem', align: 'end' }
	];
	const PHONE: readonly Column[] = [
		{ id: 'what', width: 'minmax(0, 1fr)' },
		{ id: 'state', width: '5rem', align: 'end' }
	];
</script>

<div class="receipt-card">
	<Panel label="The {noun}'s receipt">
		<header class="head">
			<p class="headline">{headlineWords(receipt)}</p>
			<p class="number">
				<span class="figure">{receipt.files.toLocaleString()}</span>
				<span class="context">{contextWords(receipt)}</span>
			</p>
			<p class="how">{howWords(receipt)}</p>
			{#if back}<p class="how">{back}</p>{/if}
			{#if wait}<p class="how">{wait}</p>{/if}
		</header>

		{#if rows.length > 0}
			{#key phoneWidth.yes}
				<DataRows
					items={rows}
					key={(one) => `${one.way}-${one.kind}-${one.id ?? groupName(one)}`}
					label="What the {noun} carried"
					columns={phoneWidth.yes ? PHONE : COLUMNS}
				>
					{#snippet row(one)}
						<DataRow compact cells={phoneWidth.yes ? { what, state } : { what, files, state }} />
						{#snippet what()}
							{@const href = hrefOf(one)}
							<span class="group" class:taken-back={!!receipt.undo}>
								<span class="mark"><Icon name={markOf(one.kind)} size={16} /></span>
								<span class="said">
									{#if href}
										<a class="named" {href}>{groupName(one)}</a>
									{:else}
										<span class="name">{groupName(one)}</span>
									{/if}
									<span class="filed">{filedWords(one)}</span>
								</span>
							</span>
						{/snippet}
						{#snippet files()}
							<span class="data"
								>{one.files.toLocaleString()} {'\u00b7'} {size(one.bytes) ?? ''}</span
							>
						{/snippet}
						{#snippet state()}
							{#if receipt.undo && one.way === 'landed'}
								<Badge state="canceled" label="Undone" />
							{:else}
								<Badge state="done" label={one.way === 'landed' ? 'Landed' : 'Sent'} />
							{/if}
						{/snippet}
					{/snippet}
				</DataRows>
			{/key}
		{/if}

		{#if notTaken.length > 0}
			<p class="fact"><span class="label">Not taken</span> {notTaken.join(' \u00b7 ')}</p>
		{/if}
		{#if receipt.folder}
			<p class="fact">
				<span class="label">Where</span>
				<span class="path">{receipt.folder.path}</span>
			</p>
		{/if}

		{#if receipt.undo}
			<Note icon="undo">{undoneWords(receipt.undo)}</Note>
		{/if}

		{#if (receipt.folder && onopen && !receipt.undo) || canUndo}
			<div class="finish">
				{#if receipt.folder && onopen && !receipt.undo}
					<Button onclick={onopen}>Open the folder</Button>
				{/if}
				{#if canUndo}
					<Button icon="undo" tone="danger-quiet" busy={undoing} onclick={onundo}
						>Undo this {noun}</Button
					>
				{/if}
			</div>
			<Problem message={problem} />
		{/if}
	</Panel>
</div>

<style>
	/* A page of reading at most: a wide window gets margin, not a longer row. */
	.receipt-card {
		max-inline-size: var(--page-measure);
	}

	.head {
		display: grid;
		gap: var(--space-1);
	}

	.headline,
	.number,
	.how,
	.fact {
		margin: 0;
	}

	.headline {
		font: var(--text-h2);
		color: var(--sift-ink);
	}

	/* The heavy number and its context on one baseline, the context wrapping under on a phone. */
	.number {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-2);
	}

	.figure {
		font: var(--text-display-lg);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}

	.context {
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	.how {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.group {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.mark {
		display: flex;
		flex: none;
		color: var(--sift-ink-3);
	}

	.said {
		display: flex;
		flex-wrap: wrap;
		column-gap: var(--space-2);
		min-inline-size: 0;
	}

	.name,
	.named {
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}

	.named {
		color: var(--sift-accent-text);
		text-decoration: none;
		text-underline-offset: 2px;
		border-radius: var(--radius-sm);
	}

	.named:hover,
	.named:focus-visible {
		text-decoration: underline;
	}

	.filed {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* Taken back is still something that happened: quieter, never struck through. */
	.group.taken-back .name {
		color: var(--sift-ink-3);
	}

	.data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-2);
		white-space: nowrap;
	}

	.fact {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		overflow-wrap: anywhere;
	}

	.label {
		font: var(--text-label);
		color: var(--sift-ink-3);
		margin-inline-end: var(--space-2);
	}

	.path {
		font: var(--text-data);
	}

	.finish {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
