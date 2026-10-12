<script lang="ts">
	/*
	 * The Manifest: what a swap carries, as one list grouped the way Sift files things (People, Sites,
	 * Collections, Photo Sets, Songs, then the other files). It is written before anything moves and
	 * every step draws it again; only the state column changes. A group folds open to its files: the
	 * sample the session carries first, then a page at a time, asked once per opening and never by
	 * the poll. On a phone a group's files open as a sheet.
	 *
	 * With `choose`, the guest's offer: a tick per group and per file, the after number, and the
	 * rows already here unticked with why. A row kept out stays on the list in the danger colour.
	 */
	import { goto } from '$app/navigation';
	import type { SvelteSet } from 'svelte/reactivity';
	import {
		Avatar,
		Badge,
		Button,
		Checkbox,
		DataRow,
		DataRows,
		Modal,
		type BadgeState
	} from '$lib/components/common';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import { size } from '$lib/library/facts';
	import {
		afterNumber,
		fileFacts,
		filesAndBytes,
		groupContext,
		groupName,
		haveWords,
		heldFileWords,
		inFilingOrder,
		joined,
		kindIcon,
		landedHref,
		leftOutWords,
		oneThing,
		pillOf,
		stateOf
	} from './manifest';
	import { GROUP_PAGE, groupFiles } from './swap';
	import type { Crossing, ManifestFile, ManifestGroup } from './wire';

	interface Props {
		groups: readonly ManifestGroup[];
		/** What the list is, for a screen reader. */
		label: string;
		/** The session, to ask for a group's files past the sample. */
		sessionId?: string;
		/** The guest's answer as it stands: the groups and the files left unticked. */
		choose?: { skipped: SvelteSet<string>; unticked: SvelteSet<string> } | null;
		/** The file crossing now, drawn on its group's row. */
		now?: Crossing | null;
	}

	let { groups, label, sessionId, choose = null, now = null }: Props = $props();

	const rows = $derived(inFilingOrder(groups));
	const single = $derived(choose ? null : oneThing(rows));

	const TONES: Record<ReturnType<typeof pillOf>['tone'], BadgeState> = {
		queued: 'queued',
		running: 'running',
		done: 'done',
		canceled: 'canceled',
		failed: 'failed'
	};

	function ticked(group: ManifestGroup): boolean | null {
		return choose ? !choose.skipped.has(group.key) : null;
	}

	function canTick(group: ManifestGroup): boolean {
		return choose !== null && !group.left_out && group.to_take > 0;
	}

	function tickGroup(group: ManifestGroup, on: boolean): void {
		if (!choose) return;
		if (on) choose.skipped.delete(group.key);
		else choose.skipped.add(group.key);
	}

	function tickFile(file: ManifestFile, on: boolean): void {
		if (!choose) return;
		if (on) choose.unticked.delete(file.key);
		else choose.unticked.add(file.key);
	}

	/* The groups folded open, and the files read for each past its sample. */
	let open = $state<string | null>(null);
	let pages = $state<Record<string, { files: ManifestFile[]; total: number }>>({});
	let reading = $state(false);

	function filesOf(group: ManifestGroup): ManifestFile[] {
		return pages[group.key]?.files ?? group.sample;
	}

	function moreOf(group: ManifestGroup): number {
		const page = pages[group.key];
		return page ? page.total - page.files.length : group.more;
	}

	async function readMore(group: ManifestGroup): Promise<void> {
		if (!sessionId || reading) return;
		reading = true;
		const have = filesOf(group);
		try {
			const page = await groupFiles(sessionId, group.key, have.length, GROUP_PAGE);
			pages = { ...pages, [group.key]: { files: [...have, ...page.files], total: page.total } };
		} catch {
			// Out of reach for now: the sample stays, and the press asks again.
		} finally {
			reading = false;
		}
	}

	function toggle(group: ManifestGroup): void {
		open = open === group.key ? null : group.key;
	}

	/* A track for Open what landed only while a landed group has a page to open; on a phone the
	   press stands under the name instead, leaving the row its width. */
	const openable = $derived(
		!phoneWidth.yes &&
			rows.some((one) => stateOf(one, null) === 'landed' && landedHref(one) !== null)
	);

	const sheetGroup = $derived(
		phoneWidth.yes && open ? (rows.find((one) => one.key === open) ?? null) : null
	);

	const COLUMNS = $derived([
		{ id: 'what', width: 'minmax(0, 1fr)' },
		{ id: 'size', width: '11rem', align: 'end' as const },
		...(choose ? [{ id: 'after', width: '8rem', align: 'end' as const }] : []),
		{ id: 'state', width: '9rem', align: 'end' as const }
	]);
	const PHONE_COLUMNS = [
		{ id: 'what', width: 'minmax(0, 1fr)' },
		{ id: 'state', width: '7rem', align: 'end' as const }
	];
