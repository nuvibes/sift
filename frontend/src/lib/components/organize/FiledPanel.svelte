<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * What a pass filed under somebody without asking: the record beside the folder questions.
	 *
	 * A folder whose faces were already named is filed under that person and nobody is asked, which
	 * is the point of not asking twice. A flat list, one line per folder with the person's name
	 * repeated, or headings with rows under them, would read on a wall of forty people as forty
	 * labels and no shape. So it is a wall of cards, one per person (their picture, their name, the
	 * folders filed under them as rows inside) on the same `DecisionCard` the questions
	 * beside it are drawn on, so the record and the questions read as one screen.
	 *
	 * Each row takes its folder back with one press, Undo, the word the neighbouring tab's rows
	 * use for the same act: the person comes off the files the folder pass put them on there, the folder is
	 * no longer theirs, and Sift never adds it to them again without asking. One decision, and the
	 * toast carries its Undo. It works for every folder listed, however long ago it was added.
	 */
	import { coverUrl } from '$lib/entity/art';
	import {
		Avatar,
		Button,
		DataRow,
		DataRows,
		Empty,
		Problem,
		Skeleton
	} from '$lib/components/common';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import { libraryChanges } from '$lib/library/changes.svelte';
	import { decided } from '$lib/organize/organize.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { filedWithoutAsking, takeBackFolder, type Filed } from '$lib/search/suggestions.svelte';

	let filed = $state<Filed[]>([]);
	/** The row whose Undo is on its way, by folder and person. One at a time. */
	let taking = $state<string | null>(null);
	let loading = $state(true);
	let failed = $state(false);

	async function load() {
		loading = true;
		failed = false;
		try {
			filed = (await filedWithoutAsking()) as Filed[];
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	/* And again whenever the library's shape changes underneath: a folder filed while this screen
	   is open is a row here, and a share moving changes which rows this account may see. */
	$effect(() => {
		void libraryChanges.generation;
		void load();
	});

	/**
	 * How many folders a card lists before the rest go behind a press.
	 *
	 * So every card is the same height and the wall has a line to scan down. Four rows are enough
	 * to see what was filed, which is all this tab is for (nothing here is a question), and the
	 * rest is one press away, as in `FilenamesPanel`.
	 */
	const FOLDED_OVER = 4;

	/** Which cards somebody opened, by person id. Not remembered: this is one screen's
	 *  arrangement while it is being read, not a preference. */
	let unfolded = $state<Record<string, boolean>>({});

	function listOpen(id: string, folders: number): boolean {
		return unfolded[id] ?? folders <= FOLDED_OVER;
	}

	function toggleList(id: string, folders: number): void {
		unfolded[id] = !listOpen(id, folders);
	}

	/*
	 * Where a row's count leads: the person's own page, filtered to the files this pass filed
	 * there.
	 *
	 * Two ordinary query-language filters on the person's ordinary address: the Files tab's grid
	 * reads every query field in its address on top of the person it is (see `FILTER_PARAMS` in
	 * `AssetGrid`), and the bar draws each as a chip somebody can read and take off.
	 *
	 * - `in`: the folder, by the same words the row draws (`path`, or the folder's own name for a
	 *   library folder itself, whose path is empty). Search resolves it by path or name, subtree
	 *   included, the same subtree the server's count walks. By words, not id, so the chip reads as
	 *   the folder; a name two libraries share filters to both, the wider and safe direction.
	 * - `enriched=folder`: what a folder-name reading wrote. The count is of files carrying this
	 *   person by a pass's doing (`filed_counts`), and the pass writes `source = 'folder'`; a file
	 *   the person was put on by hand is not one this pass filed.
	 *
	 * Together the two select exactly the row's count of files under the person.
	 */
	function filedHref(personId: string, one: Filed): string {
		const narrowed = new URLSearchParams({ in: one.path || one.folder, enriched: 'folder' });
		return `/people/${encodeURIComponent(personId)}?${narrowed.toString()}`;
	}

	/*
	 * Undo on a row: one decision, said in the toast with its own Undo. The row leaves on the library's
	 * bell, which the press rings on the server and `decided` rings here.
	 */
	async function takeBack(one: Filed, person: string): Promise<void> {
		const key = `${one.folder_id}:${one.person_id}`;
		if (taking) return;
		taking = key;
		try {
			const taken = await takeBackFolder(one.folder_id, one.person_id);
			if (!taken.decision_id) {
				toasts.show('There was nothing left to undo');
			} else {
				const who = thing('person', one.person_id, person);
				decided(
					taken.files > 0
						? [`Removed ${files(taken.files)} from `, who]
						: ['Removed ', thing('folder', one.folder_id, one.path || one.folder), ' from ', who],
					taken.decision_id,
					{ after: load }
				);
			}
			await load();
		} catch {
			toasts.show("That folder couldn't be undone", { tone: 'error' });
		} finally {
			taking = null;
		}
	}

	/** A count of files, grouped the way the reader's language groups a long number. */
	function files(count: number): string {
		return count === 1 ? '1 file' : `${count.toLocaleString()} files`;
	}

	/** The record by person: everybody who was filed for, with their folders under them. */
	const byPerson = $derived.by(() => {
		/* `cover` is the FIRST row for this person, kept so the portrait can be addressed with its
		   token: every row for one person carries the same four cover fields, so which one is
		   held does not matter, only that one is. */
		const held = new Map<string, { person: string; folders: Filed[]; cover: Filed }>();
		for (const one of filed) {
			const found = held.get(one.person_id);
			if (found) found.folders.push(one);
			else held.set(one.person_id, { person: one.person, folders: [one], cover: one });
		}
		return [...held.entries()].map(([id, group]) => ({
			id,
			...group,
			files: group.folders.reduce((sum, one) => sum + one.files, 0)
		}));
	});
</script>

<section>
	{#if loading && filed.length === 0}
		<Skeleton lines={3} />
	{:else if failed}
		<Problem
			message="Folders added without asking couldn't be loaded. Refresh the page to try again."
		/>
	{:else if filed.length === 0}
		<Empty scope="page" icon="folder_supervised" title="Nothing added without asking">
			When a folder's faces or name already match a person in your library, Sift adds its files to
			that person and lists the folder here.
		</Empty>
	{:else}
		<ul class="people">
			{#each byPerson as person (person.id)}
				<li>
					<DecisionCard opens="/people/{person.id}">
						<header class="who">
							<a class="portrait" href="/people/{person.id}" aria-label="Open {person.person}">
								<!-- The token, so this portrait may be kept for a week. The address is the
								     entity's own cover, which resolves whichever picture was chosen;
								     without the token the server refuses the week-long promise and the
								     picture is re-checked on every visit. See `coverUrl`. -->
								<Avatar
									src={coverUrl(`/people/${person.id}`, person.cover.art, {
										assetId: person.cover.cover_asset_id,
										atMs: person.cover.cover_at_ms,
										uploadId: person.cover.cover_upload_id,
										frame: person.cover.cover_frame
									})}
									name={person.person}
									shape="face"
									decorative
								/>
							</a>
							<div class="named">
								<a class="name" href="/people/{person.id}">{person.person}</a>
								<p class="tally data">
									{person.folders.length === 1
										? '1 folder'
										: `${counted(person.folders.length)} folders`}
									&middot;
									{files(person.files)}
								</p>
							</div>
						</header>
						<DataRows
							items={listOpen(person.id, person.folders.length)
								? person.folders
								: person.folders.slice(0, FOLDED_OVER)}
							key={(one: Filed) => one.path || one.folder}
							label="Folders added to {person.person}"
						>
							{#snippet row(one: Filed)}
								<DataRow compact>
									<span class="path">{one.path || one.folder}</span>
									{#snippet trailing()}
										<!-- The count OPENS those files, on the person's page. See `filedHref`. -->
										<a
											class="data count"
											href={filedHref(person.id, one)}
											aria-label="Open the {files(one.files)} added from {one.path ||
												one.folder} on {person.person}'s page"
										>
											{files(one.files)}
										</a>
										<Button
											icon="undo"
											tone="ghost"
											size="small"
											busy={taking === `${one.folder_id}:${one.person_id}`}
											disabled={taking !== null}
											aria-label={`Undo adding ${one.path || one.folder} to ${person.person}`}
											onclick={() => void takeBack(one, person.person)}>Undo</Button
										>
									{/snippet}
								</DataRow>
							{/snippet}
						</DataRows>
						{#if person.folders.length > FOLDED_OVER}
							<!-- On the right, where an action goes on a row or a card, and at the
							     foot whatever is above it, so a row of cards keeps its control on
							     one line. -->
							<div class="fold">
								<Button
									tone="link"
									size="small"
									aria-expanded={listOpen(person.id, person.folders.length)}
									onclick={() => toggleList(person.id, person.folders.length)}
								>
									{listOpen(person.id, person.folders.length)
										? 'Hide'
										: `Show all ${counted(person.folders.length)} folders`}
								</Button>
							</div>
						{/if}
					</DecisionCard>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	/* The same wall the questions beside this are laid on: cards that fill the row, the last of
	   them not stretched to the width of a screen. */
	.people {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(22rem, 100%), 1fr));
		gap: var(--space-3);
	}

	/*
	 * Each card at its own height, with no floor, as every Organize card is: a floor would leave the
	 * short ones a band of empty ground. The grid gives the cards of one row one height.
	 */
	.people > li {
		display: grid;
	}

	.who {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		min-inline-size: 0;
	}

	/* On the card's button side (`DecisionCard`), and at the foot whatever is above it: the same
	   rule the folder questions' answers follow, so a row of cards keeps its controls on one line. */
	.fold {
		margin-block-start: auto;
		display: flex;
		justify-content: var(--card-actions-justify, flex-end);
	}

	.portrait {
		flex: none;
		inline-size: 3rem;
		block-size: 3rem;
		border-radius: var(--radius-md);
		overflow: hidden;
	}

	.named {
		min-inline-size: 0;
	}

	.name {
		font: var(--text-body);
		color: var(--sift-accent-text);
		text-decoration: none;
		overflow-wrap: anywhere;
	}

	.name:hover {
		text-decoration: underline;
	}

	.tally {
		margin: var(--space-1) 0 0;
		color: var(--sift-ink-3);
	}

	.data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	.path {
		overflow-wrap: anywhere;
	}

	/* A link in the accent, like the name above it, so it reads as a way in rather than a figure. */
	.count {
		color: var(--sift-accent-text);
		text-decoration: none;
		white-space: nowrap;
	}

	.count:hover {
		text-decoration: underline;
	}
</style>
