<script lang="ts">
	/*
	 * A person's usernames on one Site, drawn under a card: the whole of where a username is shown.
	 *
	 * A username is a fact about a person on a Site, so it is drawn where both are on screen: under
	 * the Site's card on the person's Sites tab, and under the person's card on the Site's People
	 * tab, by this one component. It has no page of its own; the row still files downloads under a
	 * Site, carries sharing through to files, and holds the Site's ID for the username.
	 *
	 * A line reads "<username> posted <7 files> &middot; ID 12345678". The count is the files
	 * posted under this username and says so, because the card above counts something else (that
	 * person's files from the Site however they arrived), and two bare numbers one above the other
	 * would read as one being wrong. The count is a link to the Files wall filtered to the username
	 * (`?username=`, drawn there as a chip), exactly the set the server filed under this row. The
	 * Site's number for the username is its "ID", one word everywhere. Not "User ID": a User in
	 * Sift signs in.
	 *
	 * Pressing the username opens a sheet, holding everything the server lets an admin change about
	 * a username: who it belongs to (the same picker "Who is this?" opens on Usernames Waiting,
	 * `people-picker.ts`), its ID (typed where none is known; corrected behind a confirmation
	 * saying what changes, recorded as typed), and the name the Site shows beside it and its page
	 * there. The username itself is read-only and the sheet says why: the files were filed under
	 * it, and the ID is how a rename is recognized.
	 *
	 * An ID another username on the Site already has is refused by the server with a sentence
	 * naming that username ("These may be the same person, renamed"), shown as it is
	 * (`ApiError.detail`).
	 *
	 * Only usernames that hold something reach this component (see `holdsSomething`): the profile
	 * links a stash-box writes are the record's Links, not usernames.
	 */
	import { goto } from '$app/navigation';
	import { ApiError } from '$lib/api/client';
	import {
		Button,
		ConfirmDialog,
		Field,
		MenuButton,
		Modal,
		PickMenu,
		TextInput
	} from '$lib/components/common';
	import { askPeople, makePerson } from '$lib/people/people-picker';
	import { usernames as writes, type Username } from '$lib/people/usernames.svelte';
	import { usernameArt } from '$lib/entity/creator-art.svelte';
	import { sizeOf } from '$lib/entity/entity-counts';
	import { size } from '$lib/library/facts';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	interface Props {
		/** The usernames to draw, already filtered to one Site and one person by the caller. */
		usernames: readonly Username[];
		/** Told after a write, so the caller reads its usernames again. */
		onchanged?: () => void;
	}

	let { usernames, onchanged }: Props = $props();

	/* The pictures that failed to load, so a line whose picture has gone draws the handle alone. */
	let unseen = $state(new Set<string>());

	/** The username whose sheet is open, or null. */
	let open = $state<Username | null>(null);

	/* The ID: typed where none is known, or after "Edit ID" on one that is. */
	let editingId = $state(false);
	let typedId = $state('');
	let idProblem = $state<string | null>(null);
	let confirmingId = $state(false);
	let savingId = $state(false);

	/* The name the Site shows, and the username's page there. */
	let typedName = $state('');
	let typedPage = $state('');
	let detailsProblem = $state<string | null>(null);
	let savingDetails = $state(false);

	/* Who it belongs to. */
	let choosing = $state(false);
	let parting = $state(false);

	/** What the server accepts as an ID: digits, the lengths the Sites actually use with room. */
	const ID_SHAPE = /^\d{1,20}$/;

	/**
	 * What a username is called on screen. A download that could not read who posted something
	 * files it under an empty username, and a line reading nothing looks broken.
	 *
	 * The username first, and the display name only where there is no username, so the line, the
	 * panel's title and the Browse chip the count opens all show the same username. The display
	 * name is a field of the record ("the name the Site shows beside the username") and is shown as
	 * one, in its own box.
	 */
	function shownAs(one: Username): string {
		return one.username.trim() || one.display_name?.trim() || 'No username recorded';
	}

	function filesText(count: number): string {
		return count === 1 ? '1 file' : `${count.toLocaleString()} files`;
	}

	/** The Site by name in a sentence, or "the Site" for a username whose Site row has gone. */
	function siteOf(one: Username): string {
		return one.site_name ?? 'the Site';
	}

	/** What the ID is, and why Sift wants it: said under the field it is typed into. */
	function idHelp(one: Username): string {
		return (
			`The number ${siteOf(one)} gives this username. It stays the same if the username is ` +
			'renamed, so Sift uses it to keep new downloads with the right person.'
		);
	}

	/** "Instagram ID", or plain "ID" for a username whose Site row has gone. */
	function idLabel(one: Username): string {
		return one.site_name ? `${one.site_name} ID` : 'ID';
	}

	/** The sheet's one-line summary: what this is, and how much was posted under it. */
	function aboutOf(one: Username): string {
		const what = one.site_name ? `A username on ${one.site_name}.` : 'A username.';
		const posted =
			one.asset_count === 1
				? '1 file was posted under it'
				: `${one.asset_count.toLocaleString()} files were posted under it`;
		// How much room those files take, off the same row as their count; said only where known.
		const bytes = one.asset_count > 0 ? size(sizeOf(one)) : null;
		return `${what} ${posted}${bytes ? `, ${bytes} in all` : ''}.`;
	}

	/** The files posted under a username: the Files wall filtered to it, which is exactly the set the
	 *  server filed under this row. See `browse/router.py`'s `username` parameter. */
	function filesOf(one: Username): string {
		return `/browse?username=${encodeURIComponent(one.id)}`;
	}

	function show(one: Username) {
		open = one;
		editingId = false;
		typedId = '';
		idProblem = null;
		typedName = one.display_name ?? '';
		typedPage = one.url ?? '';
		detailsProblem = null;
	}

	/** A write landed: the sheet shows the row as the server now holds it, and the caller re-reads. */
	function landed(now: Username) {
		open = now;
		onchanged?.();
	}

	/** The server's own sentence where it wrote one for people (a 409), else ours. */
	function refusal(error: unknown, ours: string): string {
		return error instanceof ApiError && error.status === 409 && error.detail ? error.detail : ours;
	}

	function editId() {
		if (!open) return;
		typedId = open.number ?? '';
		idProblem = null;
		editingId = true;
	}

	/* Filling a blank is saved at once; REPLACING an ID is asked first, because the ID is what keeps
	   new downloads with the right person. See the confirmation's own words. Typing the ID it
	   already has is no change and asks nothing. */
	function askToSaveId() {
		const one = open;
		const number = typedId.trim();
		if (!one || !number) return;
		if (!ID_SHAPE.test(number)) {
			idProblem = 'An ID is digits only.';
			return;
		}
		idProblem = null;
		if (one.number && number !== one.number) {
			confirmingId = true;
			return;
		}
		void saveId(false);
	}

	async function saveId(replacing: boolean) {
		confirmingId = false;
		const one = open;
		const number = typedId.trim();
		if (!one || !number || savingId) return;
		savingId = true;
		try {
			const saved = await writes.save(one.id, { number, replaceNumber: replacing });
			editingId = false;
			typedId = '';
			landed(saved);
			toasts.show('Saved', { tone: 'success' });
		} catch (error) {
			idProblem = refusal(error, "That ID couldn't be saved.");
		} finally {
			savingId = false;
		}
	}

	async function saveDetails() {
		const one = open;
		if (!one || savingDetails) return;
		savingDetails = true;
		detailsProblem = null;
		try {
			const saved = await writes.save(one.id, {
				display_name: typedName.trim() || null,
				url: typedPage.trim() || null
			});
			landed(saved);
			toasts.show('Saved', { tone: 'success' });
		} catch (error) {
			detailsProblem =
				error instanceof ApiError && error.status === 422
					? 'A page link starts with http:// or https://.'
					: "Those couldn't be saved.";
		} finally {
			savingDetails = false;
		}
	}

	/* The join the queue's "Who is this?" makes, from the same picker. The answer carries no ID, so
	   the sheet keeps the one it already shows and takes only who it now belongs to. */
	async function belongsTo(personId: string) {
		const one = open;
		if (!one || choosing) return;
		choosing = true;
		try {
			const joined = await writes.attach(one.id, { personId });
			landed({ ...one, person_id: joined.person_id, person_name: joined.person_name });
			toasts.show(
				[
					thing('username', one.id, shownAs(one)),
					' is ',
					joined.person_id ? thing('person', joined.person_id, joined.person_name ?? '') : 'them'
				],
				{ tone: 'success' }
			);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		} finally {
			choosing = false;
		}
	}

	async function takeOff() {
		const one = open;
		if (!one || parting) return;
		parting = true;
		try {
			await writes.detach(one.id);
			open = null;
			toasts.show(
				[
					'Removed from ',
					one.person_id ? thing('person', one.person_id, one.person_name ?? '') : 'them'
				],
				{ tone: 'success' }
			);
			onchanged?.();
		} catch {
			toasts.show("That couldn't be undone", { tone: 'error' });
		} finally {
			parting = false;
		}
	}
