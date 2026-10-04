<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'BatchRename',
		category: 'composition',
		role: 'the sheet that renames many files from one template, with every new name shown before anything moves',
		basis: 'own',
		states: ['planning', 'clashes', 'refused', 'as a task']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: not a control. It is a form on the one dialog (`Modal`, which is bits-ui),
	   built from the shared field, box, chip and select. */
	/*
	 * Renaming a batch of files from one template.
	 *
	 * The words are pressed in as chips, the way a Site's naming rule is written, and every new name
	 * is shown before anything moves: the server plans the whole batch against what each folder
	 * already holds, marks every name that clashes, and the sheet draws that plan and the count. What
	 * a clash does is the person's choice: the next number, or leave that file alone.
	 *
	 * Nothing is renamed until Rename is pressed, and then the batch is ONE thing in the record,
	 * with one Undo that puts every file back. A large batch runs as a task, and the toast says
	 * where to follow it.
	 */
	import { Button, Chip, Field, Modal, Select, TextInput } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import { ApiError } from '$lib/api/client';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { place } from '$lib/components/common/toast-pieces';
	import { undo as undoDecision, undoneLine } from '$lib/organize/organize.svelte';
	import { counted, filesSaid } from '$lib/entity/entity-counts';
	import {
		MOST_FILES,
		ON_CLASH,
		STARTING_TEMPLATE,
		WORD_ORDER,
		startingTemplate,
		applyRename,
		folderFiles,
		insertWord,
		previewRename,
		rowNote,
		summary,
		type OnClash,
		type RenamePreview
	} from './batch-rename';

	interface Props {
		open?: boolean;
		/** The files, in the order they are shown, which is the order `{n}` counts in. */
		assetIds?: readonly string[];
		/** Or a folder: every file it shows, in name order. Read when the sheet opens. */
		folderId?: string | null;
		/** Told once files have new names, so whoever drew them can say so. */
		ondone?: () => void;
		/** Where the plan comes from. The server's, always, except on a page that only shows the
		 *  sheet and must write nothing. */
		preview?: typeof previewRename;
	}

	let {
		open = $bindable(false),
		assetIds = [],
		folderId = null,
		ondone,
		preview = previewRename
	}: Props = $props();

	let template = $state(STARTING_TEMPLATE);
	let onClash = $state<OnClash>('number');
	let plan = $state<RenamePreview | null>(null);
	let problem = $state<string | undefined>(undefined);
	let busy = $state(false);
	let files = $state<string[]>([]);
	let box = $state<HTMLInputElement | null>(null);

	/* Every open starts from the same place: a template left over from the last batch is a
	   template nobody chose for this one. */
	$effect(() => {
		if (!open) return;
		template = STARTING_TEMPLATE;
		onClash = 'number';
		plan = null;
		problem = undefined;
		void gather();
	});

	async function gather() {
		let gathered: string[];
		if (folderId) {
			try {
				gathered = await folderFiles(folderId);
			} catch (failure) {
				problem = said(failure, "The folder's files couldn't be read.");
				return;
			}
		} else gathered = assetIds.slice(0, MOST_FILES);
		files = gathered;
		// One file starts from its own name: "{n}" would give it a " 1" nobody asked for. Written
		// and never read here, since this runs inside the effect that opens the sheet.
		template = startingTemplate(gathered.length);
	}

	/* Which plan was asked for last. The box asks on every pause in typing and the answers are not
	   bound to come back in order: only the newest question's answer may reach the screen. */
	let asked = 0;
	let waiting: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		const words = template;
		const rule = onClash;
		const batch = files;
		if (!open || batch.length === 0) return;
		clearTimeout(waiting);
		waiting = setTimeout(() => void ask(batch, words, rule), PAUSE_MS);
		return () => clearTimeout(waiting);
	});

	/** How long the box waits after the last key before it asks. */
	const PAUSE_MS = 250;

	async function ask(batch: string[], words: string, rule: OnClash) {
		const mine = ++asked;
		if (!words.trim()) {
			problem = 'Put at least one word or some text in the box.';
			return;
		}
		try {
			const answer = await preview(batch, words, rule);
			if (mine !== asked) return;
			plan = answer;
			problem = undefined;
		} catch (failure) {
			if (mine !== asked) return;
			problem = said(failure, "Sift couldn't work out the new names.");
		}
	}

	function said(failure: unknown, fallback: string): string {
		return failure instanceof ApiError && failure.detail ? failure.detail : fallback;
	}

	/** The words the server fills, in the order people reach for them. */
	const words = $derived(
		plan
			? WORD_ORDER.filter((word) => word in plan!.words && !(one && word === 'n'))
			: ([] as string[])
	);

	function press(word: string) {
		const from = box?.selectionStart ?? template.length;
		const to = box?.selectionEnd ?? from;
		const put = insertWord(template, from, to, word);
		template = put.text;
		queueMicrotask(() => {
			box?.focus();
			box?.setSelectionRange(put.caret, put.caret);
		});
	}

	/* The sheet's words follow the count: one file has a name, several have names. */
	const one = $derived(files.length === 1);
	const heading = $derived(one ? 'Rename this file' : `Rename ${filesSaid(files.length)}`);
	const lede = $derived(
		one
			? 'This file gets a name made from the words below. Only the name changes; the file stays in its folder.'
			: 'Each file gets a name made from the words below. Only the names change; every file stays in its folder.'
	);

	const ready = $derived(plan !== null && plan.renaming > 0 && !busy && !problem);

	async function rename() {
		if (!ready) return;
		busy = true;
		try {
			const done = await applyRename(files, template, onClash);
			open = false;
			if (done.job_id) {
				toasts.show([
					`Renaming ${filesSaid(plan?.renaming ?? files.length)} as a task. Follow it in `,
					place('Activity', '/settings/tasks?show=now')
				]);
			} else if (done.renamed > 0) {
				const receipt = done.receipt_id;
				toasts.show(`Renamed ${filesSaid(done.renamed)}`, {
					tone: 'success',
					action: receipt ? { label: 'Undo', run: () => void undo(receipt) } : undefined
				});
			} else {
				toasts.show(done.reason ?? 'No file got a new name', { tone: 'error' });
			}
			ondone?.();
		} catch (failure) {
			problem = said(failure, "The files couldn't be renamed.");
		} finally {
			busy = false;
		}
	}

	async function undo(receiptId: string) {
		try {
			/* Through the record's own Undo, and said from its counts: a batch where some files
			   were renamed again since goes back only in part, and says how many. */
			const put = await undoDecision(receiptId);
			toasts.show(undoneLine(put, 'Put every name back', 'There was nothing left to undo'));
			if (put.undone) ondone?.();
		} catch (failure) {
			toasts.show(said(failure, "That couldn't be undone"), { tone: 'error' });
		}
	}
