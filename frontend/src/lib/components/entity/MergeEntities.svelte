<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/* Two people, two Sites or two songs that turn out to be one: the candidates as cards, what would
	 * happen in short lines under them and one button, in one sheet. It cannot be undone, so the
	 * numbers come first; the name that goes stays as another name of the survivor. */
	import { untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		Avatar,
		Button,
		ChoiceCard,
		ChoiceGroup,
		Modal,
		NarrowBox,
		Problem
	} from '$lib/components/common';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		mergeSeveral,
		problemFrom,
		weighSeveral,
		type MergeableKind,
		type MergeSubject,
		type Weighed
	} from '$lib/entity/merge.svelte';
	import { people as peopleStore, sites as siteStore } from '$lib/people/people.svelte';
	import { personRow } from '$lib/people/person-row';
	import { siteRow } from '$lib/entity/site-row';
	import { glyphOf, pickRow } from '$lib/entity/entity-picture';
	import { songs as songStore, type Song } from '$lib/entity/songs.svelte';

	/** Every word that differs between the kinds, fixed here rather than by the caller. */
	const WORDS: Record<MergeableKind, { one: string; many: string; find: string }> = {
		person: { one: 'person', many: 'people', find: 'Search people' },
		site: { one: 'Site', many: 'Sites', find: 'Search Sites' },
		song: { one: 'song', many: 'songs', find: 'Search songs' }
	};

	/** The keeper when it is somebody else, in each kind's own word. */
	const ANOTHER: Record<MergeableKind, { into: string; pick: string }> = {
		person: { into: 'somebody', pick: 'Pick somebody else' },
		site: { into: 'another Site', pick: 'Pick another Site' },
		song: { into: 'another song', pick: 'Pick another song' }
	};

	/** A song as a row of this sheet: its name, its picture where one was chosen, its files. */
	function songRow(one: Song): MergeSubject {
		return { ...pickRow('song', one), files: one.item_count };
	}

	interface Props {
		/** Who was picked, by id, all read from the server so none silently falls out. */
		ids?: string[];
		/** The people already held whole (one, from their page): one asks who they really are,
		 * several which to keep. */
		people?: MergeSubject[];
		/** Which kind this is about: the address, the field name and every word. */
		kind?: MergeableKind;
		/** Draw the button. False where something else opens this: a wall's own menu. */
		withButton?: boolean;
		/** Open it from outside, for the surfaces that have no button of their own. */
		open?: boolean;
		/** Called once the merge has landed, so the page can leave or the wall can reload. */
		onmerged: (into: string) => void;
	}

	let {
		ids,
		people = [],
		kind = 'person',
		withButton = true,
		open: opened = $bindable(false),
		onmerged
	}: Props = $props();

	const words = $derived(WORDS[kind]);

	/** Every id picked, in the order it was picked, which is the order the others fold in. */
	const picked = $derived(ids ?? people.map((one) => one.id));
	/** The picks as the server has them, with whole counts; empty until read. */
	let subjects = $state<MergeSubject[]>([]);
	let reading = $state(false);
	/** Whether somebody has pressed a keeper, so the read landing late does not move it back. */
	let keeperPressed = $state(false);

	/** Whether this is one thing asking who it really is, or several asking which to keep. */
	const several = $derived(picked.length > 1);
	/** The one, when there is one. Used for the wording and to keep it out of its own chooser. */
	const only = $derived(
		subjects[0] ?? people.find((one) => one.id === picked[0]) ?? { id: picked[0] ?? '', name: '' }
	);

	let busy = $state(false);
	let failed = $state<string | null>(null);
	let typed = $state('');
	/** What the server answered for what was typed. Empty until the first answer lands. */
	let found = $state<MergeSubject[]>([]);
	/** Whether the server has answered once, so an empty list can be told from a list not yet read. */
	let looked = $state(false);
	/** Whose id is being kept. Set by a press, defaulted for a selection. */
	let keeping = $state('');
	/** The kept row on the one-subject path, held so a new search cannot drop it. */
	let chosen = $state<MergeSubject | null>(null);
	/** Whether the chooser is open again over a choice already made. See the fold, below. */
	let changing = $state(false);
	let weighed = $state<Weighed | null>(null);

	/** The selection biggest first: keeping the most files moves least, a default the card says. */
	const biggestFirst = $derived(
		[...subjects].sort((one, other) => (other.files ?? 0) - (one.files ?? 0))
	);

	/** The row being kept, as a row rather than an id: the button and every line name it. */
	const into = $derived(
		several ? (biggestFirst.find((one) => one.id === keeping) ?? null) : chosen
	);

	/* Who goes, for the summary's counts. */
	const going = $derived(
		several ? subjects.filter((one) => one.id !== keeping) : subjects.slice(0, 1)
	);

	/** What the going ones are called in a line: one named, more counted. */
	const others = $derived(
		going.length === 1 ? (going[0]?.name ?? '') : `the other ${going.length}`
	);

	/** Candidates to merge into from the whole library, with their pictures. */
	async function candidates(prefix: string): Promise<MergeSubject[]> {
		const page =
			kind === 'site'
				? (await siteStore.choices(prefix)).items.map(siteRow)
				: kind === 'song'
					? (await songStore.choices(prefix)).items.map(songRow)
					: (await peopleStore.choices(prefix)).items.map(personRow);
		return page.filter((one) => one.id !== only.id);
	}

	/* A refusal goes on the sheet while it is open, else to a toast. */
	function refuse(error: unknown): void {
		const message = problemFrom(error);
		failed = message;
		if (!opened && !withButton) toasts.show(message, { tone: 'error' });
	}

	/* `opened` is bound straight to the dialog. */
	$effect(() => {
		if (!opened) return;
		/*
		 * The last opening forgotten here, untracked: `people` is rebuilt on every redraw behind
		 * it.
		 */
		untrack(() => {
			failed = null;
			typed = '';
			looked = false;
			found = [];
			chosen = null;
			changing = false;
			weighed = null;
			keeping = '';
			keeperPressed = false;
			unfolded = {};
			subjects = [];
			void readPicks([...picked]);
		});
	});

	/* Every pick read by id; the keeper settles on the most files unless one was pressed. A pick
	   that cannot be read refuses the sheet. */
	let readingFor = 0;
	async function readPicks(wanted: string[]): Promise<void> {
		const mine = ++readingFor;
		reading = true;
		try {
			const rows: MergeSubject[] =
				kind === 'site'
					? (await siteStore.several(wanted)).map((one) => ({
							...siteRow(one),
							files: one.asset_count
						}))
					: kind === 'song'
						? (await songStore.several(wanted)).map(songRow)
						: (await peopleStore.several(wanted)).map((one) => ({
								...personRow(one),
								files: one.asset_count
							}));
			if (mine !== readingFor) return;
			subjects = rows;
			if (!keeperPressed) keeping = rows.length > 1 ? (biggestFirst[0]?.id ?? '') : '';
		} catch {
			if (mine !== readingFor) return;
			subjects = [];
			const which =
				wanted.length > 1 ? `One of the ${words.many} picked` : `The ${words.one} picked`;
			failed = `${which} couldn't be read \u2014 it may have been removed or hidden. Nothing was merged.`;
		} finally {
			if (mine === readingFor) reading = false;
		}
	}

	/*
	 * The library's candidates, asked on opening and a beat after typing; a stale answer is
	 * dropped.
	 */
	$effect(() => {
		if (!opened || several) return;
		const asked = typed.trim();
		const timer = setTimeout(
			() => {
				void candidates(asked)
					.then((answer) => {
						if (typed.trim() !== asked) return;
						found = answer;
						looked = true;
					})
					.catch((error: unknown) => {
						looked = true;
						refuse(error);
					});
			},
			asked ? 150 : 0
		);
		return () => clearTimeout(timer);
	});

	/* What would move, counted as soon as there is a keeper and again on every change; it writes
	   nothing, and a generation counter drops a stale answer. */
	let asking = 0;
	/* The question already asked, so a repaint behind the sheet does not POST again. */
	let askedFor = '';
	$effect(() => {
		const survivor = into?.id ?? '';
		const from = going.map((one) => one.id);
		if (!opened || !survivor || from.length === 0) {
			askedFor = '';
			weighed = null;
			return;
		}
		const question = `${survivor}|${from.join(',')}`;
		if (question === askedFor) return;
		askedFor = question;
		const mine = ++asking;
		busy = true;
		void weighSeveral(kind, from, survivor)
			.then((answer) => {
				if (mine !== asking) return;
				weighed = answer;
				failed = null;
			})
			.catch((error: unknown) => {
				if (mine !== asking) return;
				weighed = null;
				refuse(error);
			})
			.finally(() => {
				if (mine === asking) busy = false;
			});
	});

	/** How many names a line shows before it folds the rest behind "and N more". */
	const FOLD_AT = 5;

	/** One line of what would happen; `beyond` counts what the server did not name. */
	interface Line {
		id: string;
		text: string;
		names?: string[];
		beyond?: number;
	}

	/** Which folded lines have been opened in place. Forgotten on every opening. */
	let unfolded = $state<Record<string, boolean>>({});

	/** The confirmed faces that move, with whose they were. */
	function facesLines(w: Weighed): Line[] {
		const lines: Line[] = [];
		const faces = w.faces_from ?? [];
		if (w.faces > 0) {
			const moves = `confirmed ${w.faces === 1 ? 'face moves' : 'faces move'} too`;
			lines.push({
				id: 'faces',
				text:
					faces.length === 1
						? `${counted(w.faces)} ${moves}, from ${faces[0]?.whose ?? ''}`
						: `${counted(w.faces)} ${moves}`,
				names:
					faces.length > 1
						? faces.map((one) => `${counted(one.count)} from ${one.whose}`)
						: undefined
			});
		}
		return lines;
	}

	/** Every blank the merge fills, by name, or the count of them where none is named. */
	function filledLines(w: Weighed, survivor: string, others: string): Line[] {
		const lines: Line[] = [];
		const filled = w.filled ?? [];
		for (const one of filled)
			lines.push({
				id: `filled-${one.key}`,
				text: one.value
					? `${one.label}, missing here, is filled in from ${one.whose}: ${one.value}`
					: `${one.label}, missing here, is taken from ${one.whose}`
			});
		// A count with nothing named beside it is still said, as the count.
		if (filled.length === 0 && w.facts > 0)
			lines.push({
				id: 'facts',
				text: `${counted(w.facts)} ${w.facts === 1 ? 'field' : 'fields'} ${survivor} is missing ${w.facts === 1 ? 'is' : 'are'} filled in from ${others}`
			});
		return lines;
	}

	/** What would happen, in short named lines, files first, noughts left out. */
	const moving = $derived.by((): Line[] => {
		const survivor = into?.name ?? '';
		if (weighed === null || !survivor) return [];
		const w = weighed;
		const lines: Line[] = [];
		/* One gets a singular sentence, more are counted with names after a colon. */
		const listed = (
			id: string,
			count: number,
			named: string[],
			one: (name: string) => string,
			oneBare: string,
			many: string
		) => {
			if (count <= 0) return;
			if (count === 1) {
				lines.push({ id, text: named[0] !== undefined ? one(named[0]) : oneBare });
				return;
			}
			lines.push({
				id,
				text: `${counted(count)} ${many}`,
				names: named.length > 0 ? named : undefined,
				beyond: named.length > 0 ? Math.max(0, count - named.length) : undefined
			});
		};
		if (w.files > 0)
			lines.push({
				id: 'files',
				text: `${counted(w.files)} ${w.files === 1 ? 'file moves' : 'files move'} to ${survivor}`
			});
		const onSite = (one: { name: string; where?: string | null }) =>
			one.where ? `${one.name} on ${one.where}` : one.name;
		listed(
			'usernames',
			w.usernames,
			(w.usernames_named ?? []).map(onSite),
			(name) => `Username ${name} moves too`,
			'1 username moves too',
			'usernames move too'
		);
		listed(
			'aliases',
			w.aliases,
			(w.aliases_named ?? []).map((one) => one.name),
			(name) => `Alias ${name} moves to ${survivor}`,
			`1 alias moves to ${survivor}`,
			`aliases move to ${survivor}`
		);
		lines.push(...facesLines(w));
		listed(
			'links',
			w.links,
			(w.links_named ?? []).map((one) => one.name),
			(name) => `Link ${name} moves too`,
			'1 link moves too',
			'links move too'
		);
		// A Site published under one that goes changes which Site it is under.
		listed(
			'children',
			w.children,
			(w.children_named ?? []).map((one) => one.name),
			(name) =>
				`${name}, published under ${others}, is published under ${survivor} instead \u2014 it isn't removed`,
			`1 Site published under ${others} is published under ${survivor} instead \u2014 it isn't removed`,
			`Sites published under ${others} are published under ${survivor} instead \u2014 none of them is removed`
		);
		lines.push(...filledLines(w, survivor, others));
		return lines;
	});

	/* The lines fold past six, so the promises and button stay in view. */
	const shownLines = $derived(
		moving.length > FOLD_AT + 1 && !unfolded['lines'] ? moving.slice(0, FOLD_AT + 1) : moving
	);
	const linesHidden = $derived(moving.length - shownLines.length);

	/** The lines with no number, always drawn: they make the press safe. */
	const promises = $derived.by(() => {
		const survivor = into?.name ?? '';
		if (weighed === null || !survivor) return [];
		/* Capitalised where the count leads a line. */
		const leader = going.length === 1 ? others : `The other ${going.length}`;
		/* A song: only the files move and the others go. */
		if (kind === 'song') {
			return [
				`${leader} ${going.length === 1 ? 'is' : 'are'} then removed`,
				'No file is deleted',
				"This can't be undone"
			];
		}
		return [
			'Nothing already filled in changes',
			`${leader} ${going.length === 1 ? 'is' : 'are'} then removed, and the ` +
				`${going.length === 1 ? 'name stays' : 'names stay'} searchable`,
			'No file is deleted',
			"This can't be undone"
		];
	});

	/** What the sheet is called. It names the act and, once there is a keeper, which way it goes. */
	const title = $derived(
		several
			? `Merge ${counted(picked.length)} ${words.many}`
			: `Merge ${only.name} into ${ANOTHER[kind].into}`
	);

	/** The one line under the title: the question this sheet is asking, and nothing else. */
	const subject = $derived(
		several
			? `Everything the others hold moves to whichever you keep. Pick the one to keep.`
			: `Everything ${only.name} holds moves to whichever you pick. ${only.name} is then removed.`
	);

	/** How many files a candidate holds, for the line under its name. Absent is said as nothing. */
	function held(one: MergeSubject): string | undefined {
		if (one.files === undefined) return undefined;
		return one.files === 1 ? '1 file' : `${counted(one.files)} files`;
	}

	function start() {
		failed = null;
		if (picked.length === 0) return;
		opened = true;
	}

	async function go() {
		const survivor = into;
		if (survivor === null || busy) return;
		busy = true;
		failed = null;
		try {
			await mergeSeveral(
				kind,
				going.map((one) => one.id),
				survivor.id
			);
			opened = false;
			onmerged(survivor.id);
		} catch (error) {
			refuse(error);
		} finally {
			busy = false;
		}
	}
