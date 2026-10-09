<script lang="ts">
	/* General: what the application does on THIS device, and the choices about the whole app
	 * that belong to no one feature. */
	import { onMount } from 'svelte';
	import { Note, Problem, Switch } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { bridge } from '$lib/bridge';
	import {
		deleteConfirmationSkipped,
		forgetDeleteConfirmation,
		rememberDeleteConfirmation
	} from '$lib/shell/remembered.svelte';
	import {
		askBeforeChipRemove,
		chipRemoveSkipped,
		recallInterfaceState,
		skipChipRemoveConfirm
	} from '$lib/shell/interface-state.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';
	import ActionRow from './ActionRow.svelte';
	import ClosingTheWindow from './ClosingTheWindow.svelte';
	import LinksOpenIn from './LinksOpenIn.svelte';
	import NetworkSharing from './NetworkSharing.svelte';
	import ServerFirewall from './ServerFirewall.svelte';
	import ServerSharing from './ServerSharing.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY } from './General.search';
	import {
		offersServer,
		readServerDesktop,
		restartServer,
		setServerStartsWithWindows,
		thisDevice,
		type ServerDesktop
	} from '$lib/desktop/server-shell';

	/* Asked once: whether this shell can answer any of the three device rows. */
	const offersStartup = bridge.canStartWithWindows();
	const offersDevice =
		bridge.canChooseBrowser() || bridge.canKeepRunningWhenClosed() || offersStartup;

	/* The way back to setup, and the restart that reaches it now rather than at the next launch. */
	const offersSetup = bridge.canForgetMode();
	const offersRestart = bridge.canRestartApp();

	/* The app on a computer that is not the one running Sift: a shell that answers this window's
	   rows and cannot restart the Sift it is looking at. */
	const clientWindow = offersDevice && !offersRestart;
	let here = $state<string | null>(null);

	/* The rows only Sift's app can answer, reached from a browser by a search or a link: the line
	   that says where they are is rung in their place, or the sentence says it where no line is. */
	const DEVICE_ROWS = [
		'general.links_open_in',
		'general.closing_the_window',
		'general.start_with_windows'
	];
	const SETUP_ROWS = ['general.library_location', 'general.run_setup', 'general.restart'];
	/* The computer running Sift, when this window is not on it: the app in client mode, or a
	   browser. */
	let desk = $state<ServerDesktop | null>(null);
	const reachesServer = $derived(offersServer(desk) && !(offersStartup && offersRestart));
	let restartingServer = $state(false);
	let serverProblem = $state<string | null>(null);

	onMount(() => {
		void readServerDesktop().then((answer) => (desk = answer));
		if (clientWindow) void thisDevice().then((name) => (here = name));
	});

	async function setServerStarting(on: boolean) {
		if (desk === null) return;
		serverProblem = null;
		desk = { ...desk, starts_with_windows: on };
		try {
			desk = await setServerStartsWithWindows(on);
		} catch {
			desk = await readServerDesktop();
			serverProblem = COPY.server.cannot;
		}
	}

	async function restartThere() {
		restartingServer = true;
		serverProblem = null;
		const outcome = await restartServer(COPY.server.cannot, COPY.server.slow);
		/* On ok the page is already loading again from the new run; nothing here is put back. */
		if (outcome.ok) return;
		restartingServer = false;
		serverProblem = outcome.problem;
	}

	const ON_THIS_COMPUTER =
		'The computer running Sift is changed from here only while the Sift app is open on it and you are on another computer.';
	$effect(() =>
		explainAbsentRows((key) => {
			/* Asked from another computer, the switches and the restart are the ones in the
			   group about the computer running Sift. */
			const there = ['general.restart', 'privacy.network_sharing'];
			/* This window's own switch is drawn here in the app; only a browser is sent there. */
			if (!offersStartup) there.push('general.start_with_windows');
			if (reachesServer && there.includes(key)) {
				return { because: COPY.server.help(desk?.machine ?? null), near: 'general.server' };
			}
			if (DEVICE_ROWS.includes(key) && !offersDevice) {
				return { because: COPY.inABrowser, near: 'general.in_a_browser' };
			}
			if (SETUP_ROWS.includes(key) && !offersSetup) return { because: COPY.setupInTheApp };
			/* The group about the computer running Sift is drawn only where it can act on that one. */
			if (key === 'general.server' && !reachesServer) return { because: ON_THIS_COMPUTER };
			if (key === 'general.restart' && !offersRestart) {
				return { because: COPY.restart.byHand, near: 'general.library_location' };
			}
			if (key === 'privacy.network_sharing' && !bridge.canShareOnNetwork()) {
				return { because: COPY.sharingInTheApp };
			}
			return null;
		})
	);
	let forgetting = $state(false);
	let restarting = $state(false);

	async function forget() {
		forgetting = true;
		try {
			if (await bridge.forgetMode()) toasts.show(COPY.again.done, { tone: 'success' });
		} finally {
			forgetting = false;
		}
	}

	async function restart() {
		restarting = true;
		/* True means the app is on its way down and this page with it. */
		if (!(await bridge.restartApp())) {
			restarting = false;
			toasts.show(COPY.restart.failed, { tone: 'error' });
		}
	}

	/* This browser's own answer, not a registry setting: the delete sheet's "Don't ask me again"
	   box writes it, and this is the one place it can be turned back on. */
	let askBeforeDelete = $state(!deleteConfirmationSkipped());
	/* The account's answer, not this browser's: the chip's "Don't ask me again" box writes it to
	   the account's interface state, so it is read after that document has landed. */
	let askBeforeRemove = $state(true);
	onMount(() => {
		void recallInterfaceState().then(() => {
			askBeforeRemove = !chipRemoveSkipped();
		});
	});

	/* Null until the shell has answered, and the row is not drawn before then: a switch showing
	   off while the question is still in flight is a switch that says the wrong thing first. */
	let starting = $state<boolean | null>(null);

	onMount(() => {
		if (offersStartup) void bridge.startsWithWindows().then((answer) => (starting = answer));
	});

	async function setStarting(on: boolean) {
		/* Shown immediately and then corrected by what the shell answers, as the close-button switch is:
		   a switch that waits for a round trip before moving reads as a switch that did not take. */
		starting = on;
		starting = await bridge.setStartsWithWindows(on);
	}