</script>

<Modal bind:open title={heading} description={lede} sheetClass="batch-rename">
	<form
		id="batch-rename"
		onsubmit={(event) => {
			event.preventDefault();
			void rename();
		}}
	>
		<Field
			label={one ? 'New name' : 'New names'}
			help="Press a word to put it where the cursor is. Anything else you type stays as it is."
			error={problem}
		>
			{#snippet control({ id, describedBy, invalid })}
				<!-- svelte-ignore a11y_autofocus: the sheet exists to be typed into -->
				<TextInput
					{id}
					bind:value={template}
					bind:element={box}
					{describedBy}
					{invalid}
					disabled={busy}
					spellcheck="false"
					autocomplete="off"
					autofocus
				/>
			{/snippet}
		</Field>

		{#if words.length > 0}
			<dl class="words">
				{#each words as word (word)}
					<dt>
						<Chip size="sm" shape="square" tone="neutral" onselect={() => press(word)}>
							<span class="word">{'{' + word + '}'}</span>
						</Chip>
					</dt>
					<dd>{plan?.words[word]}</dd>
				{/each}
			</dl>
		{/if}

		<Field label="When a name is taken">
			{#snippet control({ id, describedBy })}
				<Select
					{id}
					{describedBy}
					value={onClash}
					options={ON_CLASH}
					onValueChange={(value) => (onClash = value as OnClash)}
				/>
			{/snippet}
		</Field>
	</form>

	{#if plan}
		<p class="summary" role="status">{summary(plan, onClash)}</p>
		<ol class="plan" aria-label="The new names">
			{#each plan.rows as row (row.asset_id)}
				{@const note = rowNote(row)}
				<li class="row" class:kept={row.state !== 'renamed' && row.state !== 'numbered'}>
					<!-- A file that cannot be renamed may be one this sheet was never told the name of. -->
					<span class="before">{row.before || 'One file'}</span>
					<Icon name="arrow_forward" size={16} />
					<span class="after">{row.after || row.before}</span>
					<!-- Always drawn, empty or not: the row lends four parts to four columns. -->
					<span class="note">{note ?? ''}</span>
				</li>
			{/each}
		</ol>
		{#if plan.total > plan.rows.length}
			<p class="more">
				The first {counted(plan.rows.length)} of {filesSaid(plan.total)} are shown. The rest follow the
				same rule.
			</p>
		{/if}
		{#if plan.as_task}
			<p class="more">
				This many files, or a folder on another computer, are renamed as a task you can follow in
				Activity.
			</p>
		{/if}
	{/if}

	{#snippet footer()}
		<div class="buttons">
			<Button type="button" onclick={() => (open = false)} disabled={busy}>Cancel</Button>
			<Button type="submit" form="batch-rename" tone="primary" icon="edit_square" disabled={!ready}>
				{plan && plan.renaming > 0 ? `Rename ${filesSaid(plan.renaming)}` : 'Rename'}
			</Button>
		</div>
	{/snippet}
</Modal>

<style>
	:global(.batch-rename) {
		/* The width only. `.sheet` clamps it to the window. */
		--sheet-inline: 44rem;
	}

	.words {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: var(--space-2) var(--space-3);
		align-items: baseline;
		margin: 0 0 var(--space-4);
		/* The words belong to the field above, so they stand off its help by the field's own gap. */
		margin-block-start: var(--space-2);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	dt,
	dd {
		margin: 0;
	}

	/* A word is data: the braces and the mono are what a reader then finds in the box. */
	.word {
		font: var(--text-data);
	}

	.summary {
		margin: var(--space-4) 0 var(--space-2);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.plan {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) minmax(0, 14rem);
		gap: var(--space-1) var(--space-2);
		align-items: center;
		margin: 0;
		padding: 0;
		list-style: none;
		font: var(--text-body-sm);
	}

	/* Each row lends its four parts to the list's columns, so every arrow stands in one column. */
	.row {
		display: contents;
	}

	.before,
	.after {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.before {
		color: var(--sift-ink-3);
	}

	.after {
		color: var(--sift-ink);
	}

	.kept .after {
		color: var(--sift-ink-3);
	}

	/* A reason can be a sentence; it wraps in its own column rather than squeezing the names. */
	.note {
		color: var(--sift-ink-3);
	}

	.more {
		margin: var(--space-2) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
