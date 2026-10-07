<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Two people, or two Sites, who turn out to be one.
	 *
	 * One sheet holding the decision and its consequences together: the candidates side by side as
	 * cards, what would happen in short lines under them, and one button that says which way it
	 * goes, like a stash-box reconciliation. There is no second step to reach, and changing the
	 * keeper brings the new keeper's numbers under it, answering "which way round?" by looking.
	 *
	 * It opens ready to act on whatever it was opened from: from a card's menu or with one thing
	 * selected, it asks what to merge into with nothing needing to be ticked first; with several
	 * selected, the keeper is a real choice shown with faces and counts, not whichever row happened
	 * to be listed first.
	 *
	 * It cannot be taken back, so the guard is before the fact. Putting a merge back would mean
	 * recreating a deleted person and deciding row by row which of the survivor's rows had been
	 * theirs, and a half-accurate undo of an identity is a library that looks correct and is
	 * quietly wrong. So there is none, and the numbers are on the screen instead.
	 *
	 * Nothing becomes unfindable: the name that goes is written onto the survivor as another name
	 * for it, which the summary says.
	 */
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

	/** Every word that differs between the two subjects, in one place.
	 *
	 * Beside the component rather than passed in, because these are facts about the KIND and not
	 * about the screen that opened the sheet: a caller free to word them is a caller free to word
	 * them differently, and the sheet would say "person" on one wall and "performer" on another. */
	const WORDS: Record<MergeableKind, { one: string; many: string; find: string }> = {
		person: { one: 'person', many: 'people', find: 'Search people' },
		site: { one: 'Site', many: 'Sites', find: 'Search Sites' },
		song: { one: 'song', many: 'songs', find: 'Search songs' }
	};

	/** The keeper, when it is somebody else: "somebody" is a person's word, and neither a Site
	 *  nor a song is somebody. */
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
		/**
		 * Who was picked, by id: the whole of what a wall hands over.
		 *
		 * Ids rather than rows from the loaded page: somebody found by a search or on another page
		 * is not on the loaded page, and must not silently fall out. The sheet reads every id from
		 * the server itself (`several`), and a pick that cannot be read refuses the sheet rather
		 * than shrinking it.
		 *
		 * Absent, the ids are those of `people`.
		 */
		ids?: string[];
		/**
		 * The people this is about, where the caller already holds them whole: one, from their own
		 * page. Read again by id like any other pick; what is here is drawn only until that lands.
		 *
		 * One sheet for both, because they are the same act asked two ways. With ONE the question is
		 * "who are they really", so the keeper is searched for across the whole library; with
		 * several it is "which of these do we keep", so the keeper is one OF them. What must never
		 * differ is what is read before the press, and here it cannot: the same summary, from the
		 * same count, in the same place.
		 */
		people?: MergeSubject[];
		/**
		 * Which of the two this is about. It decides the address, the body's field name and every
		 * word on screen.
		 *
		 * Defaulted to `person`, the subject most callers mean, so they need not say it.
		 */
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
	/**
	 * The picks as the SERVER has them, read by id when the sheet opens. Empty until that lands.
	 *
	 * Never the caller's rows: those came off one page of a wall, and a pick that page did not hold
	 * would silently fall out. The counts come back whole, too: a wall reached through
	 * a tab counts in context and hands none over, and "most files" is a question about the library.
	 */
	let subjects = $state<MergeSubject[]>([]);
	/** Whether the picks are still being read. */
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
	/** What has been typed into the search box, on the one-subject path. */
	let typed = $state('');
	/** What the server answered for what was typed. Empty until the first answer lands. */
	let found = $state<MergeSubject[]>([]);
	/** Whether the server has answered once, so an empty list can be told from a list not yet read. */
	let looked = $state(false);
	/** Whose id is being kept. Set by a press, defaulted for a selection. */
	let keeping = $state('');
	/**
	 * The row being kept, on the one-subject path, held rather than looked up.
	 *
	 * Looking it up in `found` would be a choice that DISAPPEARS the moment somebody types again:
	 * the search answers a different page, the chosen row is not on it, and the sheet quietly falls
	 * back to having no keeper, which reads as the press having been forgotten.
	 */
	let chosen = $state<MergeSubject | null>(null);
	/** Whether the chooser is open again over a choice already made. See the fold, below. */
	let changing = $state(false);
	let weighed = $state<Weighed | null>(null);

	/**
	 * The selection with the biggest first, and the one the sheet opens on.
	 *
	 * Keeping whoever holds the most files is what moves the least, so it is the answer that is
	 * right far more often than any other, which is what a default has to be to be worth having.
	 * It is a DEFAULT and not a decision, and the sheet SAYS so: the card carries "most files"
	 * beside the name, every candidate is drawn with its face and its count, and one press moves it.
	 *
	 * A count is not always there (a wall reached through a tab counts in context and hands none
	 * over: see `WallRow.count`), and a missing one counts as none, so the order then falls back
	 * to the order the selection arrived in.
	 */
	const biggestFirst = $derived(
		[...subjects].sort((one, other) => (other.files ?? 0) - (one.files ?? 0))
	);

	/** The row being kept, as a row rather than an id: the button and every line name it. */
	const into = $derived(
		several ? (biggestFirst.find((one) => one.id === keeping) ?? null) : chosen
	);

	/* Who is going, once the keeper is known.
	 *
	 * With several it is the selection minus whoever is kept; with one it is that one. The server
	 * drops the survivor from the list anyway, so this is about what the SUMMARY counts rather than
	 * about what is sent. */
	const going = $derived(
		several ? subjects.filter((one) => one.id !== keeping) : subjects.slice(0, 1)
	);

	/**
	 * What the things going are called in a line: one is named, more than one is counted. A name is
	 * worth four words of a sentence read before an irreversible press, and "the other 1" is worse
	 * than either.
	 */
	const others = $derived(
		going.length === 1 ? (going[0]?.name ?? '') : `the other ${going.length}`
	);

	/**
	 * The candidates to merge INTO, filtered by what has been typed, on the one-subject path.
	 *
	 * Asked of the store rather than of an address of this sheet's own, which is what makes the
	 * chooser reach the whole library rather than one page of it. The rows come back with their
	 * pictures, by the same rule the "Add to" flyout draws them by: a thing is recognised by its
	 * face before its name, and this sheet asks the hardest question in the application about which
	 * one is which.
	 */
	async function candidates(prefix: string): Promise<MergeSubject[]> {
		const page =
			kind === 'site'
				? (await siteStore.choices(prefix)).items.map(siteRow)
				: kind === 'song'
					? (await songStore.choices(prefix)).items.map(songRow)
					: (await peopleStore.choices(prefix)).items.map(personRow);
		return page.filter((one) => one.id !== only.id);
	}

	/*
	 * WHERE A REFUSAL GOES, and it is one rule: wherever this component still is.
	 *
	 * The sheet itself while it is open, which it is for the whole of the decision: there is
	 * no step that closes before the server answers. A toast only once the sheet has gone, which
	 * happens on exactly one path: the merge landed the sheet closed and then failed.
	 */
	function refuse(error: unknown): void {
		const message = problemFrom(error);
		failed = message;
		if (!opened && !withButton) toasts.show(message, { tone: 'error' });
	}

	/*
	 * Opening is a single assignment: `opened` is whether this sheet is on the screen, bound
	 * straight to the dialog, so no state can disagree with what is drawn.
	 */
	$effect(() => {
		if (!opened) return;
		/* Everything about the last opening is forgotten here rather than on the way out, because a
		   caller may reopen this on a different selection without it ever being told.
		 *
		 * UNTRACKED, and that is not tidiness: `people` is a prop, and two of the four callers build
		 * it with `.map` in the template: a fresh array on every redraw of the wall behind the
		 * sheet. Tracked, this effect would re-run on each of those and throw away the keeper
		 * somebody had just pressed, which on a busy wall is a choice that will not stay put. */
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

	/*
	 * EVERY PICK, READ BY ID. The keeper is settled when they land: whoever holds the most files,
	 * with the others folding into them, unless somebody has already pressed a card.
	 *
	 * A pick that cannot be read refuses the whole sheet. Merging the ones that could be read
	 * would be a silent shrink of what was picked.
	 */
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
	 * What the whole library has to offer, asked when the sheet opens and again a beat after typing
	 * stops. An answer to an older question is dropped rather than drawn over a newer one.
	 *
	 * Only on the one-subject path: with several, the candidates ARE the selection and there is
	 * nothing to fetch.
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

	/*
	 * WHAT WOULD MOVE, COUNTED AS SOON AS THERE IS A KEEPER, and counted again the moment the
	 * keeper changes.
	 *
	 * That is the whole reason the count is here rather than behind a "weigh it up" press: the
	 * question somebody has in front of two candidates is which way round it should go, and the
	 * only thing that answers it is seeing both sets of numbers. A press between the choice and the
	 * count makes comparing them a matter of memory.
	 *
	 * It writes nothing. A generation counter drops a stale answer, because pressing three
	 * candidates in a row starts three requests and the slowest may land last.
	 */
	let asking = 0;
	/* The question already asked, as one string.
	 *
	 * The guard is against the SAME question being asked twice, not against the answer being wrong:
	 * `going` is derived from a prop two callers rebuild on every redraw, so without this a wall
	 * repainting behind the sheet would start a fresh count each time: a POST per repaint, and a
	 * summary that flickers while nothing has changed. */
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

	/**
	 * One line of what would happen: a sentence, and, where the server named them, the things
	 * it is about, folded past five.
	 *
	 * `beyond` is how many the count holds that the server did not name (it names at most fifty of
	 * any one kind), so a line never claims a list is whole when it is not.
	 */
	interface Line {
		id: string;
		text: string;
		names?: string[];
		beyond?: number;
	}

	/** Which folded lines have been opened in place. Forgotten on every opening. */
	let unfolded = $state<Record<string, boolean>>({});

	/**
	 * What would happen, in short lines rather than a paragraph, and by name.
	 *
	 * The server names each thing (`MergeWeighed.*_named`, `filled`), and each is its own line:
	 * which username on which Site, which aliases, which links, whose faces, and for every blank
	 * that is filled, the box, the value and whose it was.
	 *
	 * Files come first, because that is the number that decides the press. Only kinds with
	 * something in them are said, since a list of noughts buries the one that matters. A count with
	 * no names beside it is said as the count alone.
	 */
	const moving = $derived.by((): Line[] => {
		const survivor = into?.name ?? '';
		if (weighed === null || !survivor) return [];
		const w = weighed;
		const lines: Line[] = [];
		/* One is spelled out with a singular sentence of its own, and more are counted with the
		   names after a colon. Written as a pair rather than as a plural with an `s` bolted on,
		   because "1 usernames move" is the kind of sentence that makes a screen look unfinished at
		   the exact moment somebody is deciding whether to trust it. */
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
		listed(
			'links',
			w.links,
			(w.links_named ?? []).map((one) => one.name),
			(name) => `Link ${name} moves too`,
			'1 link moves too',
			'links move too'
		);
		// A Site published under one that goes does not disappear: it changes which Site it is
		// under. The clause saying so is the whole reason this line is not "3 Sites move".
		listed(
			'children',
			w.children,
			(w.children_named ?? []).map((one) => one.name),
			(name) =>
				`${name}, published under ${others}, is published under ${survivor} instead \u2014 it isn't removed`,
			`1 Site published under ${others} is published under ${survivor} instead \u2014 it isn't removed`,
			`Sites published under ${others} are published under ${survivor} instead \u2014 none of them is removed`
		);
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
	});

	/*
	 * The lines fold too, past six: a person who was looked up can fill fifteen boxes in one go,
	 * and fifteen lines push the promises and the button below the fold of the sheet. The first six
	 * always show (the files and what moves come first) and "and N more" opens the rest in place.
	 */
	const shownLines = $derived(
		moving.length > FOLD_AT + 1 && !unfolded['lines'] ? moving.slice(0, FOLD_AT + 1) : moving
	);
	const linesHidden = $derived(moving.length - shownLines.length);

	/**
	 * The lines with no number in them, which are the same every time.
	 *
	 * Always drawn, unlike the counts: they are what makes the press safe to make, and a
	 * reassurance that appears only sometimes is one somebody has to go looking for.
	 */
	const promises = $derived.by(() => {
		const survivor = into?.name ?? '';
		if (weighed === null || !survivor) return [];
		/* The count is capitalised where it LEADS a line: "the other 2 are then removed" reads as a
		   fragment of the line above it, and these are read as statements one at a time. A name
		   already carries its own capital. */
		const leader = going.length === 1 ? others : `The other ${going.length}`;
		/* A song keeps no other names and has no fields a merge fills, so its lines say only what
		   a song merge does: the files move, the others go. */
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

<!-- The button and its failure STACKED, not side by side. This sits inside a row of actions, so a
     message rendered as a sibling takes a place in that row and reads as a fourth red button. -->
{#if withButton}
	<div class="merge">
		<Button icon="merge" onclick={start}>Merge into&hellip;</Button>
		<Problem message={opened ? null : failed} />
	</div>
{/if}

<!--
	ONE SHEET: the decision and what it comes to, together.

	`insistent`, because this is a destructive question and that is what the flag is for. The acting
	button is this file's own rather than the dialog's `Act`, deliberately: `Act` closes the sheet as
	it is pressed, and a merge of five thousand files takes long enough that the sheet has to stay
	while it runs and has to be able to fail back onto itself. Cancel is still the library's, so
	Escape, the veil and the button all mean the same thing.
-->
<!--
	THE PICTURE AT THE HEAD OF A CANDIDATE, written once for the three places it is drawn.

	A Site with no picture wears the Sites glyph, never a letter. `siteRow` answers NO picture for a
	Site the shipped pack does not cover and nothing was chosen for, and says the row then wears the
	verb's glyph, which the "Add to" flyout does. Handed that empty answer, `Avatar` with nothing
	to draw would draw the name's first letter: the coloured letter a PERSON with no cover wears,
	on a Site.
	A person still falls through to the letter, which is what a person with no face looks like, and
	a song to the music glyph, which is what a song with no cover looks like everywhere.
-->
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
				<!--
					THE CANDIDATES SIDE BY SIDE, as cards with their faces on them.

					A radio group rather than a list of ticks: there is exactly one answer, and arrow
					keys, roving focus and the checked semantics are all the library's. The one held
					up is the one with the most files, and its card says why in as many words: a
					default nobody can see the reason for reads as the machine having decided.
				-->
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
				<!--
					ONCE SOMEBODY IS PICKED, THE LIST FOLDS AWAY TO THAT ONE ROW.

					A list of sixty candidates left open puts the summary a screen below the choice, so
					what the press would do is out of sight at the moment it is decided, which is the
					one thing this sheet exists to prevent. The chosen row
					stays drawn because it is the answer, and the way back is a press beside it.
				-->
				<!-- Named once: a snippet is its own closure, so `chosen` read inside it is not narrowed
				     by the branch it is drawn in. -->
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
				<!-- The whole library, filtered by the server, so the chooser holds everybody
				     rather than one page filtered in the browser. -->
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

	/* One outcome per line, read down. The marker is dropped because each line is a whole statement
	   rather than an item in a set, and a column of dots in front of eight sentences is furniture. */
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

	/* The picture at the head of a candidate, and it is the picker's box exactly: a circle off
	   the space scale, holding whatever `Avatar` falls through to. Sized here because `ChoiceCard`
	   does not size what a caller puts in its preview, and an unsized avatar fills the card: two
	   candidates then stand a screen apart with the summary below the fold, which is the shape this
	   sheet exists to undo. */
	.face {
		display: block;
		flex: none;
		inline-size: var(--space-6);
		block-size: var(--space-6);
		overflow: hidden;
		border-radius: 50%;
	}

	/* A Site with nothing to draw: the kind's glyph in the same box, so the names still line up, in
	   the quieter ink the "Add to" flyout gives the same row: it says what KIND a row is. */
	.glyph {
		display: grid;
		place-items: center;
		color: var(--sift-ink-3);
	}
</style>
