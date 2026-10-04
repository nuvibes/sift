<script lang="ts">
	/*
	 * Whether this library answers the rest of the network, and what Windows has to allow for it.
	 *
	 * ## Why it is under General
	 *
	 * It is a choice about the whole install and this device's network, made once and rarely
	 * revisited, which is what General holds. Its address keeps the id it was given under Privacy,
	 * so an old link still rings it.
	 *
	 * ## What Windows asks, said before the switch is pressed
	 *
	 * Turning it on restarts Sift, and Windows then keeps other devices out until somebody allows
	 * them through its own permission prompt. Both are said in the Note under the switch while it is
	 * off, so neither arrives as a surprise.
	 *
	 * ## Its own file
	 *
	 * The switch is four lines and everything around it is not: a sheet that holds the screen while
	 * the server restarts, the address to type on the other machine, the state of Windows Firewall,
	 * a button that asks for administrator rights, and the command to run when that is declined.
	 * Inlining that into General would double the pane; it is one component with one job instead.
	 */
	import { onMount } from 'svelte';
	import { Button, Empty, Modal, Note, Switch } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { bridge, type FirewallReport, type FirewallScope, type Sharing } from '$lib/bridge';
	import { copyText } from '$lib/shell/clipboard';
	import ExitAddress from './ExitAddress.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { session } from '$lib/shell/session.svelte';
	import FactRow from './FactRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { NETWORK_SHARING } from './NetworkSharing.search';

	let shared = $state<Sharing | null>(null);
	let sharingBusy = $state(false);
	/* Which way it is going, kept for the sheet that says so. Read off the request rather than off
	   `shared`, which is the state BEFORE the change and would name the wrong direction. */
	let sharingTo = $state(false);

	/* The same rule the button below asks Windows to make, for somebody who would rather run it
	 * themselves, or whose machine could not be asked.
	 *
	 * Every part of it is written out except the port, which arrives from the shell that binds
	 * the socket, so this screen is not a second place that number lives. The rest stays literal
	 * because this is a thing somebody COPIES, and a string assembled from several variables is
	 * one that will eventually be assembled wrong.
	 *
	 * `Private` only (a home network, never a coffee shop's), `LocalSubnet` so only devices on the
	 * same network are let in (the shell's own rule says the same), and the port rather than the
	 * program, because what listens is the Python backend inside Sift rather than Sift itself,
	 * which is the mistake Windows' own "allow this app" prompt makes.
	 */
	const firewallCommand = $derived(
		'New-NetFirewallRule -DisplayName "Sift" -Direction Inbound ' +
			`-LocalPort ${shared?.port ?? ''} -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet`
	);

	/* Whether Windows is letting anything through, and whether we are in the middle of asking.
	 *
	 * ASKED SEPARATELY FROM THE SHARING STATE, and only when sharing is on. The question costs a
	 * PowerShell start, the switch must not wait on it, and the answer changes nothing while the
	 * library is not being offered to anybody. */
	let firewall = $state<FirewallReport>({ state: 'unknown', networks: null, scope: null });

	/* The rule is there and the network the machine is on is one it does not reach. Windows
	   files a new network as Public unless somebody says otherwise, so this is the ordinary
	   state of a machine that was just set up, and it must not read as "letting your other
	   computers through" while the block holds. */
	const blockedByCategory = $derived(
		firewall.state === 'open' &&
			firewall.scope === 'private' &&
			(firewall.networks?.includes('Public') ?? false)
	);
	let openingFirewall = $state(false);
	/* Set when the button was pressed and the port is still shut. That is when the command to run by
	   hand earns its place on the screen, and not before: it is the second way to do one thing. */
	let firewallRefused = $state(false);

	async function readFirewall() {
		if (shared?.enabled !== true) return;
		firewall = await bridge.firewall();
	}

	/* The prompt is Windows' own, so this can take as long as somebody takes to answer it, and
	 * what comes back is the state afterwards rather than whether they said yes. A refusal is not
	 * an error: it is a decision, and the screen goes back to saying what to run by hand. */
	async function openFirewall(scope: FirewallScope = 'private') {
		openingFirewall = true;
		try {
			firewall = await bridge.openFirewall(scope);
			firewallRefused = firewall.state !== 'open';
			toasts.show(
				firewall.state === 'open'
					? 'Windows now lets your other devices reach Sift'
					: "Nothing changed. Windows didn't get permission to open the port.",
				{ tone: firewall.state === 'open' ? 'success' : 'error' }
			);
		} finally {
			openingFirewall = false;
		}
	}

	onMount(() => {
		if (bridge.canShareOnNetwork()) {
			void bridge.sharing().then((state) => {
				shared = state;
				void readFirewall();
			});
		}
	});

	/* Flipping this STOPS AND STARTS Sift's server, so it takes a few seconds and it is awaited.
	 *
	 * The address a server listens on is chosen when its socket is opened, so this is the only way
	 * the switch can mean anything before the next launch. Two consequences show up here:
	 *
	 *   - the switch is disabled while it runs, or a second press lands on a backend that is
	 *     half-way through being restarted;
	 *   - the master key that unseals saved logins, stash-box keys and tunnels lives in memory and
	 *     goes with the restart, exactly as it does on a launch. Re-reading the session is what
	 *     makes the "unlock your saved keys" panel on the Sites pane notice and ask.
	 */
	async function setShared(on: boolean) {
		sharingTo = on;
		sharingBusy = true;
		try {
			shared = await bridge.setSharing(on);
			/* Said as an outcome, not as a promise. The sheet above says what is happening; this
			   says what happened, and it is the only part somebody reads after looking away. */
			const live = shared?.live ?? on;
			toasts.show(
				live === on
					? on
						? 'Other devices on your network can now open this library'
						: 'This library is no longer shared'
					: "Couldn't change sharing. Who can reach this library hasn't changed.",
				{ tone: live === on ? 'success' : 'error' }
			);
			void readFirewall();
		} finally {
			sharingBusy = false;
			await session.load();
		}
	}

	async function copyAddress() {
		if (shared?.address == null) return;
		const done = await copyText(shared.address);
		toasts.show(done ? 'Address copied' : "Couldn't copy the address", {
			tone: done ? 'success' : 'error'
		});
	}
