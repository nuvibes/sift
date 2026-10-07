<script lang="ts">
	import type { Crumb } from '$lib/components/common';
	import type { Frame } from '$lib/entity/cover-frame';
	import { filesSized, sizeOf } from '$lib/entity/entity-counts';
	import { untrack } from 'svelte';
	/*
	 * One person: their name, the vault flag, and the editor for the other names they go by.
	 *
	 * The alias list here holds names somebody typed. A USERNAME is not listed among them, deliberately:
	 * it is a name on one Site rather than another name for the person, and listing it here would
	 * invite deleting it and being surprised the username survived. Usernames are drawn on the Sites tab
	 * instead, under the card of the Site each one is on. See `usernamesHere` below.
	 */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { page } from '$app/state';
	import {
		Button,
		Chip,
		ChipRow,
		ConfirmDialog,
		ContextMenuItem,
		Empty,
		Field,
		Problem,
		Skeleton
	} from '$lib/components/common';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import WaitingForYou from '$lib/components/faces/WaitingForYou.svelte';
	import HeldFaces from '$lib/swap/HeldFaces.svelte';
	import Disagreements from '$lib/components/record/Disagreements.svelte';
	import RecognitionStrength, { startersSay } from '$lib/components/RecognitionStrength.svelte';
	import EntityHeader from '$lib/components/entity/EntityHeader.svelte';
	import EntityHistory from '$lib/components/entity/EntityHistory.svelte';
	import { waitingText } from '$lib/entity/reconcile.svelte';
	import MergeEntities from '$lib/components/entity/MergeEntities.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import PickPicture from '$lib/components/entity/PickPicture.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import RecordForm from '$lib/components/record/RecordForm.svelte';
	import RecordSummary from '$lib/components/record/RecordSummary.svelte';
	import RecordFacts from '$lib/components/entity/RecordFacts.svelte';
	import RecordView from '$lib/components/record/RecordView.svelte';
	import StashBoxIds from '$lib/components/record/StashBoxIds.svelte';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import PickedFilter from '$lib/components/entity/PickedFilter.svelte';
	import { narrowedFilesTotal, picksOf, tabsCarryingPicks } from '$lib/components/entity/picks';
	import RelatedWall from '$lib/components/entity/RelatedWall.svelte';
	import TabHold, { wallOfTab } from '$lib/components/entity/TabHold.svelte';
	import {
		showing as chosenTab,
		iconOf,
		tabsFor,
		type RelatedKind,
		TabCounts,
		TabWords
	} from '$lib/entity/related.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { emptyWallSays } from '$lib/components/shell/wall-words';
	import ShareDialog from '$lib/components/ShareDialog.svelte';
	import VisibilityDialog from '$lib/components/VisibilityDialog.svelte';
	import type { ShareTarget } from '$lib/library/sharing';
	import { people, type Alias, type Link, type Person } from '$lib/people/people.svelte';
	import { UsernamesByCard } from '$lib/people/usernames.svelte';
	import UsernameLines from '$lib/components/entity/UsernameLines.svelte';
	import { libraryChanges, reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { EntitySubject } from '$lib/entity/subject.svelte';
	import { underCover } from '$lib/people/under-cover.svelte';
	import { entityTags } from '$lib/entity/entity-tags.svelte';
	import { fields } from '$lib/entity/records.svelte';
	import LinkToStashBox from '$lib/components/record/LinkToStashBox.svelte';
	import { givenBy, sourcesOf, type Maker, type StashBoxLink } from '$lib/entity/enrich.svelte';
	import {
		EntityEnrichment,
		autoEnrichRows,
		enrichBoxes,
		loadEnrichBoxes,
		sayKeptLocal,
		setKeptLocal
	} from '$lib/entity/enrichment.svelte';
	import { enrichMany } from '$lib/entity/enrich-many.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { api } from '$lib/api/client';
	import { setHidden } from '$lib/library/hiding';
	import { vault } from '$lib/shell/vault.svelte';
	import type { components } from '$lib/api/schema';
	import EntityDropZone from '$lib/components/entity/EntityDropZone.svelte';

	const personId = $derived(page.params.id ?? '');

	/* History has no wall behind it, so it is kept out of `tabsFor`. */
	const HISTORY = 'history';

	const asked = $derived(page.url.searchParams.get('show'));
	const showingHistory = $derived(asked === HISTORY);

	/* The tab, read off the address so each tab is a place Back and a link reach; unknown means Files. */
	const shown = $derived<RelatedKind>(chosenTab('person', asked));
	const fileWords = new TabWords();

	/*
	 * THIS PERSON'S USERNAMES, filed by the Site each is on, for the Sites tab.
	 *
	 * A username has no page of its own; it is shown here, under the card of its Site, with how
	 * many files were posted under it and the Site's own number when that is known. One read for
	 * the whole tab (see `UsernamesByCard`).
	 *
	 * Read only while the Sites tab is showing (no other tab draws them), and again whenever the
	 * library moves, because a filing, a join or a typed number all change what a line says.
	 */
	const usernamesHere = new UsernamesByCard('site_id');

	$effect(() => {
		void libraryChanges.generation;
		const who = personId;
		if (shown !== 'sites' || !who) return;
		untrack(() => void usernamesHere.read({ personId: who }));
	});

	/* The numbers beside the tab words: all asked at once, and a wall's own answer wins (`TabCounts`). */
	const counts = new TabCounts();
	$effect(() => counts.follow('person', personId));
	/* The strip's numbers follow the library as its walls do: History has no wall to report one. */
	reloadOnLibraryChange(() => counts.refresh());

	/* How many fields a stash-box disagrees with about this record, out of the strip's own numbers.
	 *
	 * Off the counts map rather than asked for separately, because it arrives with every other number
	 * on that strip (one request, one moment), and because the panel on the History tab OVERWRITES
	 * it through `saw` the moment it has read them itself. Two readers of one number is how a mark
	 * comes to stand over a panel that has nothing in it.
	 *
	 * Undefined until the strip has answered, which draws no mark: a mark that appeared and then went
	 * again would report a question that was never there. */
	const disagreeing = $derived(counts.current.disagreements ?? 0);

	/*
	 * WHAT HAPPENED TO THEM is `EntityHistory`, which owns all of it: the asking, the two states an
	 * array cannot tell apart, and the Undo. It is a file of its own with its own tests (mounting
	 * this page takes fifteen stand-ins), and this page says which subject and which one of them.
	 */

	let aliases = $state<Alias[]>([]);
	let links = $state<Link[]>([]);
	let busy = $state(false);
	let failed = $state(false);
	let confirmVault = $state(false);
	/* Bumped when suggestions are agreed to or what a swap brought is added, so the recognition
	   bar beside them re-reads: those are what move that number, and they sit together on purpose. */
	let agreed = $state(0);
	/* The stash-boxes this person's starter pictures came from, as the recognition bar reads them,
	   or null where Sift already knows them by a face somebody confirmed. */
	let starterBoxes = $state<readonly string[] | null>(null);

	/** Rising counter, so aliases fetched for the person we have just navigated away from cannot
	 * land under the name of the one we navigated to. Without it the previous person's other
	 * names appear under this person's heading, and removing one from there deletes nothing. */
	let generation = 0;

	/* The subject, fetched by id.
	 *
	 * Not read out of the shared list, which is one page of the WALL: a bounded number of rows in
	 * a chosen order. Anybody ranked past the cap would have no page at all, and anything that
	 * replaced the cached page (the vault opening or shutting, a reload, a later page being
	 * fetched) would take the subject out from under an already-open page.
	 *
	 * Every write below assigns back into this. The store owns the writing; this is where the
	 * page finds out who it is about.
	 */
	/* Whether the form is up. On the PAGE rather than inside the header, because editing replaces
	 * what the page is showing: one save at the end of a whole record is a screen's worth of boxes,
	 * and a strip above a wall of files is not where that goes. */
	let editing = $state(false);
	/* The subject and its fetching flags, shared with every entity page (`EntitySubject`): a re-read
	 * leaves this person on screen. `follow` also hears a share given or taken back, which changes
	 * what this account may see and announces no import job. */
	const subject = new EntitySubject<Person>((id) => people.one(id));
	subject.follow(() => personId);
	/* Held until what sits under the cover answers too, so the header is drawn once (`underCover`). */
	const cover = underCover(() => personId);
	/* Read through a `const` so the markup keeps its narrowing. A field on a class is not narrowed
	 * inside an event handler (the checker has to assume anything could have reassigned it since
	 * the enclosing block was entered), and every button on this page reads a field off it. */
	const person = $derived(subject.value);

	/* The whole record, saved once.
	 *
	 * Everything is diffed against what is stored rather than written unconditionally: a rename
	 * costs a re-index of everything filed under that name, and adding an alias that is already
	 * there is a refusal the person editing did not cause. Nothing is written for a field nobody
	 * touched.
	 *
	 * The name and the details ride together because the route that takes them replaces what it
	 * is sent: sending one without the other renames somebody to nothing, or clears what an
	 * admin wrote, as a side effect of the other edit.
	 */
	async function saveRecord(draft: Record<string, unknown>) {
		if (!person) return;
		const wantedName = String(draft.name ?? '').trim() || person.name;
		const wantedNotes = String(draft.details ?? '').trim();
		/* The row is written every time, even when the name and the details are unchanged: the
		 * record is on that row, so skipping the write when the name is the same would throw away
		 * a changed birthdate, and editing a record is usually editing everything except the
		 * name. The other names and the addresses ride on the same request, so an address the
		 * server refuses refuses the whole save rather than leaving the rename behind it.
		 */
		const wantedAliases = ((draft.aliases as string[]) ?? []).map((one) => one.trim());
		const wantedLinks = ((draft.links as string[]) ?? []).map((one) => one.trim());
		subject.value = await people.update(
			person.id,
			wantedName,
			person.vault,
			wantedNotes || null,
			recordFrom(draft),
			{
				aliases: wantedAliases.filter(Boolean),
				links: wantedLinks.filter(Boolean)
			}
		);
		notes = wantedNotes;

		/* Tags, which the form edits into its draft rather than writing as they are pressed. See
		   `RecordForm`. Diffed, so a record saved with nothing changed writes no tag. */
		const wantedTags = (draft.tags as { id: string }[] | undefined) ?? [];
		for (const gone of tags.filter((one) => !wantedTags.some((held) => held.id === one.id))) {
			await entityTags.set('people', person.id, gone.id, false);
		}
		for (const added of wantedTags.filter((one) => !tags.some((held) => held.id === one.id))) {
			await entityTags.set('people', person.id, added.id, true);
		}

		await refreshLists(person.id);
		editing = false;
		toasts.show('Saved', { tone: 'success' });
	}

	/* How many files the Files tab holds while picks filter it (null: nothing picked). Asked of
	   the same listing the tab reads, once per change of the picks, with a sequence number so a
	   slower answer for picks moved away from cannot land over the current one. */
	let narrowedFiles = $state<number | null>(null);
	let narrowedAsk = 0;
	$effect(() => {
		const url = page.url;
		const subject = person;
		const ask = (narrowedAsk += 1);
		if (!subject || picksOf(url).length === 0) {
			narrowedFiles = null;
			return;
		}
		void narrowedFilesTotal(url, { people: person.id }).then((total) => {
			if (ask === narrowedAsk) narrowedFiles = total;
		});
	});

	const tabs = $derived([
		...tabsFor('person', personId, `/people/${personId}`, {
			...counts.current,
			// Files is the one wall this page does not fetch (the media grid does), and the
			// person's own row already carries that number, scoped the same way.
			// The filtered figure while picks are in force, the record's own otherwise. See `narrowedFilesTotal`.
			files: narrowedFiles ?? person?.asset_count
		}),
		/* And it wears its number: the strip is a MAP of what this page can show, and one bare
		   word on a row of numbered ones reads as a tab nobody has looked at yet. It is the
		   length of the very thread the pane draws, read beside the strip (`readThread`). */
		{
			id: HISTORY,
			label: 'History',
			icon: 'history' as const,
			href: `/people/${personId}?show=${HISTORY}`,
			count: counts.current.history,
			/* And a mark where a stash-box disagrees with this record. It is on THIS word
			   because the panel that settles it is at the top of this tab, so the mark is
			   what says the question is there without anybody opening anything. Absent at
			   nought and absent for anyone who may not settle them, which is what an absent
			   count from the strip already means. */
			attention: disagreeing > 0 ? waitingText(disagreeing, counts.boxes) : undefined
		}
	]);

	/* Sharing a person shares everything they are in, and keeps doing so as more arrives, which is
	   the reason the logical axis exists at all. A folder share is a share of what is on a disk; this
	   is a share of a subject, and it reaches next month's files without anybody going back to it. */
	let shareOpen = $state(false);
	let reachOpen = $state(false);
	const shareTarget = $derived<ShareTarget | null>(
		person ? { type: 'person', id: person.id, label: person.name } : null
	);

	/* The tags on this person, fetched per page and forgotten with it. Keyed on the id alone, for
	 * the same reason the aliases effect below is: reading the person object would make the whole
	 * list a dependency and refetch these on every drag-assign anywhere in the app. */
	const tags = $derived(entityTags.items);

	$effect(() => {
		const id = personId;
		entityTags.forget();
		if (id) void entityTags.load('people', id);
	});

	/* The store puts a failed write back on its own, so all these have to do is say so. Silence
	 * would be a heart that springs back with nothing on screen to explain it. */
	async function heart(favorite: boolean) {
		try {
			await people.setFavorite(personId, favorite);
			subject.value = person ? { ...person, favorite } : person;
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(rating: number | null) {
		try {
			await people.setRating(personId, rating);
			subject.value = person ? { ...person, rating } : person;
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* Put a tag on. Admin-only, like every other write to shared vocabulary: a rating is an
	 * opinion somebody holds privately, a tag changes what everybody else's searches return. */
	async function addTag(tag: { id: string }) {
		try {
			await entityTags.set('people', personId, tag.id, true);
		} catch {
			toasts.show("That tag couldn't be added", { tone: 'error' });
		}
	}

	async function untag(tagId: string) {
		try {
			await entityTags.set('people', personId, tagId, false);
		} catch {
			toasts.show("That tag couldn't be removed", { tone: 'error' });
		}
	}

	// Keyed on the id alone. Reading `people.byId` in here would make the whole list a dependency,
	// so every drag-assign anywhere would refetch these and widen the window above.
	//
	// AND ON THE LIBRARY BELL, as the row above is (`subject.follow`): a merge writes the name that
	// went onto the survivor as another name and brings the other person's links with it, and a
	// page standing on the survivor has to list them without a reload.
	$effect(() => {
		void libraryChanges.generation;
		void refreshLists(personId);
	});

	/** The other names and the links, re-read. Also what a save calls, so the record shows what was
	 *  stored rather than what was typed. */
	async function refreshLists(id: string) {
		if (!id) return;
		const mine = ++generation;
		try {
			const found = await people.aliases(id);
			if (mine !== generation) return;
			aliases = found;
			failed = false;
		} catch {
			if (mine === generation) failed = true;
		}
		try {
			const found = await people.links(id);
			if (mine === generation) links = found;
		} catch {
			// An empty list rather than an error: the page is readable without them, and a screen
			// that refuses to draw because a secondary fetch failed is worse than a missing row.
			if (mine === generation) links = [];
		}
	}

	/*
	 * Notes: free text an admin wrote about somebody.
	 *
	 * Read from a route of their own rather than from the person row, because the row is what every
	 * wall and every suggester draws and notes belong on none of them. Written through the person
	 * route, which is the only writer of the column. The read being separate does not make it a
	 * second way to change it.
	 *
	 * Asked for by anybody signed in, not only an admin. Details is part of what the record says,
	 * and a guest who saw four of its five fields would be reading a record that looks broken. The
	 * server agrees: the read is open, the write is not, and neither is in the search index.
	 *
	 * The same editor the site page has, for the same reason a heart is the same heart: two boxes
	 * that both mean "what I wrote about this" should not behave differently.
	 */
	let notes = $state('');
	let notesFor = $state('');
	let notesLoaded = $state(false);

	/* Everything the record is made of, in one place.
	 *
	 * The readout, the summary under the name and the form are three surfaces over the same facts,
	 * and each of them building its own object is how one of them ends up a field behind. The form
	 * takes the links as plain addresses because that is what its list editor edits; everything else
	 * is identical.
	 */
	/* The stash-boxes that have been agreed to know this person.
	 *
	 * Its own read, because it belongs to a different slice and is optional: an install with no
	 * stash-boxes configured gets an empty list and every screen here works exactly as before. A
	 * failure is an empty list too: the record is not the place a network problem surfaces.
	 */
	let sources = $state<StashBoxLink[]>([]);
	/* Which box MADE this thing, read in the same answer the links come in (see `sourcesOf`).
	   Null for everything nothing recorded, which is most of a library. */
	let madeBy = $state<Maker | null>(null);
	let sourcesFor = $state('');
	let lookUpOpen = $state(false);
	let mergeOpen = $state(false);

	/* Everything this page can do to this person, behind the one door.
	 *
	 * Declared rather than drawn, which is what lets the same rows sit in the same menu on every
	 * entity page instead of each growing its own row of named buttons, which would be the widest
	 * thing on a page about somebody's files.
	 *
	 * Merging keeps its component (it owns a chooser, a count of what would move and a warning
	 * that cannot be taken back) and does not draw its own button, the same seam the wall's menu
	 * uses.
	 */
	/* WHERE THIS ROW STANDS WITH ENRICHMENT, so the two rows that send its name outside are
	   drawn refused rather than refused on the press. See `EntityEnrichment`: one route answers
	   it, and pressing the row below writes the reply back. */
	const enrichment = new EntityEnrichment('person');
	enrichment.follow();
	const enrichState = $derived(enrichment.of(person?.id));
	const enrichRefused = $derived(enrichState?.refused === true);
	const enrichWhy = $derived(enrichState?.why || undefined);
	const enrichKept = $derived(enrichState?.kept_local === true);
	$effect(() => {
		enrichment.ask(person?.id);
	});
	/* The box list for Auto-enrich's row per box: the same list every wall's flyout reads. */
	$effect(() => {
		if (session.isAdmin) loadEnrichBoxes();
	});
	/* AUTO-ENRICH, THE SAME ROWS EVERY SURFACE DRAWS: every box, then each box by name, and the
	   box handed on. With no list it is a plain press, which asks what Settings says. See
	   `autoEnrichRows`, which also says why there is no "Sift's own" row. */
	function autoEnrichThis(id: string, box: string = ''): void {
		if (enrichRefused) return void sayKeptLocal();
		void enrichMany('person', [id], box);
	}

	const options = $derived<Verb[]>(
		!person
			? []
			: [
					...(session.isAdmin
						? [
								/* Auto-enrich, Enrich, Do not enrich: the order every wall and file menu uses,
								   so the three are found in one place by shape wherever they are offered. */
								{
									id: 'auto-enrich',
									label: 'Auto-enrich',
									icon: 'auto_fix_high' as const,
									...(enrichRefused
										? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) }
										: {}),
									...(enrichBoxes().length > 0 && !enrichRefused
										? {
												children: autoEnrichRows(enrichBoxes(), (_ids, box) =>
													autoEnrichThis(person.id, box)
												)
											}
										: { run: () => autoEnrichThis(person.id) })
								},
								{
									id: 'enrich',
									label: 'Enrich',
									icon: 'backlight_low' as const,
									...(enrichRefused
										? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) }
										: {}),
									run: () => (enrichRefused ? sayKeptLocal() : (lookUpOpen = true))
								},
								/* KEPT LOCAL, beside the two rows it refuses. See the Site page. */
								{
									id: 'keep-local',
									label: enrichKept ? 'Allow enrichment' : "Don't enrich",
									icon: enrichKept ? ('public' as const) : ('shield' as const),
									run: () =>
										void setKeptLocal('person', person.id, !enrichKept).then((state) =>
											enrichment.mark([person.id], state)
										)
								},
								{
									id: 'share',
									label: 'Sharing',
									icon: 'group' as const,
									run: () => (shareOpen = true)
								},
								/* What the sharing above it comes to. Share is where a decision is made; this
								   reports who can actually reach this, however the reach was arranged: through a
								   folder, a tag, a set, or the network above a label, none of which are written
								   here. */
								{
									id: 'visibility',
									label: 'Visibility',
									icon: 'policy' as const,
									run: () => (reachOpen = true)
								},
								{
									id: 'merge',
									label: 'Merge into\u2026',
									icon: 'merge' as const,
									run: () => (mergeOpen = true)
								}
							]
						: []),
					/* Only reachable with the vault open: with it shut, this page answers 404 for them. So the
		   row appears exactly when it can work. */
					person.vault
						? {
								id: 'unhide',
								label: 'Stop hiding them',
								icon: 'visibility' as const,
								run: () => void reveal()
							}
						: {
								id: 'hide',
								label: 'Hide them',
								icon: 'visibility_off' as const,
								run: () => (confirmVault = true)
							}
				]
	);

	/* Arriving with the chooser already open, because a menu somewhere else asked for it.
	 *
	 * `Enrich` on the wall cannot draw this sheet itself (it needs the record's own values and
	 * its save), so it navigates here and says so in the address. Reading it here rather than
	 * passing state through the navigation means the address is the whole story: it survives a
	 * reload, and it can be sent to somebody.
	 *
	 * Once, on arrival, and not as a `$derived`: this is a door being opened, not a fact about the
	 * page. Left reactive, closing the sheet with the parameter still in the address would
	 * immediately re-open it.
	 */
	$effect(() => {
		if (untrack(() => lookUpOpen)) return;
		// Refused before the sheet opens, even where the address asked for it. See the Site page.
		if (page.url.searchParams.get('enrich') === '1' && !untrack(() => enrichRefused))
			lookUpOpen = true;
	});

	$effect(() => {
		const id = personId;
		if (!id || sourcesFor === id) return;
		sourcesFor = id;
		void (async () => {
			const held = await sourcesOf('person', id);
			// A slower answer for somebody navigated away from must not land under this heading.
			if (sourcesFor === id) {
				sources = held.links;
				madeBy = held.madeBy;
			}
		})();
	});

	/** After the chooser links and takes fields: the link list AND the record, because the take
	 *  route wrote through the record's own writer (so a hand link records what it filled in, the
	 *  same as one made from a queue) and this page holds a copy. */
	async function afterLinked() {
		await reloadSources();
		if (!personId) return;
		try {
			subject.value = await people.one(personId);
		} catch {
			/* The save has landed; a failed re-read is a stale screen, not a failed save. */
		}
	}

	async function reloadSources() {
		if (!personId) return;
		const held = await sourcesOf('person', personId);
		sources = held.links;
		madeBy = held.madeBy;
	}

	const recordValues = $derived({
		/* Everything the server keeps on the person's own row, spread FIRST so the five below win.
		 *
		 * Those five are not columns on that row. They are their own tables, or they are read by
		 * their own route, and this page holds each of them separately because each is written
		 * separately. Anything else the record gains is a column, arrives here already, and needs no
		 * line adding: that is the point of the record being a mapping keyed the way the registry
		 * names its fields. */
		...(person?.record ?? {}),
		name: person?.name ?? '',
		aliases: aliases.map((one) => one.alias),
		details: notes,
		links,
		tags,
		sources
	});

	/* The keys the person's own ROW holds, taken from the registry rather than listed here.
	 *
	 * The five the page writes another way are named once, in one place, and everything else the
	 * registry declares goes to the record. A list of fifteen field names written into this page
	 * would be a second copy of the server's declaration, and the copy that stops being edited.
	 */
	/* `accounts` and `sources` are their own tables and are never written from here. `age` is
	   arithmetic on the birthdate and there is nothing to write it to. The server ignores a key
	   it does not pair with a column, so leaving it in would work and would read as an attempt to
	   store something that cannot be stored. */
	const APART = ['name', 'details', 'aliases', 'links', 'tags', 'accounts', 'sources', 'age'];

	function recordFrom(draft: Record<string, unknown>): Record<string, unknown> {
		const out: Record<string, unknown> = {};
		for (const one of fields.of('person')) {
			if (APART.includes(one.key)) continue;
			out[one.key] = draft[one.key] ?? null;
		}
		return out;
	}

	$effect(() => {
		const id = personId;
		if (!id || notesFor === id) return;
		notesFor = id;
		notesLoaded = false;
		void (async () => {
			try {
				const held = await api.get<components['schemas']['PersonNotes']>(`/people/${id}/notes`);
				// A slower answer for somebody navigated away from must not land under this heading.
				if (notesFor !== id) return;
				notes = held.notes ?? '';
			} catch {
				if (notesFor === id) notes = '';
			} finally {
				if (notesFor === id) notesLoaded = true;
			}
		})();
	});

	/** The id the header's Save submits. One form is open at a time, so one name is enough. */
	const RECORD_FORM = 'person-record-form';

	/* Deleting the person.
	 *
	 * Their FILES are untouched: `asset_people` names the asset with the cascade pointing the other
	 * way, so what goes is the person, their other names, and the rows joining them to files. The
	 * sentence on the question says so, because that is the fact somebody wants before pressing it.
	 */
	async function removePerson() {
		if (!person) return;
		await people.remove(person.id);
		toasts.show('Deleted. Their files are still here.', { tone: 'success' });
		await leaveFor('/people');
	}

	/* What the two below are about, in one place: the noun the sentence uses, the plural it cannot
	   guess, and the write itself. The same four lines the People wall passes. */
	const hiddenAs = $derived({
		noun: 'person',
		plural: 'people',
		stays: vault.unlocked,
		set: (id: string, flag: boolean) => people.setVault(id, flag)
	});

	/*
	 * Hiding somebody, and bringing them back, through the ONE mechanism that does it.
	 *
	 * `people.setVault` is the per-account vault route, not the record write an admin makes, so a
	 * guest's Hide is not a 403, and everything around the write (the Privacy wording, a 401 read
	 * like the 409, an Undo on the sentence) comes from `hiding.ts`, which the People WALL uses
	 * too.
	 *
	 * `stays` is the vault's state and not a constant: with Hidden open they are still listed, so
	 * "unlock Hidden to see them" would be advice about somebody who is on the screen.
	 */
	async function conceal() {
		if (!person) return;
		const moved = await setHidden([person.id], true, hiddenAs);
		// Back to the list, because this page is about to stop answering: with the vault shut they
		// are concealed from every route that names them, this one included. Only on a write that
		// actually landed. A refusal has already been said, and leaving the page would take the
		// sentence away with it.
		if (moved.length > 0) await leaveFor('/people');
	}

	/** Whether the picture chooser is open. Opened by the pencil on the cover, while editing. */
	let pickingPicture = $state(false);

	/* Set the still this person is drawn as, from one of their own files. Admin-only, like the
	 * server behind it. */
	async function makeCover(
		assetId: string,
		atMs: number | null = null,
		frame: Frame | null = null
	) {
		try {
			subject.value = await people.setCover(personId, assetId, atMs, { frame });
			toasts.show('Cover set', { tone: 'success' });
		} catch {
			toasts.show("That couldn't be used as the cover", { tone: 'error' });
		}
	}

	/* A picture from the disk rather than from the library. The same two lines as the pick, and
	   the refusal is deliberately NOT caught here: the sheet shows the server's own sentence, which
	   is the half that knows whether the file was too big or was not readable as a picture. */
	async function uploadCover(file: File) {
		subject.value = await people.uploadCover(personId, file);
		toasts.show('Cover set', { tone: 'success' });
	}

	/* The same write pointed the other way, and nothing to navigate: the route answers 204 and
	   rings the library bell, so this page re-reads itself where it stands. */
	async function reveal() {
		if (!person) return;
		await setHidden([person.id], false, hiddenAs);
	}
	/* The trail, handed to whichever grid is showing and drawn by the frame's band above the
	   identity band: the same trail on every tab of this page. */
	const crumbs = $derived<Crumb[]>([
		{ label: 'People', href: '/people' },
		{ label: person?.name ?? '' }
	]);

	/* What the header says under the name: how many files they are on. This account's own O tally
	   over them is handed to the header beside it (`oCount`) and drawn there as a glyph.
	 *
	 * The tally is left off entirely when it is nought rather than drawn as a zero. A number that
	 * reads the same on every person in a library until somebody presses something is a number that
	 * says nothing, and the counts line is the one place on this page somebody scans rather than
	 * reads.
	 *
	 * Theirs and not the person's: it is a sum of what THIS account has counted, exactly as the
	 * heart and the stars beside it are this account's. */
	const countsLine = $derived.by(() => {
		if (!person) return '';
		return `On ${filesSized(person.asset_count, sizeOf(person))}`;
	});
</script>

<svelte:head><title>{person?.name ?? 'Person'}</title></svelte:head>

<!-- A link dropped anywhere on this page is fetched and filed under it, exactly as one
     dropped on its card on the wall is. Only while there IS one: a page still settling
     has no id to aim at. See `EntityDropZone`. -->
{#if person}
	<EntityDropZone kind="person" id={person.id} name={person.name} />
{/if}

<!--
	ONE frame and one scrolling region: the grid's.

	Three bands stacked in a flex column, each with a percentage cap and an `overflow-y` of its own,
	would be three scroll regions on one screen and would squeeze the wall to a sliver of the
	window. The band is handed to the grid instead, which draws it inside its own frame's header.
	The frame already knows how to hold furniture still above a body that scrolls, and the grid
	already measures that body, so there is nothing left for this page to arrange. The tall part
	of the band, the fields, folds into a dialog; see `EntityHeader`'s `foldFields`.
-->
{#if subject.settling || !cover.ready}
	<Skeleton lines={3} />
{:else if subject.unreadable}
	<Problem
		message="That person couldn't be loaded. They are still there — try again in a moment."
	/>
{:else if !person}
	<Empty scope="page" icon="person" title="That person isn't here"
		>They may have been deleted, or merged into another person.</Empty
	>
{:else}
	<!-- Their files are the `AssetGrid` Browse draws, asking `people=` as the Filters and the search
	     box do. The tabs sit in `beside`, inline after the title: a press changes what the word says
	     and what is under it, which reads as tabs rather than a filter bar off to one side. -->
	<!-- Whose page this is. A named snippet rather than a child of the media grid, because it
	     is drawn on EVERY tab: what is being shown OF somebody changes, and who they are does
	     not. -->
	{#snippet identity()}
		<EntityHeader
			bind:editing
			icon={iconOf('person')}
			kind="person"
			saveForm={editing ? RECORD_FORM : undefined}
			deleteWord="person"
			ondelete={session.isAdmin ? removePerson : undefined}
			mayEdit={session.isAdmin}
			name={person.name}
			onpicture={() => (pickingPicture = true)}
			oncover={async (assetId, atMs, more) => {
				subject.value = await people.setCover(personId, assetId, atMs, more);
			}}
			coverHref={`/people/${person.id}`}
			coverAssetId={person.cover_asset_id}
			coverUploadId={person.cover_upload_id}
			coverAtMs={person.cover_at_ms}
			coverFrame={person.cover_frame}
			coverTrackId={person.cover_track_id}
			creatorName={person.name}
			pmvCreator={person.pmv_creator}
			art={person.art}
			counts={countsLine}
			oCount={person.o_count}
			favorite={person.favorite}
			rating={person.rating}
			enrichedBy={sources.map((one) => ({ name: one.box_name, box: one.box_slug }))}
			{madeBy}
			{tags}
			onfavorite={(next) => heart(next)}
			onrate={(next) => rate(next)}
			onuntag={session.isAdmin ? (tagId) => untag(tagId) : undefined}
			ontag={session.isAdmin ? (tag) => addTag(tag) : undefined}
			{options}
			optionIds={[person.id]}
		>
			{#snippet summary()}
				<RecordSummary subject="person" label="About {person.name}" values={recordValues} />
			{/snippet}

			{#snippet facts()}
				<RecordFacts
					subject="person"
					label="Facts about {person.name}"
					values={recordValues}
					given={givenBy(sources)}
				/>
			{/snippet}

			{#snippet record()}
				<!-- Which entry in each stash-box this is: under More, with the rest of the record, and
				     first there, on the facts' columns. Read by every viewer; Remove is an admin's. See
				     `StashBoxIds`. -->
				<StashBoxIds
					subject="person"
					id={person.id}
					name={person.name}
					links={sources}
					mayForget={session.isAdmin}
					onforgot={reloadSources}
				/>
				<RecordView
					subject="person"
					label="More about {person.name}"
					values={recordValues}
					given={givenBy(sources)}
				/>
			{/snippet}

			{#snippet underCover()}
				{#if session.isAdmin}
					<div class="recognition">
						<!-- The three numbers first and the bar under them, which is the order somebody
						     reads this in: what is there, then what it comes to. The bar is a verdict on
						     the first of the three, so it cannot be read before the number it judges. -->
						<WaitingForYou
							personId={person.id}
							name={person.name}
							refresh={agreed}
							help={starterBoxes ? startersSay(starterBoxes) : undefined}
						/>
						<!-- Directly under the numbers it moves. Agreeing is the only thing that raises
						     what the bar measures, and the screen that does it is one press away. -->
						<RecognitionStrength
							personId={person.id}
							name={person.name}
							refresh={agreed}
							onstarters={(boxes) => (starterBoxes = boxes)}
						/>
						<!-- What a swap brought for them, held until it is added here. Adding moves the
						     bar above, so it is told to read again. -->
						<HeldFaces
							personId={person.id}
							name={person.name}
							onadded={(added) => {
								if (added > 0) agreed += 1;
							}}
						/>
					</div>
				{/if}
			{/snippet}
		</EntityHeader>
	{/snippet}

	{#snippet editing_form()}
		<RecordForm
			subject="person"
			formId={RECORD_FORM}
			label="Editing {person.name}"
			values={{ ...recordValues, links: links.map((one) => one.url) }}
			onsave={saveRecord}
			oncancel={() => (editing = false)}
		/>
	{/snippet}

	{#snippet tabStrip()}
		<Tabs
			tabs={tabsCarryingPicks(tabs, page.url)}
			current={showingHistory ? HISTORY : shown}
			label="What to show for {person.name}"
		/>
		<!-- The cards picked on the tabs, counted, and the press that filters the Files tab to
		     them. Draws nothing while nothing is picked. See `picks.ts`. -->
		<PickedFilter />
	{/snippet}

	{#if editing}
		<!--
			Editing takes the page.

			Not a band above the wall and not a dialog: one Save at the end of a whole record is a
			screen's worth of boxes, and both of those squeeze it into a strip with its own scrollbar.
			The page scrolls, the form is as tall as it needs to be, and the wall is not competing
			with it for the window.
		-->
		<PageFrame header={identity}>
			{#snippet children()}
				{@render editing_form()}
			{/snippet}
		</PageFrame>
	{:else if showingHistory}
		<!--
			The thread, in the frame every other screen uses. Not a wall: there is nothing to select,
			nothing to page and nothing to count, so the grid's furniture would all be furniture with
			no work behind it. The identity band stays, because this is a different view OF somebody
			rather than a different page.
		-->
		<!-- WITHOUT `measure`: that bounds the line AND centres it, and the thread belongs at
		     the page's own left edge where the tabs and the title are. `EntityHistory` bounds
		     itself on the same token. -->
		<!-- History draws the identity and the tab strip in the shape every other tab does
		     (`PageAbove`, then the strip as the heading row), so the strip stands at one height
		     on every tab. -->
		<PageFrame {crumbs}>
			{#snippet header()}
				<PageAbove>{@render identity()}</PageAbove>
				<PageHeader title="History" icon="history" level={2} titleHidden beside={tabStrip} />
			{/snippet}
			{#snippet children()}
				<EntityHistory subject="person" id={personId} name={person.name}>
					{#snippet waiting()}
						<!-- Where a stash-box disagrees with THIS record, at the top of the thread rather
						     than in the header. Draws nothing at all when nothing does, which is the
						     ordinary case. Admin-only: what a box wrote is shared vocabulary, like every
						     other stash-box control, and the strip answers None rather than a number for
						     anybody else, so the mark and the panel appear and disappear together. -->
						{#if session.isAdmin}
							<Disagreements
								subject="person"
								localId={person.id}
								onchange={(found, named) => counts.sawDisagreements(found, named)}
								onwritten={() => void afterLinked()}
							/>
						{/if}
					{/snippet}
				</EntityHistory>
			{/snippet}
		</PageFrame>
	{:else}
		<TabHold tab={shown} wallOf={wallOfTab}>
			{#snippet surface(tab, arrived)}
				{#if tab === 'files'}
					<AssetGrid
						oncount={arrived}
						query={{ people: person.id }}
						title="Files"
						titleLevel={2}
						beside={tabStrip}
						titleHidden
						above={identity}
						{crumbs}
						empty={emptyWallSays(
							'files',
							fileWords.asked,
							false,
							'Nothing is attributed to them yet. Drag clips onto them from the library.'
						)}
						pinnable
					>
						{#snippet tools()}
							<WallControls
								noun="file"
								plural="files"
								bind:term={fileWords.term}
								onsettled={(typed) => fileWords.write(typed)}
							/>
						{/snippet}
						{#snippet menuExtra(item)}
							{#if session.isAdmin}
								<ContextMenuItem
									label={person?.cover_asset_id === item.id
										? 'This is the cover'
										: 'Use as the cover'}
									icon="star"
									disabled={person?.cover_asset_id === item.id}
									onselect={() => void makeCover(item.id)}
								/>
							{/if}
						{/snippet}
					</AssetGrid>
				{:else}
					<!-- Every other tab is a wall of things, the same `RelatedWall` on each. -->
					<RelatedWall
						on="person"
						id={personId}
						named={person.name}
						showing={tab}
						title={tabs.find((one) => one.id === tab)?.label ?? ''}
						icon={tabs.find((one) => one.id === tab)?.icon ?? 'browse'}
						beside={tabStrip}
						titleHidden
						above={identity}
						{crumbs}
						oncount={(total, searched) => {
							if (!searched) counts.saw(tab, total);
							arrived();
						}}
					>
						{#snippet under(row, drawn)}
							{#if drawn === 'sites'}
								<UsernameLines
									usernames={usernamesHere.of(row.id)}
									onchanged={() => void usernamesHere.read({ personId })}
								/>
							{/if}
						{/snippet}
					</RelatedWall>
				{/if}
			{/snippet}
		</TabHold>
	{/if}
{/if}

<ConfirmDialog
	bind:open={confirmVault}
	title={person ? `Hide ${person.name}?` : 'Hide them?'}
	consequence={'They disappear from every list, count and search box, on your screens. ' +
		"Unlock Hidden with your PIN to bring them back. Their files aren't deleted or moved."}
	confirmLabel="Hide them"
	onconfirm={conceal}
/>

<ShareDialog bind:open={shareOpen} targets={shareTarget ? [shareTarget] : []} />
<VisibilityDialog bind:open={reachOpen} target={shareTarget} />

<!-- No button of its own: the row in the Options menu opens it. What stays here is the chooser and
     the warning, which is the whole of what this component is. -->
{#if person}
	<MergeEntities
		people={[person]}
		withButton={false}
		bind:open={mergeOpen}
		onmerged={(into) => goto(`/people/${into}`)}
	/>
{/if}

<!--
	Looking somebody up, and agreeing field by field to what comes back.

	Handed `saveRecord`, the SAME function the edit form is handed. What an import writes and what
	somebody types go through one path, so the two can never come to disagree about what saving a
	record does.
-->
{#if person}
	<LinkToStashBox
		bind:open={lookUpOpen}
		subject="person"
		id={person.id}
		name={person.name}
		values={recordValues}
		onlinked={afterLinked}
	/>

	<!-- The pencil's sheet. The same files the Files tab shows, and the same route the right-click
	     menu already writes through: one way to set a cover, reached from two places. -->
	<PickPicture
		bind:open={pickingPicture}
		name={person.name}
		query={{ people: person.id }}
		current={person.cover_asset_id}
		onpick={makeCover}
		onupload={uploadCover}
	/>
{/if}

<style>
	/* How well Sift knows them, and what is waiting to be answered. Not inside the record panel,
	 * which would make both of them things you had to open the record to find.
	 *
	 * Under the COVER (see `EntityHeader.underCover`), with no width of its own: the cover's
	 * column ends at the picture and is empty from there down, so this block costs the page
	 * nothing there, where under the name column it would push the bottom of the hero (and the
	 * tab strip with it) a long way down the one screen somebody opens in order to browse. It
	 * is about the person in the picture, and it is under the picture of them.
	 *
	 * What that buys is paid for in the words: the column is 200 pixels wide, so the three
	 * numbers are three short lines and their sentences are in tooltips. See `WaitingForYou`.
	 */
	.recognition {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
</style>