</script>

<!-- The button and its failure stacked, not a fourth red button in the row. -->
{#if withButton}
	<div class="merge">
		<Button icon="merge" onclick={start}>Merge into&hellip;</Button>
		<Problem message={opened ? null : failed} />
	</div>
{/if}

<!-- One sheet, insistent; the acting button is this file's, since `Act` would close it while a
merge runs. -->
<!-- A candidate's picture; a Site with none wears the Sites glyph, never a person's letter. -->

{#snippet face(one: MergeSubject)}
	{#if one.picture || kind !== 'site'}
		<span class="face">
			<Avatar
				src={one.picture?.src}
				instead={one.picture?.instead}
				mark={one.picture?.mark}
				name={one.name}
				glyph={kind === 'song' ? glyphOf('song') : undefined}
			/>
		</span>
	{:else}
		<span class="face glyph" data-glyph="site"><Icon name="public" size={16} /></span>
	{/if}
{/snippet}

<Modal bind:open={opened} {title} description={subject} insistent sheetClass="merge-sheet">
	{#snippet children()}
		<div class="decide">
			{#if several && subjects.length === 0}
				<p class="quiet">{reading ? `Reading the ${words.many}` : ''}</p>
			{:else if several}
				<!-- A radio group of candidate cards; the default, most files, says why. -->

				<ChoiceGroup
					bind:value={keeping}
					onchange={() => (keeperPressed = true)}
					label="Which one to keep"
				>
					{#each biggestFirst as one, at (one.id)}
						<ChoiceCard
							value={one.id}
							name={one.name}
							note={held(one)}
							aside={at === 0 && (one.files ?? 0) > 0 ? 'most files' : undefined}
							dense
						>
							{#snippet preview()}{@render face(one)}{/snippet}
						</ChoiceCard>
					{/each}
				</ChoiceGroup>
			{:else if chosen !== null && !changing}
				<!-- Once picked, the list folds to that row, so the summary stays in view. -->
				<!-- Named once: a snippet's `chosen` is not narrowed by its branch. -->

				{@const kept = chosen}
				<ChoiceGroup value={kept.id} label="Which one to keep" layout="column">
					<ChoiceCard value={kept.id} name={kept.name} note={held(kept)} dense>
						{#snippet preview()}{@render face(kept)}{/snippet}
					</ChoiceCard>
				</ChoiceGroup>
				<div class="again">
					<!-- "Somebody" is a person's word; a Site is not somebody, as the title above knows. -->
					<Button tone="quiet" size="small" onclick={() => (changing = true)}>
						{ANOTHER[kind].pick}
					</Button>
				</div>
			{:else}
				<!-- The whole library, filtered by the server. -->
				<NarrowBox bind:value={typed} label={words.find} placeholder={words.find} />
				{#if found.length === 0}
					<p class="quiet">
						{looked ? 'Nothing matched.' : `Reading the ${words.many}`}
					</p>
				{:else}
					<ChoiceGroup
						bind:value={keeping}
						onchange={(id) => {
							chosen = found.find((one) => one.id === id) ?? null;
							changing = false;
						}}
						label="Which one to keep"
						layout="column"
					>
						{#each found as one (one.id)}
							<ChoiceCard value={one.id} name={one.name} note={held(one)} dense>
								{#snippet preview()}{@render face(one)}{/snippet}
							</ChoiceCard>
						{/each}
					</ChoiceGroup>
				{/if}
			{/if}
		</div>

		<!-- What it comes to, under the choice rather than behind a press. -->
		<div class="comes-to">
			{#if into === null}
				<p class="quiet">
					Pick the {words.one} to keep and this says exactly what would move.
				</p>
			{:else if weighed === null && busy}
				<p class="quiet">Counting what would move</p>
			{:else if weighed !== null}
				<ul class="lines">
					{#each shownLines as line (line.id)}
						{@const open = unfolded[line.id] === true}
						{@const names = line.names ?? []}
						{@const shown = open ? names : names.slice(0, FOLD_AT)}
						{@const beyond = line.beyond ?? 0}
						<li>
							{line.text}{#if shown.length > 0}: {shown.join(', ')}{/if}
							{#if !open && names.length > FOLD_AT}
								<Button tone="link" onclick={() => (unfolded = { ...unfolded, [line.id]: true })}>
									Show {counted(names.length - FOLD_AT + beyond)} more
								</Button>
							{:else if beyond > 0}
								and {counted(beyond)} more
							{/if}
						</li>
					{/each}
					{#if linesHidden > 0}
						<li>
							<Button tone="link" onclick={() => (unfolded = { ...unfolded, lines: true })}>
								Show {counted(linesHidden)} more
							</Button>
						</li>
					{/if}
					{#each promises as line (line)}
						<li class="quiet">{line}</li>
					{/each}
				</ul>
			{/if}
			<Problem message={failed} />
		</div>
	{/snippet}

	{#snippet footer({ Cancel })}
		<div class="buttons">
			<Cancel class="cancel">Cancel</Cancel>
			<Button
				tone="danger"
				disabled={into === null || weighed === null}
				busy={busy && weighed !== null}
				onclick={() => void go()}
			>
				{into === null ? 'Merge' : `Merge into ${into.name}`}
			</Button>
		</div>
	{/snippet}
</Modal>

<style>
	.merge {
		display: flex;
		flex-direction: column;
		align-items: stretch;
		gap: var(--space-1);
	}

	/* The two halves of the sheet: the decision, then what it comes to. */
	.decide,
	.comes-to {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.comes-to {
		margin-block-start: var(--space-4);
	}

	/* One outcome per line, no markers. */
	.lines {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* The way back out of a choice already made, on the RIGHT of the row like every other action. */
	.again {
		display: flex;
		justify-content: flex-end;
	}

	/* The picker's circle, sized here, or an avatar fills the card. */
	.face {
		display: block;
		flex: none;
		inline-size: var(--space-6);
		block-size: var(--space-6);
		overflow: hidden;
		border-radius: 50%;
	}

	/* A Site with nothing to draw: its glyph in the same box, quieter. */
	.glyph {
		display: grid;
		place-items: center;
		color: var(--sift-ink-3);
	}
</style>