</script>

{#snippet picture(group: ManifestGroup)}
	{#if group.kind === 'person' || group.kind === 'site'}
		<span class="face"><Avatar name={groupName(group)} shape="face" decorative /></span>
	{:else}
		<span class="glyph"><Icon name={kindIcon(group.kind)} size={20} /></span>
	{/if}
{/snippet}

{#snippet pill(group: ManifestGroup)}
	{@const said = pillOf(stateOf(group, ticked(group)), group)}
	<Badge state={TONES[said.tone]} label={said.word} />
{/snippet}

{#snippet fileTick(group: ManifestGroup, file: ManifestFile)}
	{#if choose}
		<Checkbox
			state={file.held || choose.unticked.has(file.key) || choose.skipped.has(group.key)
				? 'off'
				: 'on'}
			disabled={file.held !== null || choose.skipped.has(group.key)}
			label="Take {file.title}"
			onchange={(next) => tickFile(file, next === 'on')}
		/>
	{/if}
{/snippet}

<!-- Under its group, on the group's own columns: the title under the name, the facts under the
     size. In the phone's sheet, a plain list. -->
{#snippet fileList(group: ManifestGroup, aligned: boolean)}
	{#if aligned}
		<DataRows
			items={filesOf(group)}
			key={(file: ManifestFile) => file.key}
			label="The files of {groupName(group)}"
		>
			{#snippet row(file: ManifestFile)}
				{@const held = heldFileWords(file.held)}
				{#snippet title()}
					<span class="file">
						{@render fileTick(group, file)}
						<span class="title">{file.title}</span>
						{#if held}<span class="facts">{held}</span>{/if}
					</span>
				{/snippet}
				{#snippet facts()}
					<span class="facts">{fileFacts(file)}</span>
				{/snippet}
				<DataRow compact indent={1} cells={{ what: title, size: facts }} />
			{/snippet}
		</DataRows>
	{:else}
		<ul class="files" aria-label="The files of {groupName(group)}">
			{#each filesOf(group) as file (file.key)}
				{@const held = heldFileWords(file.held)}
				<li class="file wraps">
					{@render fileTick(group, file)}
					<span class="title">{file.title}</span>
					<span class="facts">{held ?? fileFacts(file)}</span>
				</li>
			{/each}
		</ul>
	{/if}
	{#if moreOf(group) > 0}
		<div class="more">
			{#if sessionId}
				<Button size="small" tone="quiet" busy={reading} onclick={() => void readMore(group)}
					>Show {counted(Math.min(moreOf(group), GROUP_PAGE))} more of {counted(
						moreOf(group)
					)}</Button
				>
			{:else}
				<p class="note">And {counted(moreOf(group))} more.</p>
			{/if}
		</div>
	{/if}
{/snippet}

{#if single}
	<div class="single">
		{@render picture(single)}
		<p class="sentence">
			<span class="name">{groupName(single)}</span>{#if groupContext(single)}<span class="context"
					>, {groupContext(single)}</span
				>{/if}: {filesAndBytes(single.files, single.bytes)}
		</p>
		{@render pill(single)}
	</div>
	{#if now && now.group === single.key}
		<p class="now">Now: {now.title}, {size(now.size)}</p>
	{/if}
{:else}
	<DataRows
		items={rows}
		key={(group: ManifestGroup) => group.key}
		{label}
		columns={phoneWidth.yes ? PHONE_COLUMNS : COLUMNS}
		actions={openable ? '10rem' : undefined}
		folds
	>
		{#snippet row(group: ManifestGroup)}
			{@const href = stateOf(group, null) === 'landed' ? landedHref(group) : null}
			{#snippet what()}
				<span class="what" class:out={group.left_out !== null}>
					{#if canTick(group)}
						<Checkbox
							state={ticked(group) ? 'on' : 'off'}
							label="Take {groupName(group)}"
							onchange={(next) => tickGroup(group, next === 'on')}
						/>
					{/if}
					{@render picture(group)}
					<span class="words">
						<span class="name">{groupName(group)}</span>
						{#if group.left_out}
							<span class="context reason">{leftOutWords(group)}</span>
						{:else}
							{#if groupContext(group)}<span class="context">{groupContext(group)}</span>{/if}
							{#if choose && haveWords(group)}<span class="context">{haveWords(group)}</span>{/if}
							{#if now && now.group === group.key}
								<span class="context now">Now: {now.title}, {size(now.size)}</span>
							{/if}
							{#if group.failed > 0}
								<span class="context reason">{counted(group.failed)} refused on import</span>
							{/if}
						{/if}
						{#if phoneWidth.yes && !group.left_out}
							<span class="context"
								>{joined(
									filesAndBytes(group.files, group.bytes),
									choose ? afterNumber(group, ticked(group) === true) : null
								)}</span
							>
							{#if href}
								<span class="open">
									<Button size="small" tone="quiet" onclick={() => void goto(href)}
										>Open what landed</Button
									>
								</span>
							{/if}
						{/if}
					</span>
				</span>
			{/snippet}
			{#snippet sized()}
				{#if !group.left_out}<span class="figure">{filesAndBytes(group.files, group.bytes)}</span
					>{/if}
			{/snippet}
			{#snippet after()}
				{@const said = afterNumber(group, ticked(group) === true)}
				{#if said && !group.left_out}<span class="figure">{said}</span>{/if}
			{/snippet}
			{#snippet state()}
				{@render pill(group)}
			{/snippet}
			{#snippet landed()}
				{#if href}
					<Button size="small" tone="quiet" onclick={() => void goto(href)}>Open what landed</Button
					>
				{/if}
			{/snippet}
			<DataRow
				cells={{ what, size: sized, after, state }}
				expanded={open === group.key && !phoneWidth.yes}
				ontoggle={group.left_out || group.files === 0 ? undefined : () => toggle(group)}
				toggleLabel="the files of {groupName(group)}"
				actions={href && openable ? landed : undefined}
			>
				{#snippet expansion()}
					{#if open === group.key && !phoneWidth.yes}
						{@render fileList(group, true)}
					{/if}
				{/snippet}
			</DataRow>
		{/snippet}
	</DataRows>
{/if}

{#if sheetGroup}
	<Modal
		open
		title={groupName(sheetGroup)}
		description={filesAndBytes(sheetGroup.files, sheetGroup.bytes)}
		onOpenChange={(next) => {
			if (!next) open = null;
		}}
	>
		{#snippet children()}
			{@render fileList(sheetGroup, false)}
		{/snippet}
	</Modal>
{/if}

<style>
	.single {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	.sentence {
		flex: 1;
		min-inline-size: 0;
		margin: 0;
	}

	.what {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		min-inline-size: 0;
	}

	.words {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.context {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.now {
		margin: 0;
		color: var(--sift-ink-2);
		overflow-wrap: anywhere;
	}

	/* Kept out: the danger colour on the name and the reason, as a refused pick wears it. */
	.out .name,
	.reason {
		color: var(--sift-bad-text);
	}

	/* The picture's box: a disc a small control's height, the size the Avatar fills. */
	.face {
		display: block;
		inline-size: var(--control-height-sm);
		flex: none;
		border-radius: var(--radius-full);
		overflow: hidden;
	}

	.open {
		display: flex;
	}

	.glyph {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		inline-size: var(--control-height-sm);
		block-size: var(--control-height-sm);
		flex: none;
		color: var(--sift-ink-2);
	}

	.figure {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	.files {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.file {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* In the sheet: the title and its facts on one line where there is room, the facts under it
	   where there is not. */
	.file.wraps {
		flex-wrap: wrap;
		row-gap: 0;
	}

	.wraps .facts {
		margin-inline-start: auto;
	}

	.wraps .title {
		flex-basis: var(--card-column-min);
	}

	.title {
		flex: 1;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.facts,
	.note {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		font-variant-numeric: tabular-nums;
	}

	.more {
		display: flex;
		justify-content: flex-end;
	}
</style>
