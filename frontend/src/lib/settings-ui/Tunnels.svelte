<script lang="ts">
	import { onMount } from 'svelte';
	import {
		Badge,
		Button,
		ChooseFile,
		ConfirmDialog,
		DataRow,
		DataRows,
		FormCard,
		MoreAbout,
		Note,
		Problem,
		SectionHeading,
		Select,
		Switch,
		TextArea,
		TextInput,
		Tooltip
	} from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { session } from '$lib/shell/session.svelte';
	import ExitAddress from './ExitAddress.svelte';
	import FieldRow from './FieldRow.svelte';
	import { Tunnels, addressOf, addressPhrase, type Tunnel } from './tunnels-state.svelte';
	import { COPY as PANE } from './Sites.search';

	const COPY = PANE.tunnels;

	/* Ways out: the tunnels a download can take, and which sites take which. */

	let { tunnels = new Tunnels() }: { tunnels?: Tunnels } = $props();
	// svelte-ignore state_referenced_locally
	tunnels.follow();

	/* The tunnel a press is about to delete, and the question: every destroying act asks first. */
	let deleting = $state<Tunnel | null>(null);
	let deleteOpen = $state(false);
	let name = $state('');
	let config = $state('');
	let importing = $state(false);
	let dragging = $state(false);

	onMount(() => {
		void tunnels.load();
		// A tunnel takes seconds to hand-shake, and after an unlock the server starts one with no
		// request from here to answer.
		return tunnels.watch();
	});

	function label(tunnel: Tunnel): string {
		// Locked first, because it explains every other state below it.
		if (session.secretsLocked) return COPY.states.locked;
		if (tunnel.draining) return COPY.states.finishing;
		if (tunnel.up) return COPY.states.connected;
		if (tunnel.enabled) return COPY.states.notConnecting;
		return COPY.states.off;
	}

	/* Amber for switched on and not connecting, and it is the same amber a download waiting for
	   cookies gets: both are asking for a person and neither is a failure. */
	/* The mark, where the state's own is wrong for what this row means. */
	function mark(tunnel: Tunnel): 'lock' | undefined {
		return session.secretsLocked ? 'lock' : undefined;
	}

	/* Whether a swap can be hosted on this tunnel, as the last try found. */
	function hosting(tunnel: Tunnel): string {
		if (tunnel.can_host === true) return COPY.hosting.can;
		if (tunnel.can_host === false) return COPY.hosting.cannot;
		return COPY.hosting.untried;
	}

	/* A tunnel that is simply switched off wears no pill: the switch beside it already says Off,
	   and a second Off beside it is the same fact twice. */
	function saysMore(tunnel: Tunnel): boolean {
		return label(tunnel) !== COPY.states.off;
	}

	function badge(tunnel: Tunnel): 'done' | 'blocked' | 'queued' {
		if (session.secretsLocked) return 'queued';
		if (tunnel.up) return 'done';
		if (tunnel.enabled) return 'blocked';
		return 'queued';
	}

	async function report(work: Promise<string | undefined>): Promise<void> {
		const refusal = await work;
		if (refusal) toasts.show(refusal, { tone: 'error' });
	}

	async function add(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		importing = true;
		const refusal = await tunnels.add(name.trim(), config);
		importing = false;
		tunnels.importError = refusal;
		if (!refusal) {
			name = '';
			config = '';
			toasts.show(COPY.imported, { tone: 'success' });
		}
	}

	async function toggle(tunnel: Tunnel, on: boolean): Promise<void> {
		await report(on ? tunnels.start(tunnel) : tunnels.stop(tunnel));
	}

	/** A provider reissues a configuration when it expires. */
	async function replace(tunnel: Tunnel, files: FileList | null): Promise<void> {
		const file = files?.[0];
		if (!file) return;
		await report(tunnels.replaceConfig(tunnel, await file.text()));
	}

	/** Reading a file rather than making somebody open it and copy what is inside. */
	async function chosen(file: File | null | undefined): Promise<void> {
		if (!file) return;
		config = await file.text();
		if (!name.trim()) name = file.name.replace(/\.conf$/i, '');
	}

	async function dropped(event: DragEvent): Promise<void> {
		event.preventDefault();
		dragging = false;
		await chosen(event.dataTransfer?.files?.[0]);
	}
