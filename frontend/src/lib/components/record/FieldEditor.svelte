<script lang="ts">
	/*
	 * How ONE field of a record is edited, the only definition, for the form's column and the
	 * file's grid alike. It never draws its own label; the caller passes the id it points at.
	 */
	import {
		Button,
		DateField,
		MenuButton,
		NumberInput,
		PickMenu,
		Select,
		SuggestInput,
		Switch,
		TagChip,
		TextArea,
		TextInput,
		Tooltip
	} from '$lib/components/common';
	import { appearance } from '$lib/theme/appearance.svelte';
	import { COUNTRIES, COUNTRY_CODES } from '$lib/people/countries';
	import { heightFromParts, heightParts } from '$lib/shell/measure';
	import { PICK_PAGE } from '$lib/search/frequent.svelte';
	import { linkMarks, pickRow } from '$lib/entity/entity-picture';
	import Icon from '$lib/components/Icon.svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import type { PickChoice, PickPage } from '$lib/components/common/verbs';
	import type { FieldDescription } from '$lib/entity/records.svelte';

	const unmarked = new SvelteSet<string>();
	import { tags as tagStore } from '$lib/entity/tags.svelte';

	interface Props {
		field: FieldDescription;
		/** The caller holds the draft. */
		value: unknown;
		onchange: (next: unknown) => void;
		id?: string;
		describedBy?: string;
		/** Never offered as the value of a field naming something: a Site is not part of itself. */
		self?: string;
	}

	let { field, value, onchange, id, describedBy, self }: Props = $props();

	/** A half-typed entry, not part of the record. */
	let typing = $state('');

	/** The registry's `LIST_KINDS`; the adder's empty box says the field's own `entry`. */
	const isList = $derived(field.kind === 'names' || field.kind === 'links');

	/** The registry's `ordered`: a song's artists in credit order. */
	const ordered = $derived(field.ordered === true);

	function asText(one: unknown): string {
		if (typeof one === 'string') return one;
		const held = one as { url?: string; alias?: string; name?: string };
		return held.url ?? held.alias ?? held.name ?? String(one);
	}

	const list = $derived(Array.isArray(value) ? (value as unknown[]).map(asText) : []);
	const chips = $derived(Array.isArray(value) ? (value as { id: string; name: string }[]) : []);

	/* The server's alphabetical page, less what is in the draft, counted into `more`. */
	async function askTags(typed: string): Promise<PickPage> {
		const asked = await tagStore.choices(typed, PICK_PAGE);
		const on = new Set(chips.map((one) => one.id));
		const rows = asked.items.filter((tag) => !on.has(tag.id));
		const dropped = asked.items.length - rows.length;
		return {
			choices: rows.map((tag) => pickRow('tag', tag)),
			more: Math.max(0, asked.total - asked.items.length + dropped)
		};
	}

	/* The one write before Save: a tag must exist before a draft can name it. */
	async function makeTag(named: string): Promise<PickChoice> {
		const made = await tagStore.create(named);
		return { id: made.id, name: made.name };
	}

	function add() {
		const wanted = typing.trim();
		if (!wanted) return;
		typing = '';
		if (list.some((one) => one.toLowerCase() === wanted.toLowerCase())) return;
		onchange([...list, wanted]);
	}

	/* Not trimmed as typed: "Jane" on the way to "Janet" is not a duplicate. */
	function edit(at: number, text: string) {
		onchange(list.map((held, index) => (index === at ? text : held)));
	}

	function move(at: number, by: -1 | 1) {
		const to = at + by;
		if (to < 0 || to >= list.length) return;
		const next = [...list];
		[next[at], next[to]] = [next[to], next[at]];
		onchange(next);
	}

	/*
	 * A nationality is CHOSEN, never typed; the empty first option clears it (`Select` swaps `''`
	 * internally).
	 */
	const countries = [
		{ value: '', label: 'Not set' },
		...COUNTRY_CODES.map((code) => ({ value: code, label: COUNTRIES[code] }))
	];

	/*
	 * Imperial is feet and inches, saved as whole centimetres (`$lib/shell/measure`); nothing above
	 * zero is no height.
	 */
	const centimetres = $derived.by(() => {
		const written = typeof value === 'number' ? String(value) : String(value ?? '').trim();
		const read = Number(written);
		return written !== '' && Number.isFinite(read) && read > 0 ? read : null;
	});

	const parts = $derived(centimetres === null ? { feet: 0, inches: 0 } : heightParts(centimetres));

	/* Both boxes write the whole height; an empty pair clears it. */
	function heightTyped(feet: number, inches: number): void {
		const held = heightFromParts(feet, inches);
		onchange(held > 0 ? held : '');
	}

	const mode = $derived(
		field.kind === 'link'
			? ('url' as const)
			: field.kind === 'year' || field.kind === 'length'
				? ('numeric' as const)
				: undefined
	);
</script>

