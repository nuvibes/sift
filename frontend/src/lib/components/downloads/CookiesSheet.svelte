<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Cookies: one sheet, three doors.
	 *
	 * The head of the Downloads page, a blocked row on that page, and Settings > Sites and Tunnels all open
	 * this, so a download that stops for want of cookies is fixed where it stopped, with one answer
	 * to "is this file any good".
	 *
	 * The word is Cookies on every control, badge and sentence, never the vocabulary of accounts:
	 * nobody hands Sift an account here (no username, no password, nothing that could sign in
	 * anywhere on their behalf), only what their own browser already holds for one Site.
	 *
	 * The cookies themselves are never drawn. They go one way: typed or dropped, sent, sealed,
	 * never rendered back, never put in the DOM, never bound to a value. What a person gets instead
	 * is the read-back, a description of what the server understood, which is why Save waits for
	 * it.
	 */
	import { untrack } from 'svelte';
	import {
		Avatar,
		Badge,
		Button,
		ChooseFile,
		ConfirmDialog,
		Empty,
		Field,
		Modal,
		Note,
		Popover,
		Problem,
		Select,
		TextArea
	} from '$lib/components/common';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import { dragBeganInside } from '$lib/components/common/drag-origin.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/api/client';
	import { Connections, nameOf } from '$lib/settings-ui/connections-state.svelte';
	import SettingsSearch from '$lib/settings-ui/SettingsSearch.svelte';
	import { linkMarks } from '$lib/entity/entity-picture';
	import {
		NEED_BADGE,
		readBack,
		readIsGood,
		saidAbout,
		siteMatches,
		STATE_WORD,
		type CookieRow,
		type CookiesRead
	} from './cookies';

	interface Props {
		open?: boolean;
		/**
		 * The Site to open the add form on, or nothing for the whole list: a blocked download knows
		 * which Site stopped it, so somebody is not asked to find it again in a list of forty.
		 */
		site?: string | null;
		/**
		 * The download that is waiting on these cookies, if the sheet was opened from a row.
		 *
		 * It changes one thing: the button says so, and the save is followed by the retry. A person
		 * who came here from a stopped download wants that download, not a saved setting.
		 */
		retry?: string | null;
		/** Told the Site key after a successful save, so the page that opened this can refresh. */
		onsaved?: (site: string) => void;
	}

	let { open = $bindable(false), site = null, retry = null, onsaved }: Props = $props();

	const cookies = new Connections();

	/* Which Site the add form is for: a key, an empty string for "not chosen yet", or nothing at
	   all while the list is showing. Three states in one field rather than a boolean beside a
	   string, because "the form is open but for nobody" is a real state (it is what a sheet
	   opened from Settings with an Add press leaves behind) and two fields would let it be said
	   twice, differently. */
	let adding = $state<string | null>(null);
	let pasted = $state('');
	let read = $state<CookiesRead | null>(null);
	let saving = $state(false);
	let dropping = $state(false);
	let said = $state<string | null>(null);
	let forgetting = $state<CookieRow | null>(null);
	let forgetOpen = $state(false);

	/* The read-back is asked for after typing STOPS, not on every keystroke: a cookie file is
	   thousands of characters, arriving as one event in a paste and as a stream of them when
	   somebody types, and a request per character would be exactly that. */
	const SETTLE_MS = 400;
	let settling: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		if (!open) return;
		// Read `site` so re-opening on a different Site re-runs this; everything else is written
		// rather than read, and an effect that read its own writes would run itself again.
		const asked = site;
		untrack(() => {
			void cookies.load();
			adding = asked;
			pasted = '';
			read = null;
			said = null;
			searched = '';
		});
	});

	const name = $derived(adding ? nameOf(adding, cookies.sites) : '');

	/** What is typed in the search over the list. Emptied whenever the sheet opens. */
	let searched = $state('');

	/*
	 * Every row, with what the table draws beside the Site's name: the addresses that reach it (for
	 * its mark and for the search) and what it does without cookies.
	 *
	 * A saved row for a Site the supported list does not name (a Site Sift has since stopped
	 * knowing) has no addresses and no declaration, so it has no need badge and no sentence:
	 * its badge says what is saved, which is still true, rather than a need nobody declared.
	 */
	const shown = $derived(
		cookies.rows
			.map((entry) => {
				const known = cookies.sites.find((one) => one.key === entry.row.site);
				return {
					...entry,
					hosts: known?.hosts ?? [],
					need: known ? NEED_BADGE[known.cookies] : null,
					why: known?.cookies_why ?? null
				};
			})
			.filter((entry) => siteMatches(searched, entry.name, entry.hosts))
	);

	/*
	 * The pictures that may stand for a Site, best first: the icon pack's logo by the Site's own
	 * address, then the picture a download fetched for it. The same answer a Site's links are drawn
	 * with elsewhere (`linkMarks`), with `Avatar`'s letter tile behind both; the fetched picture
	 * alone exists only for Sites something was downloaded from.
	 */
	function marksFor(siteName: string, hosts: readonly string[]): string[] {
		return hosts.length > 0 ? linkMarks(`https://${hosts[0]}/`, siteName) : [];
	}

	/** Every supported Site, as the chooser's rows, for a sheet opened with nobody named. */
	const choices = $derived(
		[...cookies.sites]
			.map((one) => ({ value: one.key, label: one.name }))
			.sort((one, other) => one.label.localeCompare(other.label))
	);

	/** How many Sites have something saved that has stopped being any use, or is about to. */
	const wanting = $derived(
		cookies.items.filter((row) => row.state === 'expired' || row.state === 'ending_soon').length
	);

	/** The colour a state is said in. The words are `STATE_WORD`'s; only the tone is chosen here. */
	function tone(state: CookieRow['state']): 'done' | 'queued' | 'blocked' | 'failed' {
		if (state === 'saved') return 'done';
		if (state === 'none') return 'queued';
		return state === 'ending_soon' ? 'blocked' : 'failed';
	}

	/**
	 * Off the add step and back to the list, keeping the sheet open. What Cancel does there, and
	 * what Escape does there too: the sheet hands this to `Modal` as its way back while the add
	 * step is showing, and hands nothing on the list, where Escape closes the sheet.
	 */
	function back() {
		adding = null;
		pasted = '';
		read = null;
		cookies.saveError = undefined;
	}

	function openForm(key: string) {
		adding = key;
		pasted = '';
		read = null;
		said = null;
	}

	/** Ask what the server makes of what is in the box, without saving any of it. */
	async function reread() {
		const value = pasted.trim();
		if (!value || !adding) {
			read = null;
			return;
		}
		read = await cookies.preview(adding, value);
	}

	function typed() {
		read = null;
		if (settling) clearTimeout(settling);
		settling = setTimeout(() => void reread(), SETTLE_MS);
	}

	/** A dropped or chosen file read here rather than making somebody open it and copy what is in
	 *  it. The contents go the same way a pasted one does; nothing keeps the file. */
	async function take(file: File | null | undefined) {
		if (!file) return;
		pasted = await file.text();
		await reread();
	}

	function dragged(event: DragEvent) {
		// A drag that began on this very page is one of the app's own cards being moved, not a file
		// arriving. See `drag-origin` for why the payload cannot tell the two apart.
		if (dragBeganInside()) return;
		event.preventDefault();
		dropping = true;
	}

	async function dropped(event: DragEvent) {
		dropping = false;
		if (dragBeganInside()) return;
		event.preventDefault();
		await take(event.dataTransfer?.files?.[0]);
	}

	async function save() {
		if (!adding || !read || !readIsGood(read)) return;
		saving = true;
		try {
			const refusal = await cookies.save(adding, pasted.trim());
			if (refusal) return; // shown under the box
			const kept = adding;
			// The download that was waiting, before anything else: somebody who came here from a
			// stopped row came for that row. Told AFTER the save, so it cannot start again against
			// cookies that are only half written.
			if (retry) await api.post(`/downloads/${retry}/retry`);
			onsaved?.(kept);
			open = false;
		} finally {
			saving = false;
		}
	}

	async function check(row: CookieRow) {
		said = null;
		const answer = await cookies.check(row.id);
		said = answer.said;
	}

	function askToForget(row: CookieRow) {
		forgetting = row;
		forgetOpen = true;
	}

	async function forget() {
		if (forgetting) await cookies.remove(forgetting);
	}
