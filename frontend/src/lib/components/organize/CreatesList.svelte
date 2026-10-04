<script lang="ts">
	/*
	 * The rows an answer would have to invent, grouped by what they are, each with its own answer.
	 *
	 * ## Why this is not one tick
	 *
	 * A real match can carry thirty-odd new names: people, tags, a Site, and words that are somebody
	 * else's filing rather than anything this library wants. One tick offers two answers to that:
	 * make all of them, or make none and lose the people who are the reason for doing this at all.
	 * A control whose whole job is to be read must reward reading it.
	 *
	 * ## Why it is grouped, and why each group has its glyph
	 *
	 * Because the kind is most of the decision, and one alphabetical list hides it. "Anouk
	 * Vestergaard, Beach, Blond Hair, Bryn Calloway, Outdoor" is a hard question. The same words under
	 * People, Tags and Sites are three easy ones, and each group can be taken or left in a single press.
	 *
	 * The glyphs are the ones the search box already uses for these three: a person, a tag, a
	 * Site. A fourth drawing of the same three things is a fourth chance for them to disagree.
	 *
	 * ## Why the default is nothing
	 *
	 * Creating rows from somebody else's vocabulary is the thing this feature deliberately does not
	 * do on its own. A list that arrived pre-ticked would be automatic creation with a step in front
	 * of it: the step being one somebody can miss.
	 *
	 * ## Why it is a component
	 *
	 * Two surfaces offer it: the pile under Organize, settling a page at a time, and the chooser on
	 * one file's own menu. Written twice they would drift, and the drift would be silent: a list
	 * that creates on one screen and not the other is the same press meaning two things.
	 *
	 * WHY NOT BITS-UI: the box is `Checkbox`, which is theirs, and the press is `Pressable`. There
	 * is no primitive for a list of toggles; this is those two, in a list.
	 */ import { Button, Checkbox, Pressable } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import { rowKey, type Missing } from '$lib/entity/tagger.svelte';
	import { counted } from '$lib/entity/entity-counts';

	interface Props {
		/** Every row this press would have to invent, already deduplicated and sorted. */
		names: Missing[];
		/** The ones ticked. Bound, because the press that sends them lives outside this. */
		chosen?: Missing[];
	}

	let { names, chosen = $bindable([]) }: Props = $props();

	/* The three kinds a stash-box can name, in the order they are worth deciding about: the people
	   are why anybody runs this, the tags are the long tail, the Site is one row. Anything a later
	   box invents a fourth kind of falls through to `Other` rather than vanishing off the list. */
	const KINDS: { kind: string; label: string; icon: IconName }[] = [
		{ kind: 'person', label: 'People', icon: 'person' },
		{ kind: 'tag', label: 'Tags', icon: 'shoppingmode' },
		{ kind: 'site', label: 'Sites', icon: 'public' }
	];

	const picked = $derived(new Set(chosen.map(rowKey)));

	const groups = $derived(
		[
			...KINDS.map((one) => ({
				...one,
				rows: names.filter((row) => row.kind === one.kind)
			})),
			{
				kind: 'other',
				label: 'Other',
				icon: 'box' as IconName,
				rows: names.filter((row) => !KINDS.some((one) => one.kind === row.kind))
			}
		].filter((group) => group.rows.length > 0)
	);

	function toggle(row: Missing): void {
		chosen = picked.has(rowKey(row))
			? chosen.filter((one) => rowKey(one) !== rowKey(row))
			: [...chosen, row];
	}

	/** Take or leave a whole group, which is the press somebody making this decision usually wants. */
	function setGroup(rows: Missing[], on: boolean): void {
		const theirs = new Set(rows.map(rowKey));
		const rest = chosen.filter((one) => !theirs.has(rowKey(one)));
		chosen = on ? [...rest, ...rows] : rest;
	}

	function allOf(rows: Missing[]): boolean {
		return rows.every((row) => picked.has(rowKey(row)));
	}
</script>

<section class="creates">
	<p class="lede">
		These answers name <strong>{counted(names.length)}</strong>
		{names.length === 1 ? 'entry' : 'entries'} this library doesn't have. Select the ones to create. Anything
		not selected isn't created.
	</p>

	<div class="both">
		<Button
			type="button"
			size="small"
			tone="ghost"
			disabled={chosen.length === names.length}
			onclick={() => (chosen = [...names])}>Select all</Button
		>
		<Button
			type="button"
			size="small"
			tone="ghost"
			disabled={chosen.length === 0}
			onclick={() => (chosen = [])}>Clear</Button
		>
		<span class="tally">{chosen.length} of {names.length} would be created</span>
	</div>

	{#each groups as group (group.kind)}
		<div class="group">
			<p class="heading">
				<Icon name={group.icon} size={16} />
				<span>{group.label}</span>
				<span class="count">{counted(group.rows.length)}</span>
				<Button
					type="button"
					size="small"
					tone="ghost"
					onclick={() => setGroup(group.rows, !allOf(group.rows))}
				>
					{allOf(group.rows) ? 'Clear' : 'Select all'}
				</Button>
			</p>
			<ul class="names">
				{#each group.rows as row (rowKey(row))}
					<li>
						<Pressable
							class="one"
							feedback="wash"
							radius="sm"
							aria-pressed={picked.has(rowKey(row))}
							aria-label={picked.has(rowKey(row))
								? `Don't create ${row.name}`
								: `Create ${row.name}`}
							onclick={() => toggle(row)}
						>
							<Checkbox state={picked.has(rowKey(row)) ? 'on' : 'off'} mark />
							<span class="who" class:on={picked.has(rowKey(row))}>{row.name}</span>
						</Pressable>
					</li>
				{/each}
			</ul>
		</div>
	{/each}
</section>

<style>
	.creates {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.lede {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.both {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.tally {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.group {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.heading {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-label);
	}

	.count {
		margin-inline-end: auto;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Columns, and as many as fit. Twenty tags down a single column is a scroll on its own; the
	   same twenty in three columns is one glance. `auto-fill` so a narrow sheet still gets one
	   rather than a row of squeezed ones. */
	.names {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
		gap: 0 var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* `:global`, and anchored by a class this file writes. The class lands on an element compiled
	   in `Pressable`, so a scoped rule for it here would match nothing at all, silently. */
	.names :global(.one) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		inline-size: 100%;
		padding: var(--space-1) var(--space-2);
		text-align: start;
	}

	.who {
		min-inline-size: 0;
		overflow-wrap: anywhere;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.who.on {
		color: var(--sift-ink);
	}
</style>
