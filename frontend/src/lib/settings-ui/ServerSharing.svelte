<script lang="ts">
	/* The sharing switch of the computer running Sift, seen from another computer. */
	import { ConfirmDialog, Note, Problem, Switch } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import type { components } from '$lib/api/schema';
	import { ApiError } from '$lib/api/client';
	import { serverBootId } from '$lib/shell/health';
	import { setServerSharing } from '$lib/desktop/server-shell';
	import { followSwitch } from './follow-switch';
	import { NETWORK_SHARING } from './NetworkSharing.search';
	import { COPY } from './General.search';

	type Sharing = components['schemas']['ShellSharing'];

	interface Props {
		machine: string | null;
		sharing: Sharing;
		/** How the page is loaded again once Sift is back; the tests hand their own. */
		arrive?: () => void;
	}

	let { machine, sharing, arrive = () => location.reload() }: Props = $props();

	const WORDS = COPY.server.sharing;

	/* What the switch shows: the server's answer, then what was asked while it is being done. */
	let asked = $state<boolean | null>(null);
	const checked = $derived(asked ?? sharing.enabled);
	let confirming = $state(false);
	let working = $state<'restarting' | 'cut-off' | null>(null);
	let problem = $state<string | null>(null);

	/* Whether the question was answered with Stop sharing, rather than closed. */
	let stopping = false;

	function flip(on: boolean) {
		problem = null;
		if (!on) {
			/* The switch shows off while it asks, and goes back on if the question is closed. */
			asked = false;
			stopping = false;
			confirming = true;
			return;
		}
		void change(true);
	}

	function stop() {
		stopping = true;
		void change(false);
	}

	$effect(() => {
		if (!confirming && asked === false && !stopping) asked = null;
	});

	async function change(on: boolean) {
		asked = on;
		const before = await serverBootId();
		try {
			const taken = await setServerSharing(on);
			if (!taken.ok) {
				asked = null;
				problem = taken.refusal ?? WORDS.cannot;
				return;
			}
		} catch (error) {
			asked = null;
			problem = error instanceof ApiError ? (error.detail ?? error.message) : WORDS.cannot;
			return;
		}
		working = on ? 'restarting' : 'cut-off';
		/* Off: nothing on another computer comes back, and the sentence already says so. */
		if (!(await followSwitch(before, { arrive })) && on) problem = COPY.server.slow;
	}
</script>

<LabelledRow label={NETWORK_SHARING.name} help={WORDS.help(sharing.live ? sharing.address : null)}>
	<Switch
		label={NETWORK_SHARING.name}
		{checked}
		disabled={working !== null}
		onCheckedChange={flip}
	/>
</LabelledRow>
{#if working === 'restarting'}
	<Note>{WORDS.restarting(machine)}</Note>
{:else if working === 'cut-off'}
	<Note>{WORDS.cutOff(machine)}</Note>
{/if}
<Problem message={problem} />

<ConfirmDialog
	bind:open={confirming}
	title={WORDS.offTitle}
	consequence={WORDS.offSays(machine)}
	confirmLabel={WORDS.offConfirm}
	destructive={false}
	onconfirm={stop}
/>