</script>

<section>
	<SectionHeading id="sites.tunnels">{COPY.name}</SectionHeading>
	<p class="lede">{COPY.lede}</p>
	<!-- Kept, because it is the one thing about tunnels that surprises people and the consequence is
	     silence: a Site set to a tunnel that is down does not fall back, it simply does not
	     download. Folded rather than cut. -->
	<MoreAbout>
		<p>{COPY.noFallback}</p>
	</MoreAbout>

	<Problem message={tunnels.problem} />

	{#if tunnels.items.length > 0}
		<!-- In a box of its own for the room around it: the list stands clear of the fold above it
		     and of the note under it, which is about every tunnel rather than the last one. -->
		<div class="list">
			<DataRows items={tunnels.items} key={(item: Tunnel) => item.id} label={COPY.name} edges>
				{#snippet row(item: Tunnel)}
					<DataRow>
						<span class="name">{item.name}</span>
						{#if item.problem && !item.up}
							<!-- Why the last start failed, from the server: a port held by another program
						     (named), a configuration the client refused, a client that never connected.
						     "Not connecting" alone would send somebody to the log to find out which. -->
							<span class="detail">{item.problem}</span>
						{/if}
						<!-- A fact about the tunnel rather than about what it is doing, so it is shown in
					     every state, locked and off included: it was measured, and it stays true. -->
						<span class="detail hosting">{hosting(item)}</span>
						{#snippet trailing()}
							{@const address = addressOf(item)}
							{#if address}
								<!-- Named for what it is: the address of the server the tunnel connects to,
							     the same words the Downloads table and a download's details use. -->
								<span class="server">
									Tunnel server
									<ExitAddress {address} what={addressPhrase(item.name)} />
								</span>
							{/if}
							{#if saysMore(item)}
								<Badge state={badge(item)} label={label(item)} icon={mark(item)} />
							{/if}
							<!-- Shown as off and not touchable while locked, and the stored setting is left
						     alone: what was switched on starts again by itself the moment the key is
						     back, so turning it off here to look tidy would quietly stop that. -->
							<Switch
								checked={item.enabled && !session.secretsLocked}
								disabled={tunnels.busy || session.secretsLocked}
								label={COPY.use(item.name)}
								onCheckedChange={(on: boolean) => void toggle(item, on)}
							/>
						{/snippet}
						{#snippet actions()}
							<ChooseFile
								tone="ghost"
								size="small"
								icon="upload"
								accept=".conf,text/plain"
								disabled={tunnels.busy}
								label={COPY.replaceFor(item.name)}
								onchoose={(file: File) => void replace(item, [file] as unknown as FileList)}
							>
								{COPY.replace}
							</ChooseFile>
							<Tooltip label="Delete">
								<Button
									tone="ghost"
									size="small"
									icon="delete"
									disabled={tunnels.busy}
									onclick={() => {
										deleting = item;
										deleteOpen = true;
									}}
									aria-label={COPY.deleteNamed(item.name)}
								/>
							</Tooltip>
						{/snippet}
					</DataRow>
				{/snippet}
			</DataRows>
		</div>
	{/if}
	<!-- Under the list whether or not there is one yet: which kind of tunnel to import is the thing
	     to know BEFORE importing it, and the pause is what a person watching a download sees. -->
	<Note>{COPY.hostingHelp}</Note>

	<!-- Marked as taking its own drops: without it the window-wide importer takes the file first
	     and a configuration dropped here is fetched as media. -->
	<FormCard
		title={COPY.import}
		data-drop-zone
		active={dragging}
		onsubmit={add}
		ondragover={(event: DragEvent) => {
			event.preventDefault();
			dragging = true;
		}}
		ondragleave={() => (dragging = false)}
		ondrop={dropped}
	>
		<div class="drop">
			<Icon name="upload" size={20} />
			<p class="headline">{COPY.drop}</p>
			<!-- The shared chooser. -->
			<ChooseFile accept=".conf,text/plain" label={COPY.chooseLabel} onchoose={chosen}>
				{COPY.choose}
			</ChooseFile>
			<p class="hint">
				{COPY.fileFrom} <code>.conf</code>
				{COPY.fileRest}
			</p>
		</div>

		<FieldRow label={COPY.nameLabel} help={COPY.nameHelp}>
			{#snippet control({ id, describedBy }: { id: string; describedBy: string | undefined })}
				<TextInput {id} bind:value={name} {describedBy} />
			{/snippet}
		</FieldRow>

		<!-- Stacked: a pasted configuration is long, and a control column cannot hold it. -->
		<FieldRow label={COPY.configLabel} help={COPY.configHelp} error={tunnels.importError} stacked>
			{#snippet control({
				id,
				describedBy,
				invalid
			}: {
				id: string;
				describedBy: string | undefined;
				invalid: boolean;
			})}
				<!-- Write-only, like saved cookies: never bound to a stored value, so an imported
					 configuration has no way back to the screen. -->
				<div class="paste">
					<TextArea
						{id}
						{invalid}
						bind:value={config}
						{describedBy}
						autocomplete="off"
						spellcheck="false"
						rows={4}
					/>
				</div>
			{/snippet}
			{#snippet press()}
				<Button
					tone="primary"
					type="submit"
					icon="upload"
					disabled={importing || tunnels.busy || session.secretsLocked}>{COPY.importButton}</Button
				>
			{/snippet}
			{#snippet under()}
				{#if session.secretsLocked}
					<!-- Said here rather than only after pressing: a configuration is sealed with a key
					     that is not in memory, so this cannot succeed, and finding that out by filling the
					     form in and being refused is the confusion this avoids. -->
					<p class="blocked" role="status">{COPY.lockedFirst}</p>
				{/if}
			{/snippet}
		</FieldRow>
	</FormCard>
</section>

<ConfirmDialog
	bind:open={deleteOpen}
	title={COPY.deleteTitle(deleting ? deleting.name : null)}
	consequence={COPY.deleteSays}
	confirmLabel={COPY.deleteConfirm}
	onconfirm={() => {
		if (deleting) void report(tunnels.remove(deleting));
		deleting = null;
	}}
/>

<style>
	/* The label and the address it names stay on one line: a wrap between them would leave the
	   label naming nothing. */
	.server {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		white-space: nowrap;
	}

	.blocked {
		margin: 0;
		padding: var(--space-3);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.drop {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-6) var(--space-4);
		border: 1px dashed var(--sift-line);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-2);
		color: var(--sift-ink-3);
		text-align: center;
	}

	:global(.card.active) .drop {
		border-color: var(--sift-accent);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
	}

	.headline {
		margin: 0;
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.hint {
		margin: 0;
		font: var(--text-body-sm);
	}

	.lede {
		margin: 0 0 var(--space-3);
	}

	.list {
		margin-block: var(--space-3) var(--space-2);
	}

	/* Semibold full ink, as every row's name on a settings pane is. */
	.name {
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
	}

	/* Whole sentences, wrapped: the row's name is cut to one line, and inheriting that, the
	   sentence saying why a tunnel can't start would stop mid-word with no way to read the rest. */
	.detail {
		display: block;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		white-space: normal;
	}

	/* Quieter than the problem line above it: that one says why the tunnel is not working now,
	   this one is a standing fact about it, read when a swap is being set up. */
	.hosting {
		color: var(--sift-ink-3);
	}

	/* The one thing this paste box wants that no other box does: the tabular face, because what
	 * goes in it is a WireGuard configuration rather than a sentence. */
	.paste :global(.text-area) {
		font: var(--text-data);
	}
</style>