</script>

<!--
	WHAT IS HAPPENING, SAID THE INSTANT IT STARTS.

	Flicking this switch stops Sift's server and starts it again: that is the only way the address
	it listens on can change before the next launch. For those few seconds every panel on this page
	fails its next request and the switch is disabled, and with nothing on the screen saying so,
	what that looks like is the application freezing.

	A sheet rather than a line under the switch, and not because a line is too quiet: the whole
	application is genuinely unavailable, so the honest thing is to hold the screen rather than
	leave somebody free to click into a screen that is about to error. It cannot be dismissed while
	the restart is running, for the same reason: `onOpenChange` is deliberately given nothing to
	do.
-->
<Modal
	open={sharingBusy}
	onOpenChange={() => undefined}
	title="Changing network sharing"
	description="Sift is restarting on a different network address. It takes a few seconds. Nothing is lost, and this page comes back by itself."
>
	{#snippet children()}
		<Empty scope="block" busy>
			{sharingTo
				? 'Sharing this library with other devices on your network\u2026'
				: 'Stopping sharing, so only this device can reach this library\u2026'}
		</Empty>
	{/snippet}
</Modal>

{#if shared !== null && shared.mode === 'standalone'}
	<section class="sharing">
		<SettingGroup
			id="privacy.network_sharing"
			heading={NETWORK_SHARING.heading}
			help={NETWORK_SHARING.lede}
		>
			<LabelledRow label={NETWORK_SHARING.name} help={NETWORK_SHARING.rowHelp}>
				<Switch
					disabled={sharingBusy}
					label={NETWORK_SHARING.name}
					bind:checked={() => shared?.enabled ?? false, (next: boolean) => void setShared(next)}
				/>
			</LabelledRow>
			{#if !shared.enabled}
				<Note>{NETWORK_SHARING.prompts}</Note>
			{/if}

			{#if shared.enabled && shared.address}
				<!--
					COVERED UNTIL IT IS ASKED FOR, and Copy is what makes that reasonable.

					This is the address of the machine holding the library, on a pane that stays
					open: the same fact the tunnel rows cover, so it is drawn the same way.
					`ExitAddress` is that one drawing: the first part plain, the rest painted
					over, and a press to show the whole of it.

					Covered, not blurred. A blur leaves the shapes legible at a glance and sharpens
					back out of a screenshot; a filled box says plainly that something is withheld.

					The button copies the whole address whether or not it is showing, which is how
					somebody uses this without ever putting it on the screen.
				-->
				<FactRow
					label="Address"
					help="Install Sift on the other device, choose to connect to this library when it asks, and enter this address."
				>
					<span class="address">
						<ExitAddress address={shared.address} what="this device's address" />
					</span>
					<Button icon="content_copy" onclick={() => void copyAddress()}>Copy</Button>
				</FactRow>
			{:else if shared.enabled}
				<FactRow
					label="Address"
					fact="Sift couldn't find this device's address. Check that it's connected to your network, then find the address in Windows network settings."
				/>
			{/if}
		</SettingGroup>

		{#if sharingBusy}
			<!-- The sheet above is what somebody is actually looking at. This is here so the section
			     does not flash its old note underneath while the sheet is up. -->
			<p class="note">Restarting Sift so this takes effect.</p>
		{:else if shared.enabled !== shared.live}
			<!--
				THE GAP BETWEEN THE SETTING AND WHAT IS ACTUALLY LISTENING, said in BOTH directions.

				The switch stops and starts the backend, so reaching this at all means the restart
				FAILED and the previous address was put back.

				Both directions, and the "off" half is the one that matters: somebody turns sharing
				off, is told nothing, and their library goes on answering the whole network exactly
				as before. Being quiet about a security setting that has not taken effect is the one
				thing this screen must not do.

				One condition rather than two, so neither direction can be added without the other.
			-->
			<p class="note" class:warn={!shared.enabled}>
				{#if shared.enabled}
					Sift couldn't start on this device's network address, so nothing is shared yet. Close Sift
					and open it again to try again.
				{:else}
					<strong>This library is still shared.</strong> Sift couldn't restart to stop sharing, so other
					devices can reach it until you close Sift and open it again.
				{/if}
			</p>
		{:else if shared.enabled}
			<!--
				THE SECOND THING THAT HAS TO BE TRUE, and it is not the switch above.

				Turning sharing on makes Sift listen. It does not make Windows let anything through:
				inbound connections are blocked by default, and the block is SILENT: the other
				computer simply waits and then gives up, which reads exactly like a wrong address.
				That is where somebody gets stuck, and nothing on the machine says so.

				THE BUTTON ASKS FOR ADMINISTRATOR RIGHTS, AND IT IS THE ONLY PLACE SIFT DOES.
				Opening a port is a decision about the whole machine, so it is asked at the moment
				somebody is deciding to share, through Windows' own prompt, which this application
				cannot suppress, pre-answer or find out the result of except by looking afterwards.
				The installer deliberately never asks for those rights, which is why this is not a
				tick-box there: it would be asking before anyone had decided to share anything.

				The command to run by hand is still here, and it appears when the button could not
				do it: a machine where the question cannot be put, or a prompt somebody declined.
			-->
			{#if blockedByCategory}
				<!-- The rule is there and reads as open; the network is not one it reaches. Two ways
				     out, and the narrow one first: telling Windows this is a home network keeps the
				     rule as it is. Opening on public networks as well is the wider door, and it says so. -->
				<p class="note">
					Windows treats this device's network as <strong>public</strong>, and Sift's firewall rule
					opens port {shared.port} on private networks only, so nothing gets through yet. If this is your
					own network, mark it as private in Windows (Settings, Network &amp; internet, then the network's
					properties). Otherwise, Sift can open the port on public networks too, which lets any device
					on the same network try to reach it.
				</p>
				<div class="firewall">
					<Button onclick={() => void openFirewall('any')} disabled={openingFirewall}>
						{openingFirewall ? 'Waiting for Windows\u2026' : 'Open port on public networks'}
					</Button>
				</div>
			{:else if firewall.state === 'open'}
				<p class="note">
					Windows lets your other devices reach Sift on port {shared.port}. To undo this later, run
					<code>Remove-NetFirewallRule -DisplayName "Sift"</code> in Windows PowerShell as an administrator.
				</p>
			{:else}
				<p class="note">
					{#if firewall.state === 'closed'}
						Windows is blocking other devices from reaching Sift, without any message: the other
						device waits and gives up, which looks like a wrong address.
					{:else}
						If the other device can't reach this one, Windows Firewall is almost always the reason.
						It blocks without a message, so the other device waits and gives up.
					{/if}
				</p>
				{#if firewall.state === 'closed'}
					<div class="firewall">
						<Button onclick={() => void openFirewall()} disabled={openingFirewall}>
							{openingFirewall ? 'Waiting for Windows\u2026' : 'Open the firewall port'}
						</Button>
						<p class="note small">
							Windows asks for your permission. Sift opens only port {shared.port}, on private
							networks only. The rule stays until you remove it, even if you uninstall Sift.
						</p>
					</div>
				{/if}
				{#if firewall.state === 'unknown' || firewallRefused}
					<p class="note">
						To do it yourself, open <strong>Windows PowerShell as administrator</strong> on this device
						and run this once:
					</p>
					<pre class="command">{firewallCommand}</pre>
					<p class="note">
						This lets other devices on your own network reach Sift, and nothing else. Remove it with <code
							>Remove-NetFirewallRule -DisplayName "Sift"</code
						>.
					</p>
				{/if}
			{/if}
		{/if}
	</section>
{/if}

<style>
	.note {
		margin: var(--space-2) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The spinner and its sentence on one line, which is what makes the sentence the label for the
	   spinner rather than a paragraph that happens to sit near one. */
	/* Read in the warning colour, not the quiet one. This is the sentence that says the machine is
	   still doing something the person has just asked it to stop doing, and a quiet grey note is how
	   that goes unread. */
	.note.warn {
		color: var(--sift-warn);
	}

	/* The last thing in the sharing block is a note, and a note has no bottom margin, so the next
	   heading on the screen would sit directly on it with nothing between them. The room belongs to the
	   section rather than to the note, because it is the SECTIONS that are being separated. */
	.sharing {
		margin-block-end: var(--space-8);
	}

	/* The button and the sentence that says what pressing it will do, kept together: that sentence
	   is the whole of what somebody is agreeing to, so it belongs to the button and not to the
	   paragraph above it. */
	.firewall {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-2);
		margin-block-start: var(--space-3);
	}

	/* A caption for the button above it rather than a paragraph of its own. */
	.note.small {
		font: var(--text-body-sm);
	}

	/* A command somebody copies. The data face, so it cannot be mistaken for prose, and it wraps
	   rather than scrolls: a line you have to drag sideways to read is a line you mistype. */
	.command {
		margin: var(--space-2) 0 0;
		padding: var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
		font: var(--text-data);
		color: var(--sift-ink);
		white-space: pre-wrap;
		word-break: break-word;
	}

	/* An address is a machine fact, so it takes the data face like every other one, and the row
	   holds it beside the button that copies it rather than under it.

	   The face and the ink are `ExitAddress`'s own; what is left here is the gap to the button,
	   which is this row's business rather than the address's. */
	.address {
		margin-inline-end: var(--space-3);
	}
</style>
