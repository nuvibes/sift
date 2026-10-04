<script lang="ts" module>
	import { personRow } from '$lib/people/person-row';
	import { refusedChoice } from '$lib/swap/refused';
	import { thinChoice } from '$lib/people/thin-fingerprints';
	import type { KnownPerson } from '$lib/people/faces.svelte';

	/** A known person with the two marks the roster carries beside her picture. */

	/** The facial fingerprints sheet's rows: people with a page and faces of their own, each drawn
	 *  by their picture, refused by the same two marks as on the people sheet, and marked with
	 *  their page's band where they have under twenty confirmed faces. */
	export function fingerprintChoices(known: readonly KnownPerson[]) {
		return known
			.filter((one) => one.id && one.faces > 0)
			.map((one) => refusedChoice(thinChoice(personRow(one), one), one));
	}
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: its Start restarts a real tunnel with a listener and hands out a real token. The gallery draws the steps a session hands over instead (`a-swap-step-by-step`). */
	/*
	 * Start a swap: what to offer, whether stash-box ids go with it, and which tunnel it goes through.
	 *
	 * ## What can be offered, and through which pickers
	 *
	 * People, Sites, tags, collections and Photo Sets, chosen through the same sheet the "Add to"
	 * verbs use, and at most one saved filter from the filters kept on the Files wall. And facial
	 * fingerprints: the people this Sift can recognize, offered with no files, chosen from the same
	 * list Settings > Faces shows. It is the one kind that is not a way of choosing files. What leaves is
	 * decided by the host's own scoped read of those things when the codes match (nothing in the
	 * vault, nothing kept local) so this screen names things and never lists files.
	 *
	 * A person, a Site or a tag that will not go (kept local, or kept out of swaps) is in the sheet,
	 * so nobody wonders where they went, wearing the refused state with the reason on its row, and
	 * a press on it ticks nothing (`refusedChoice`): the rule swap mode's walls hold for the same
	 * thing on a card. The facial fingerprints sheet reads Settings > Faces' list, which carries the
	 * same two marks as the person's People row, so somebody whose fingerprints will not go is
	 * drawn refused there too.
	 *
	 * ## The tunnel and Start
	 *
	 * `SwapLaunch`, the same as swap mode's drawer: how a swap goes out is one question whichever
	 * way its contents were chosen.
	 *
	 * ## Exchange, and the guest's own offer
	 *
	 * With `both`, the same screen starts an exchange, a swap that sends and receives: `SwapLaunch`
	 * asks where what comes back lands. With `pickOnly`, the pickers alone: the guest of an exchange
	 * says what it sends them with the same pickers, on its code step, and its They match sends it
	 * (`chosen` and `shareBoxes` are bound out; there is no Start). `initial` is what it picked in
	 * swap mode before it pasted the token, which the pickers start from.
	 */
	import { onMount, untrack } from 'svelte';
	import {
		Button,
		Checkbox,
		Chip,
		ChipRow,
		Panel,
		PickDialog,
		Problem,
		SectionHeading,
		Select,
		type Choice,
		type PickChoice
	} from '$lib/components/common';
	import { people, sites } from '$lib/people/people.svelte';
	import { tags } from '$lib/entity/tags.svelte';
	import { collections } from '$lib/library/collections.svelte';
	import { photoSets } from '$lib/library/photo-sets.svelte';
	import { pickRow } from '$lib/entity/entity-picture';
	import { siteRow } from '$lib/entity/site-row';
	import { ASSET_WALL, savedSearches } from '$lib/search/saved-searches.svelte';
	import { knownPeople } from '$lib/people/faces.svelte';
	import type { IconName } from '$lib/design/icons';
	import SendingWeight from './SendingWeight.svelte';
	import SwapLaunch from './SwapLaunch.svelte';
	import type { Chosen, SwapStarted } from './swap';

	interface Props {
		onstarted?: (started: SwapStarted) => void;
		/** Exchange: they offer too, and what comes back lands in a folder chosen here. */
		both?: boolean;
		/** The pickers alone, for the guest's own offer: what is chosen is bound out. */
		pickOnly?: boolean;
		chosen?: Chosen[];
		shareBoxes?: boolean;
		/** What the pickers start with: picks made in swap mode before the token was pasted. */
		initial?: readonly { kind: Exclude<Chosen['kind'], 'filter'>; id: string; name: string }[];
	}

	let {
		onstarted = () => undefined,
		both = false,
		pickOnly = false,
		chosen = $bindable([]),
		shareBoxes = $bindable(true),
		initial = []
	}: Props = $props();

	type EntityKind = Exclude<Chosen['kind'], 'filter'>;

	interface Picker {
		kind: EntityKind;
		/** The button that opens the sheet. */
		label: string;
		title: string;
		/** What the sheet says under its title: what offering these sends. */
		subject: string;
		placeholder: string;
		icon: IconName;
		ask: (typed: string) => Promise<PickChoice[]>;
	}

	const FILES_SUBJECT =
		'Their files are offered once the codes match. Nothing in Hidden is ever offered.';

	/** The category's own line, under the pickers: what it offers, and what it does not. */
	const FINGERPRINTS_LINE =
		'Facial fingerprints let the other Sift recognize the same people. No files go with them.';

	const PICKERS: readonly Picker[] = [
		{
			kind: 'person',
			label: 'Choose people',
			title: 'Choose people to offer',
			subject: FILES_SUBJECT,
			placeholder: 'Who',
			icon: 'person',
			ask: async (typed) =>
				(await people.choices(typed)).items.map((one) => refusedChoice(personRow(one), one))
		},
		{
			kind: 'site',
			label: 'Choose Sites',
			title: 'Choose Sites to offer',
			subject: FILES_SUBJECT,
			placeholder: 'Which Site',
			icon: 'public',
			ask: async (typed) =>
				(await sites.choices(typed)).items.map((one) => refusedChoice(siteRow(one), one))
		},
		{
			kind: 'tag',
			label: 'Choose tags',
			title: 'Choose tags to offer',
			subject: FILES_SUBJECT,
			placeholder: 'Tag name',
			icon: 'shoppingmode',
			ask: async (typed) =>
				(await tags.choices(typed)).items.map((one) => refusedChoice(pickRow('tag', one), one))
		},
		{
			kind: 'collection',
			label: 'Choose collections',
			title: 'Choose collections to offer',
			subject: FILES_SUBJECT,
			placeholder: 'Collection name',
			icon: 'box',
			ask: async (typed) =>
				(await collections.choices(typed)).items.map((one) => pickRow('collection', one))
		},
		{
			kind: 'photo_set',
			label: 'Choose Photo Sets',
			title: 'Choose Photo Sets to offer',
			subject: FILES_SUBJECT,
			placeholder: 'Photo Set name',
			icon: 'photo_library',
			ask: async (typed) =>
				(await photoSets.choices(typed)).items.map((one) => pickRow('photo_set', one))
		},
		{
			kind: 'facial_fingerprints',
			label: 'Choose facial fingerprints',
			title: 'Choose whose facial fingerprints to offer',
			subject:
				'What your Sift learned from their faces is offered once the codes match, and none of their files.',
			placeholder: 'Who',
			icon: 'face',
			// Only people with a page of their own: a name a file is holding for has nothing here yet.
			// Each drawn by the picture the rest of the app draws them by, as the people picker above
			// draws them (`personRow`), rather than as a bare name, and refused by the same marks.
			ask: async (typed) => fingerprintChoices((await knownPeople(typed)).items)
		}
	];

	/** What is offered so far, each with the name it was picked by: from the picks made in swap
	 *  mode before, where there were any, once. */
	let picked = $state<{ kind: EntityKind; id: string; name: string }[]>(
		untrack(() => initial.map(({ kind, id, name }) => ({ kind, id, name })))
	);
	let filterId = $state('');
	let problem = $state<string | null>(null);

	/** The sheet that is open, and what it offers. */
	let picking = $state<Picker | null>(null);
	let sheetOpen = $state(false);
	let choices = $state<PickChoice[]>([]);

	async function open(picker: Picker): Promise<void> {
		picking = picker;
		choices = [];
		sheetOpen = true;
		try {
			choices = await picker.ask('');
		} catch {
			problem = "That list couldn't be loaded. Try again.";
		}
	}

	function add(kind: EntityKind, chosen: Choice[]): void {
		const have = new Set(picked.map((one) => `${one.kind}:${one.id}`));
		picked = [
			...picked,
			...chosen
				.filter((one) => !have.has(`${kind}:${one.id}`))
				.map((one) => ({ kind, id: one.id, name: one.name }))
		];
	}

	function drop(kind: EntityKind, id: string): void {
		picked = picked.filter((one) => !(one.kind === kind && one.id === id));
	}

	const filters = $derived(savedSearches.on(ASSET_WALL));
	const filterOptions = $derived([
		{ value: '', label: 'No saved filter' },
		...filters.map((one) => ({ value: one.id, label: one.name }))
	]);

	const picks = $derived<Chosen[]>([
		...picked.map((one) => ({ kind: one.kind, id: one.id })),
		...(filterId ? [{ kind: 'filter' as const, id: filterId }] : [])
	]);
	/* Bound out for the guest's offer, which its They match sends. */
	$effect(() => {
		chosen = picks;
	});

	onMount(() => {
		void savedSearches.ensure().catch(() => undefined);
	});
