<script lang="ts">
	/* Whether this library answers the rest of the network, and what Windows has to allow for
	 * it. */
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
	/* Which way it is going, kept for the sheet that says so. */
	let sharingTo = $state(false);

	/* The same rule the button below asks Windows to make, for somebody who would rather run it
	 * themselves, or whose machine could not be asked. */
	const firewallCommand = $derived(
		'New-NetFirewallRule -DisplayName "Sift" -Direction Inbound ' +
			`-LocalPort ${shared?.port ?? ''} -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet`
	);

	/* Whether Windows is letting anything through, and whether we are in the middle of asking. */
	let firewall = $state<FirewallReport>({ state: 'unknown', networks: null, scope: null });

	/* The rule is there and the network the machine is on is one it does not reach. */
	const blockedByCategory = $derived(
		firewall.state === 'open' &&
			firewall.scope === 'private' &&
			(firewall.networks?.includes('Public') ?? false)
	);
	let openingFirewall = $state(false);
	/* Set when the button was pressed and the port is still shut. */
	let firewallRefused = $state(false);

	async function readFirewall() {
		if (shared?.enabled !== true) return;
		firewall = await bridge.firewall();
	}

	/* The prompt is Windows' own, so this can take as long as somebody takes to answer it, and
	 * what comes back is the state afterwards rather than whether they said yes. */
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

	/* Flipping this STOPS AND STARTS Sift's server, so it takes a few seconds and it is awaited. */
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

<!-- WHAT IS HAPPENING, SAID THE INSTANT IT STARTS. -->
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
				<!-- COVERED UNTIL IT IS ASKED FOR, and Copy is what makes that reasonable. -->
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
			<!-- The sheet above is what somebody is actually looking at. -->
			<p class="note">Restarting Sift so this takes effect.</p>
		{:else if shared.enabled !== shared.live}
			<!--
				THE GAP BETWEEN THE SETTING AND WHAT IS ACTUALLY LISTENING, said in BOTH directions.
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
			<!-- THE SECOND THING THAT HAS TO BE TRUE, and it is not the switch above. -->
			{#if blockedByCategory}
				<!-- The rule is there and reads as open; the network is not one it reaches. -->
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

	/* The spinner and its sentence on one line, which is what makes the sentence the label for
	   the spinner rather than a paragraph that happens to sit near one. */
	/* Read in the warning colour, not the quiet one. */
	.note.warn {
		color: var(--sift-warn);
	}

	/* The last thing in the sharing block is a note, and a note has no bottom margin, so the
	   next heading on the screen would sit directly on it with nothing between them. */
	.sharing {
		margin-block-end: var(--space-8);
	}

	/* The button and the sentence that says what pressing it will do, kept together: that
	   sentence is the whole of what somebody is agreeing to, so it belongs to the button and not
	   to the paragraph above it. */
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
	   holds it beside the button that copies it rather than under it. */
	.address {
		margin-inline-end: var(--space-3);
	}
</style>