</script>

{#if !offersDevice}
	<!-- The pane's first group, so the group after it opens at the group gap under its heading's
	     line rather than flush against this sentence. -->
	<SettingGroup id="general.in_a_browser">
		<Note>{COPY.inABrowser}</Note>
	</SettingGroup>
{:else}
	{#if clientWindow}
		<!-- In the app on another computer: what follows is this computer's, not the server's. -->
		<SettingGroup
			id="general.this_window"
			heading={COPY.thisWindow.heading(here)}
			help={COPY.thisWindow.help}
		/>
	{/if}
	<LinksOpenIn />

	<ClosingTheWindow />

	{#if offersStartup && starting !== null}
		<SettingGroup id="general.start_with_windows" heading={COPY.startup}>
			<LabelledRow label={COPY.startWithWindows.name} help={COPY.startWithWindows.help}>
				<Switch
					label={COPY.startWithWindows.name}
					checked={starting}
					onCheckedChange={(on: boolean) => void setStarting(on)}
				/>
			</LabelledRow>
		</SettingGroup>
	{/if}
{/if}

<SettingGroup id="general.confirmations" heading={COPY.confirmations}>
	<LabelledRow
		id="editing.delete.ask"
		label={COPY.askDelete.name}
		help={COPY.askDelete.help}
		helpId="general-delete-ask-help"
	>
		<Switch
			label={COPY.askDelete.name}
			describedBy="general-delete-ask-help"
			checked={askBeforeDelete}
			onCheckedChange={(on) => {
				if (on) forgetDeleteConfirmation();
				else rememberDeleteConfirmation();
				askBeforeDelete = on;
			}}
		/>
	</LabelledRow>
	<LabelledRow
		id="editing.remove.ask"
		label={COPY.askRemove.name}
		help={COPY.askRemove.help}
		helpId="general-remove-ask-help"
	>
		<Switch
			label={COPY.askRemove.name}
			describedBy="general-remove-ask-help"
			checked={askBeforeRemove}
			onCheckedChange={(on) => {
				if (on) askBeforeChipRemove();
				else skipChipRemoveConfirm();
				askBeforeRemove = on;
			}}
		/>
	</LabelledRow>
</SettingGroup>

{#if reachesServer && desk !== null}
	<!-- The computer running Sift, from another computer: what is changed here changes there. -->
	<SettingGroup
		id="general.server"
		heading={COPY.server.heading}
		help={COPY.server.help(desk.machine)}
	>
		{#if desk.starts_with_windows !== null}
			<LabelledRow label={COPY.startWithWindows.name} help={COPY.startWithWindows.help}>
				<Switch
					label={COPY.startWithWindows.name}
					checked={desk.starts_with_windows}
					onCheckedChange={(on: boolean) => void setServerStarting(on)}
				/>
			</LabelledRow>
		{/if}
		{#if !offersRestart}
			<ActionRow
				id="general.server_restart"
				label={COPY.restart.label}
				help={COPY.server.restartHelp}
				action={COPY.restart.action}
				busy={restartingServer}
				disabled={restartingServer}
				onclick={() => void restartThere()}
			/>
			{#if restartingServer}
				<Note>{COPY.server.restarting(desk.machine)}</Note>
			{/if}
		{/if}
		{#if desk.sharing !== null && !bridge.canShareOnNetwork()}
			<ServerSharing machine={desk.machine} sharing={desk.sharing} />
		{/if}
		{#if desk.sharing?.enabled}
			<ServerFirewall machine={desk.machine} port={desk.sharing.port} />
		{/if}
		<Problem message={serverProblem} />
	</SettingGroup>
{/if}

<!-- Who can reach this library over the network, and what Windows asks when it goes on. -->
<NetworkSharing />

<!-- Last, because it is the one group here that changes how Sift starts. -->
{#if offersSetup}
	<SettingGroup
		id="general.library_location"
		heading={COPY.location.name}
		help={COPY.location.help}
	>
		<ActionRow
			id="general.run_setup"
			label={COPY.again.label}
			help={COPY.again.help}
			action={COPY.again.action}
			busy={forgetting}
			disabled={forgetting}
			onclick={() => void forget()}
		/>
		{#if offersRestart}
			<ActionRow
				id="general.restart"
				label={COPY.restart.label}
				help={COPY.restart.help}
				action={COPY.restart.action}
				busy={restarting}
				disabled={restarting}
				onclick={() => void restart()}
			/>
		{:else}
			<Note>{COPY.restart.byHand}</Note>
		{/if}
	</SettingGroup>
{/if}
