<script lang="ts">
	/* NOT ON THE GALLERY: it is drawn on Settings > Updates and Info, and its one press resets this install's real device id. */
	/*
	 * Settings > Updates and Info > Your device id: what another Sift is told about this one.
	 *
	 * Beside the version and the licence because it is a fact about this install rather than a
	 * choice: a swap shows it to the other side, and resetting it changes what every later swap is
	 * told. The swap itself starts from the Swap screen alone.
	 *
	 * Read again whenever a setting moves anywhere: resetting the id from another window announces
	 * itself, and a stale id on this screen is the one thing it must not show.
	 */
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api/client';
	import {
		Button,
		ConfirmDialog,
		ContextMenuItem,
		LabelledRow,
		MenuButton,
		Problem
	} from '$lib/components/common';
	import { copyText } from '$lib/shell/clipboard';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { inFours, resetDevice, swapDevice, type SwapDevice } from '$lib/components/swap/swap';
	import { COPY as PANE } from './Updates.search';

	const COPY = PANE.deviceId;

	let device = $state<SwapDevice | null>(null);
	let problem = $state<string | null>(null);
	let confirming = $state(false);
	let busy = $state(false);

	async function load(): Promise<void> {
		try {
			device = await swapDevice();
			problem = null;
		} catch {
			problem = COPY.cannotLoad;
		}
	}

	onMount(() => void load());
	whenChanged(settingChanges, () => void load());

	const shown = $derived(device?.device_id ? inFours(device.device_id) : null);

	async function copy(): Promise<void> {
		if (shown && (await copyText(shown))) toasts.show(COPY.copied);
	}

	async function reset(): Promise<void> {
		busy = true;
		try {
			device = await resetDevice();
			toasts.show(COPY.wasReset);
		} catch (error) {
			problem = error instanceof ApiError ? (error.detail ?? error.message) : COPY.cannotReset;
		} finally {
			busy = false;
		}
	}
</script>

<SettingGroup id="updates.swaps" heading={COPY.heading} help={COPY.help}>
	<LabelledRow
		id="updates.device_id"
		label={COPY.label}
		help={shown ? undefined : device?.locked ? COPY.locked : COPY.notYet}
		foot={shown ? idLine : undefined}
	>
		{#if shown}
			<Button icon="content_copy" onclick={() => void copy()}>{COPY.copy}</Button>
		{/if}
		<!-- A reset is rare and changes what every later swap is told, so it waits behind the
		     three dots and a confirm rather than sitting beside Copy. -->
		<MenuButton label={COPY.more} disabled={!shown || busy}>
			<ContextMenuItem label={COPY.reset.press} onselect={() => (confirming = true)} />
		</MenuButton>
	</LabelledRow>

	<Problem message={problem} />
</SettingGroup>

{#snippet idLine()}
	<span class="id">{shown}</span>
{/snippet}

<ConfirmDialog
	bind:open={confirming}
	title={COPY.reset.title}
	consequence={COPY.reset.consequence}
	confirmLabel={COPY.reset.press}
	onconfirm={() => void reset()}
/>

<style>
	/* The id in the row's foot line, in the figures machine facts use, whole on a copy by hand. */
	.id {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
		user-select: all;
	}
</style>