</script>

{#snippet pickers()}
	<div class="pickers">
		{#each PICKERS as picker (picker.kind)}
			<Button size="small" icon={picker.icon} onclick={() => void open(picker)}
				>{picker.label}</Button
			>
		{/each}
	</div>
	<p class="line">{FINGERPRINTS_LINE}</p>
	{#if picked.length > 0}
		<ChipRow>
			{#each picked as one (`${one.kind}:${one.id}`)}
				<Chip
					icon={PICKERS.find((picker) => picker.kind === one.kind)?.icon ??
						(one.kind === 'asset' ? 'article' : undefined)}
					onremove={() => drop(one.kind, one.id)}
					removeLabel="Remove {one.name}">{one.name}</Chip
				>
			{/each}
		</ChipRow>
	{/if}

	<label class="named" for="swap-filter">And one saved filter</label>
	<Select id="swap-filter" bind:value={filterId} options={filterOptions} />

	<Problem message={problem} />
{/snippet}

{#if pickOnly}
	<Panel label="What you send them">
		<p class="line">This is an exchange. Choose what to send them, then compare the code.</p>
		{@render pickers()}
		<span class="ticked">
			<Checkbox
				state={shareBoxes ? 'on' : 'off'}
				label="Share stash-box ids"
				onchange={(next) => (shareBoxes = next === 'on')}
			/>
			<span>Share stash-box ids</span>
		</span>
		<SendingWeight chosen={picks} />
	</Panel>
{:else}
	<Panel label={both ? 'Start an exchange' : 'Start a swap'}>
		<SectionHeading>{both ? 'What to send them' : 'What to offer'}</SectionHeading>
		{@render pickers()}
		<SectionHeading>{both ? 'Start the exchange' : 'Start the swap'}</SectionHeading>
		<SwapLaunch chosen={picks} {onstarted} {both} />
	</Panel>
{/if}

{#if picking}
	<PickDialog
		bind:open={sheetOpen}
		title={picking.title}
		subject={picking.subject}
		{choices}
		onsearch={picking.ask}
		placeholder={picking.placeholder}
		confirmLabel={(on: number) => (on === 1 ? 'Add 1' : `Add ${on}`)}
		onpick={(chosen: Choice[]) => picking && add(picking.kind, chosen)}
	/>
{/if}

<style>
	.pickers {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	.line {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.named {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.ticked {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
</style>