</script>

{#if usernames.length > 0}
	<ul class="usernames" aria-label="Usernames">
		{#each usernames as one (one.id)}
			<!-- The picture this username is shown with on its Site: a mark, whole, at the size of
			     a Site's mark on a card. Decorative, because the handle beside it names it. -->
			{@const art = unseen.has(one.id) ? null : usernameArt(one)}
			<li>
				{#if art}
					<img
						class="mark"
						src={art}
						alt=""
						loading="lazy"
						decoding="async"
						onerror={() => (unseen = new Set([...unseen, one.id]))}
					/>
				{/if}
				<Button
					tone="link"
					size="small"
					aria-label="{shownAs(one)}, username details"
					onclick={() => show(one)}>{shownAs(one)}</Button
				>
				<span class="facts">
					{#if one.asset_count > 0}
						posted <a
							class="files"
							href={filesOf(one)}
							aria-label="{filesText(one.asset_count)} posted under {shownAs(one)}"
							>{filesText(one.asset_count)}</a
						>
					{/if}
					{#if one.number}
						{#if one.asset_count > 0}&middot;{/if}
						<span class="id">ID {one.number}</span>
					{/if}
				</span>
			</li>
		{/each}
	</ul>
{/if}

<Modal
	open={open !== null}
	onOpenChange={(next) => {
		if (!next) open = null;
	}}
	title={open ? `${shownAs(open)}${open.site_name ? ` on ${open.site_name}` : ''}` : 'Username'}
	description={open ? aboutOf(open) : ''}
>
	{#snippet children()}
		{#if open}
			<section class="line" aria-label="Who it belongs to">
				<p class="about">
					{open.person_name ? `Belongs to ${open.person_name}` : 'Belongs to nobody yet'}
				</p>
				{#if session.isAdmin}
					<div class="line-actions">
						<!-- The picker "Who is this?" opens on Usernames Waiting: one act, one picker. -->
						<MenuButton label="Who is {shownAs(open)}?" disabled={choosing} scrolls={false}>
							{#snippet trigger({ props })}
								<Button {...props} tone="secondary" size="small" busy={choosing} type="button"
									>{open?.person_id ? 'Choose another person' : 'Choose a person'}</Button
								>
							{/snippet}
							<PickMenu
								label="Add as person"
								icon="person_add"
								kind="person"
								plural="people"
								inline
								ask={askPeople}
								onpick={(choice) => void belongsTo(choice.id)}
								oncreate={makePerson}
							/>
						</MenuButton>
						{#if open.person_id}
							<Button
								tone="ghost"
								size="small"
								icon="close"
								busy={parting}
								onclick={() => void takeOff()}>Remove from {open.person_name ?? 'them'}</Button
							>
						{/if}
					</div>
				{/if}
			</section>

			<section class="line" aria-label={idLabel(open)}>
				{#if open.number && !editingId}
					<!-- The ID, and where it came from in the server's own words. -->
					<p class="about">{idLabel(open)} {open.number}</p>
					{#if open.number_said}<p class="said">{open.number_said}</p>{/if}
					{#if session.isAdmin}
						<div class="line-actions">
							<Button tone="secondary" size="small" icon="edit" onclick={editId}>Edit ID</Button>
						</div>
					{/if}
				{:else if session.isAdmin}
					<form
						class="field"
						onsubmit={(event) => {
							event.preventDefault();
							askToSaveId();
						}}
					>
						<Field label={idLabel(open)} help={idHelp(open)} error={idProblem ?? undefined}>
							{#snippet control({ id, describedBy, invalid })}
								<TextInput
									{id}
									{describedBy}
									{invalid}
									type="text"
									inputmode="numeric"
									autocomplete="off"
									bind:value={typedId}
								/>
							{/snippet}
						</Field>
						<div class="line-actions">
							{#if editingId}
								<Button tone="ghost" size="small" onclick={() => (editingId = false)}>Cancel</Button
								>
							{/if}
							<Button
								type="submit"
								tone="secondary"
								size="small"
								icon="save"
								busy={savingId}
								disabled={!typedId.trim()}>Save ID</Button
							>
						</div>
					</form>
				{/if}
			</section>

			{#if session.isAdmin}
				<form
					class="line field"
					aria-label="How {siteOf(open)} shows it"
					onsubmit={(event) => {
						event.preventDefault();
						void saveDetails();
					}}
				>
					<Field label="Display name" help="The name {siteOf(open)} shows beside the username.">
						{#snippet control({ id, describedBy, invalid })}
							<TextInput {id} {describedBy} {invalid} autocomplete="off" bind:value={typedName} />
						{/snippet}
					</Field>
					<Field
						label="Page link"
						help="This username's page on {siteOf(open)}."
						error={detailsProblem ?? undefined}
					>
						{#snippet control({ id, describedBy, invalid })}
							<TextInput
								{id}
								{describedBy}
								{invalid}
								type="url"
								autocomplete="off"
								bind:value={typedPage}
							/>
						{/snippet}
					</Field>
					<div class="line-actions">
						<Button type="submit" tone="secondary" size="small" icon="save" busy={savingDetails}
							>Save</Button
						>
					</div>
				</form>
			{/if}

			<p class="said">
				The username itself can't be edited: the files were filed under it, and Sift recognizes a
				rename by the ID.
			</p>

			<!-- Only where there is something to show. A username with nothing posted under it (one a
			     stash-box answered with, one typed in) would draw "Show the 0 files": a press that
			     opens an empty wall. The line above already says nothing was posted under it. -->
			{#if open.asset_count > 0}
				<div class="actions">
					<Button
						tone="primary"
						onclick={() => {
							const where = open ? filesOf(open) : null;
							open = null;
							if (where) void goto(where);
						}}
						>{open.asset_count === 1
							? 'Show the file'
							: `Show the ${open.asset_count.toLocaleString()} files`}</Button
					>
				</div>
			{/if}
		{/if}
	{/snippet}
</Modal>

<!-- Asked before an ID is REPLACED, never before a blank is filled: the ID is what keeps new
     downloads with the right person, so changing it changes where future files go, and only
     those. Its own dialog over the sheet, so the question cannot be skimmed past inside a form. -->
<ConfirmDialog
	bind:open={confirmingId}
	title={open ? `Change the ${idLabel(open)}?` : 'Change the ID?'}
	consequence="Future downloads and file names will match the new ID. Files already filed stay where they are."
	confirmLabel="Save ID"
	destructive={false}
	onconfirm={() => void saveId(true)}
/>

<style>
	/* Under a card's facts, with a hairline above: a second thing about the card rather than more
	   of its facts. Owned here, because the card draws what it is handed unwrapped. */
	.usernames {
		list-style: none;
		margin: var(--space-1) 0 0;
		padding: var(--space-2) 0 0;
		border-block-start: 1px solid var(--sift-line);
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.usernames li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		column-gap: var(--space-2);
		min-inline-size: 0;
	}

	/*
	 * The handle, cut with an ellipsis where the card is narrower than it, as the card's own name
	 * above it is. A handle is one word, so the link tone's ordinary wrapping has nowhere to break
	 * it: a long one would run past the card's inner edge and be cut by the card's own border (at
	 * a phone's width, three cards to a row on a Site's People tab).
	 * Pressing it opens the whole handle in its sheet, so nothing is lost by the cut.
	 * `:global`, because the button is `Button`'s element; bounded by this list's own rows.
	 */
	.usernames li > :global(.btn) {
		min-inline-size: 0;
		max-inline-size: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* A creator's picture is a mark: contained, never cropped, at a Site mark's size. */
	.mark {
		inline-size: var(--space-5);
		block-size: var(--space-5);
		border-radius: var(--radius-sm);
		object-fit: contain;
		align-self: center;
	}

	.facts {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: baseline;
		column-gap: var(--space-1);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-3);
	}

	.facts .files,
	.facts .id {
		white-space: nowrap;
	}

	.line {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0 0 var(--space-4);
	}

	.line-actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	.about {
		margin: 0;
		font: var(--text-body);
	}

	.said {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		align-items: stretch;
	}

	.actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
		margin-block-start: var(--space-4);
	}
</style>