{#if isList}
	<!-- The list lives inside the control, or the field's text-box look does not reach it. -->
	<div class="adding">
		{#if field.suggests}
			<!-- Completes from what the library knows, by the registry's vocabulary. -->
			<SuggestInput
				{id}
				{describedBy}
				suggests={field.suggests}
				value={typing}
				placeholder={field.entry ?? undefined}
				oninput={(typed) => (typing = typed)}
				onsubmit={(typed) => {
					typing = typed;
					add();
				}}
			/>
		{:else}
			<TextInput
				{id}
				{describedBy}
				value={typing}
				oninput={(event) => (typing = event.currentTarget.value)}
				onkeydown={(event) => {
					if (event.key !== 'Enter') return;
					// Enter adds to the list; it must not submit the record, which would save while
					// somebody was still filling one field in.
					event.preventDefault();
					add();
				}}
				placeholder={field.entry ?? undefined}
				autocomplete="off"
			/>
		{/if}
		<Button type="button" icon="add" onclick={add}>Add</Button>
	</div>

	{#if list.length > 0}
		<!-- Every entry is a BOX, keyed by position, so editing a character keeps the caret. -->
		<ul class="entries">
			{#each list as entry, at (at)}
				{@const mark =
					field.kind === 'links' ? linkMarks(entry).find((one) => !unmarked.has(one)) : undefined}
				<li>
					{#if field.kind === 'links'}
						{#if mark}
							<img
								class="mark"
								src={mark}
								alt=""
								width="16"
								height="16"
								onerror={() => unmarked.add(mark)}
							/>
						{:else}
							<Icon name="link" size={16} />
						{/if}
					{/if}
					<TextInput
						value={entry}
						aria-label="{field.label}, {at + 1}"
						autocomplete="off"
						oninput={(event) => edit(at, event.currentTarget.value)}
					/>
					{#if ordered}
						<Tooltip label="Move up">
							<Button
								icon="arrow_upward"
								size="small"
								tone="ghost"
								type="button"
								aria-label={`Move ${entry} up`}
								disabled={at === 0}
								onclick={() => move(at, -1)}
							/>
						</Tooltip>
						<Tooltip label="Move down">
							<Button
								icon="arrow_downward"
								size="small"
								tone="ghost"
								type="button"
								aria-label={`Move ${entry} down`}
								disabled={at === list.length - 1}
								onclick={() => move(at, 1)}
							/>
						</Tooltip>
					{/if}
					<Tooltip label="Remove {entry}">
						<Button
							icon="close"
							size="small"
							tone="ghost"
							type="button"
							aria-label="Remove {entry}"
							onclick={() => onchange(list.filter((_, index) => index !== at))}
						/>
					</Tooltip>
				</li>
			{/each}
		</ul>
	{/if}
{:else if field.kind === 'tags'}
	<!-- The shared tag picker, `inline`, writing into the draft only. -->
	<div class="tags">
		{#each chips as chip (chip.id)}
			<TagChip
				name={chip.name}
				onremove={() => onchange(chips.filter((one) => one.id !== chip.id))}
			/>
		{/each}
		<MenuButton label="Add a tag to {field.label}" words="Tag">
			<PickMenu
				label="Tag"
				icon="shoppingmode"
				kind="tag"
				plural="tags"
				inline
				ask={askTags}
				onpick={(choice) => {
					if (chips.some((one) => one.id === choice.id)) return;
					onchange([...chips, { id: choice.id, name: choice.name }]);
				}}
				oncreate={makeTag}
			/>
		</MenuButton>
	</div>
{:else if field.kind === 'date'}
	<DateField
		{id}
		{describedBy}
		label={field.label}
		value={String(value ?? '')}
		onchange={(day: string) => onchange(day)}
	/>
{:else if field.kind === 'country'}
	<Select
		{id}
		{describedBy}
		options={countries}
		value={String(value ?? '')}
		onValueChange={(chosen: string) => onchange(chosen)}
		placeholder="Not set"
	/>
{:else if field.kind === 'flag'}
	<!-- One switch for yes or no, written back as a real boolean. -->
	<Switch
		{id}
		{describedBy}
		checked={value === true || value === 1 || value === '1'}
		onCheckedChange={(on: boolean) => onchange(on)}
	/>
{:else if field.kind === 'paragraph'}
	<TextArea
		{id}
		{describedBy}
		rows={4}
		value={String(value ?? '')}
		oninput={(event) => onchange(event.currentTarget.value)}
	/>
{:else if field.kind === 'length' && appearance.units === 'imperial'}
	<!-- Two boxes with their units inside; each names itself. -->
	<div class="parts">
		<NumberInput
			{id}
			{describedBy}
			label="{field.label}, feet"
			unit="ft"
			min={0}
			width={2}
			value={parts.feet}
			onchange={(feet: number) => heightTyped(feet, parts.inches)}
		/>
		<NumberInput
			{describedBy}
			label="{field.label}, inches"
			unit="in"
			min={0}
			max={11}
			width={2}
			value={parts.inches}
			onchange={(inches: number) => heightTyped(parts.feet, inches)}
		/>
	</div>
{:else if field.suggests}
	<!-- Completes from Sites that exist, never refusing a new one. -->
	<SuggestInput
		{id}
		{describedBy}
		suggests={field.suggests}
		value={String(value ?? '')}
		oninput={(typed) => onchange(typed)}
		leaveOut={self ? [self] : []}
	/>
{:else}
	<!-- `type="text"`: `type="url"` refuses an address without a scheme, and a clear. -->
	<TextInput
		{id}
		{describedBy}
		type="text"
		inputmode={mode}
		placeholder={field.kind === 'link' ? 'https://\u2026' : undefined}
		value={String(value ?? '')}
		oninput={(event) => onchange(event.currentTarget.value)}
		autocomplete="off"
	/>
{/if}

<style>
	.adding {
		display: flex;
		gap: var(--space-2);
		align-items: center;
	}

	.adding :global(.text-input) {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	/* One entry per row, full width. */
	.entries {
		list-style: none;
		margin: var(--space-2) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.entries li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.entries :global(.text-input) {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	.parts {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.tags {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.mark {
		object-fit: contain;
		flex: none;
	}
</style>