</script>

<Modal
	bind:open
	title="Cookies"
	description="Some Sites show their files only to a browser they recognize. Cookies are what your browser holds for that Site once it does. Sift locks them with your password and never shows the cookies again."
	sheetClass="cookies-sheet"
	onback={adding === null ? undefined : back}
>
	{#snippet children()}
		<Problem message={cookies.problem} />

		{#if adding === null}
			{#if said}
				<!-- What the Site itself answered, read out where the press was. `status` rather than
				     `alert`: it is the answer to a question somebody asked, not an interruption. -->
				<p class="said" role="status">{said}</p>
			{/if}

			{#if cookies.loaded && cookies.rows.length === 0}
				<Empty scope="page" icon="lock" title="No Sites to show">
					Sift has no list of Sites yet, so there's nothing to add cookies for.
				</Empty>
			{:else}
				<!-- The search Settings uses, imported rather than drawn again: the same box, the same
				     cross, the same Escape. Forty Sites is a list somebody scrolls to find one in. -->
				<div class="find">
					<SettingsSearch bind:typed={searched} label="Search Sites" />
				</div>

				{#if shown.length === 0}
					<Empty icon="search" scope="block">No Site matches that search.</Empty>
				{/if}

				<!-- A table in all but element: five columns declared once on the list, and every row
				     laid on them, so the marks, the names, the badges and the verbs each stand in one
				     column however long a name or a sentence runs. -->
				<ul class="sites" aria-label="Sites and their cookies">
					{#each shown as entry (entry.row.site)}
						{@const marks = marksFor(entry.name, entry.hosts)}
						<li class="site">
							<!-- The Site's own mark, from Sift's pack or its cache rather than from the
							     Site: a page pointing a picture at a remote address fetches it from
							     whoever is looking, which tells that Site who is browsing. The letter
							     stands in where neither has one. -->
							<span class="mark">
								<Avatar
									src={marks[0] ?? null}
									instead={marks[1] ?? null}
									name={entry.name}
									mark
									bare
								/>
							</span>

							<span class="who">{entry.name}</span>

							<!-- With nothing saved, what the Site NEEDS: that is the question somebody
							     with nothing saved is asking. Once cookies are saved, how they stand. -->
							<span>
								{#if entry.row.state === 'none' && entry.need}
									<Badge state={entry.need.state} icon={entry.need.icon} label={entry.need.label} />
								{:else}
									<Badge state={tone(entry.row.state)} label={STATE_WORD[entry.row.state]} />
								{/if}
							</span>

							<span class="about">
								{#if entry.why}
									<span class="need">{entry.why}</span>
								{/if}
								{#if entry.row.state !== 'none'}
									<span class="small">{saidAbout(entry.row, Date.now() / 1000, entry.name)}</span>
								{/if}
							</span>

							<span class="verbs">
								{#if entry.row.state === 'none'}
									<Button
										tone="secondary"
										size="small"
										icon="cookie"
										onclick={() => openForm(entry.row.site)}
										aria-label="Add cookies for {entry.name}"
									>
										Add cookies
									</Button>
								{:else}
									<Button
										tone="ghost"
										size="small"
										disabled={cookies.busy}
										onclick={() => void check(entry.row)}
										aria-label="Check the {entry.name} cookies"
									>
										Check
									</Button>
									<Button
										tone="secondary"
										size="small"
										onclick={() => openForm(entry.row.site)}
										aria-label="Replace the {entry.name} cookies"
									>
										Replace
									</Button>
									<MenuButton label="More for {entry.name}">
										<ContextMenuItem
											label="Delete"
											icon="delete"
											destructive
											onselect={() => askToForget(entry.row)}
										/>
									</MenuButton>
								{/if}
							</span>
						</li>
					{/each}
				</ul>
			{/if}
		{:else}
			<SectionHeading level={3}>
				{adding === '' ? 'Add cookies' : `Add cookies for ${name}`}
			</SectionHeading>

			{#if adding === ''}
				<Field label="Site" help="Which Site these cookies are for.">
					{#snippet control({ id, describedBy })}
						<Select
							{id}
							{describedBy}
							value={adding ?? ''}
							options={choices}
							placeholder="Choose a Site"
							onValueChange={(value) => openForm(value)}
						/>
					{/snippet}
				</Field>
			{:else}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class="drop"
					class:over={dropping}
					ondragover={dragged}
					ondragleave={() => (dropping = false)}
					ondrop={(event) => void dropped(event)}
				>
					<Icon name="upload" size={28} />
					<p class="ask">Drop the cookie file here, or paste it</p>
					<p class="small">
						Exported with a browser extension such as Get cookies.txt LOCALLY, while you are signed
						in to {name}.
					</p>
					<ChooseFile
						size="small"
						icon="upload"
						accept=".txt,.json,text/plain,application/json"
						label="Choose a cookie file"
						onchoose={(file: File) => void take(file)}
					>
						Choose a file
					</ChooseFile>
				</div>

				<Field
					label="Paste the cookie file"
					hideLabel
					error={cookies.saveError}
					help="Everything inside the file, exactly as it was exported."
				>
					{#snippet control({ id, describedBy, invalid })}
						<!-- Write-only: never bound to anything stored, so saved cookies have no way back
						     to the screen. `autocomplete` off keeps a browser from offering to remember
						     what is typed here. -->
						<div class="paste">
							<TextArea
								{id}
								{invalid}
								{describedBy}
								bind:value={pasted}
								oninput={typed}
								autocomplete="off"
								spellcheck="false"
								placeholder={'# Netscape HTTP Cookie File \u2026'}
								rows={3}
							/>
						</div>
					{/snippet}
				</Field>

				{#if read}
					<p class="readback" role="status">
						<Badge
							state={readIsGood(read) ? 'done' : 'failed'}
							label={readIsGood(read) ? 'Read' : 'Not a cookie file'}
							wordless
						/>
						{readBack(read, name)}
					</p>
				{/if}

				<Popover label="How do I get this?" side="top" align="start">
					{#snippet trigger({ props })}
						<Button {...props} tone="ghost" size="small" type="button">How do I get this?</Button>
					{/snippet}
					<ol class="steps">
						<li>Open {name} in your browser and use it the way you normally would.</li>
						<li>
							Open a cookie export extension &mdash; Get cookies.txt LOCALLY is the one most people
							use &mdash; and export for that Site.
						</li>
						<li>Drop the file it saves here, or paste what is inside it.</li>
					</ol>
				</Popover>
			{/if}
		{/if}
	{/snippet}

	{#snippet footer({ Cancel })}
		{#if adding === null}
			{#if wanting > 0}
				<Note tone="caution">
					{wanting === 1 ? '1 Site needs' : `${counted(wanting)} Sites need`} your attention.
				</Note>
			{/if}
			<Cancel class="cancel">Done</Cancel>
		{:else}
			<!-- Back to the list, not out of the sheet. The sheet is a stack (the list, then the
			     add step on top) and Cancel takes the top one off; Done, on the list, is what
			     leaves. -->
			<Button tone="secondary" onclick={back}>Cancel</Button>
			<Button
				tone="primary"
				icon="save"
				disabled={saving || cookies.busy || read === null || !readIsGood(read)}
				onclick={() => void save()}
			>
				{retry ? 'Save and try the download again' : 'Save cookies'}
			</Button>
		{/if}
	{/snippet}
</Modal>

<ConfirmDialog
	bind:open={forgetOpen}
	title={forgetting
		? `Delete the ${nameOf(forgetting.site, cookies.sites)} cookies?`
		: 'Delete these cookies?'}
	consequence="Downloads that need these cookies wait until you add new ones. Nothing already downloaded is affected."
	confirmLabel="Delete"
	destructive={true}
	onconfirm={() => void forget()}
/>

<style>
	/* Wide enough for the table. A sheet at the default 420 pixels would hold one Site per two
	   lines and no room for a sentence at all. The width only; `.sheet` clamps it to the window. */
	:global(.cookies-sheet) {
		--sheet-inline: 52rem;
	}

	.find {
		margin-block-end: var(--space-3);
	}

	/*
	 * The columns, declared once, and every row laid on them by `subgrid`: mark, name, state,
	 * sentence, verbs. The name and state columns are as wide as the widest in the whole list, the
	 * sentence takes what is left and wraps inside it, and the verbs column is the width of the
	 * widest set of verbs whether or not a row has them all, so badges and verbs line up down the
	 * list.
	 */
	.sites {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: auto max-content max-content minmax(0, 1fr) max-content;
		column-gap: var(--space-3);
	}

	.site {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		align-items: center;
		padding-block: var(--space-2);
		border-block-end: 1px solid var(--sift-line);
	}

	.site:last-child {
		border-block-end: 0;
	}

	/* The Site's mark at the size the download queue draws it, and drawn the same way: BARE, with
	   nothing behind it and no corner taken off, so one Site looks like itself on both screens.
	   The picture and the letter are `Avatar`'s; the corner here is the letter's tile alone. */
	.mark {
		display: block;
		inline-size: var(--space-5);
		block-size: var(--space-5);
		border-radius: var(--radius-sm);
	}

	.who {
		color: var(--sift-ink);
		font: var(--text-body);
		white-space: nowrap;
	}

	.about {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.need {
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* Every small grey line on this sheet: what a row's cookies do next, and the sentence under
	   the drop target. One rule rather than three names for one voice. */
	.small {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/*
	 * On the right, where anything pressable sits, and at the column's end, so a lone Add cookies
	 * lines up with the Replace above it rather than with the Check beside that.
	 */
	.verbs {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
	}

	.said,
	.readback {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* The drop target says what it is before anything is dragged at all. A dashed edge that only
	   appears mid-drag is a target nobody knows is there. */
	.drop {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-5);
		border: 1px dashed var(--sift-line);
		border-radius: var(--radius-md);
		color: var(--sift-ink-3);
		text-align: center;
		transition: background-color var(--dur-instant) var(--ease);
	}

	.drop.over {
		background: var(--sift-surface-3);
	}

	.ask {
		margin: 0;
		color: var(--sift-ink);
		font: var(--text-body);
	}

	.paste {
		margin-block-start: var(--space-3);
	}

	.paste :global(.text-area) {
		font: var(--text-body-sm);
	}

	.steps {
		margin: 0;
		padding-inline-start: var(--space-5);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}
</style>
