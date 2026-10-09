<script lang="ts">
	/* Where Sift keeps its own two folders, and moving them somewhere else. */
	import { onMount } from 'svelte';
	import { Problem, ProgressBar, Spinner } from '$lib/components/common';
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SaveFolderSection from './SaveFolder.svelte';
	import { bridge, type MoveProgress, type StorageReport } from '$lib/bridge';
	import { formatBytes } from './maintenance-state.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { COPY } from './StorageFolders.search';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';
	import { offersServer, readServerDesktop, type ServerDesktop } from '$lib/desktop/server-shell';
	import ServerStorageFolders from './ServerStorageFolders.svelte';

	let report = $state<StorageReport | null>(null);
	let moving = $state(false);
	let progress = $state<MoveProgress | null>(null);
	let problem = $state<string | null>(null);

	const offered = bridge.canMoveStorage();
	/* The computer running Sift, where this window is not on it and the app there answers. */
	let desk = $state<ServerDesktop | null>(null);

	/* Both blocks are the app's to draw: from a browser a search or a link naming one is told so. */
	$effect(() =>
		explainAbsentRows((key) => {
			const absent =
				(key === 'library.storage_folders' && !offered && !offersServer(desk)) ||
				(key === 'library.save_folder' && !bridge.canChooseDownloadFolder());
			return absent ? { because: COPY.inTheApp } : null;
		})
	);

	onMount(() => {
		if (!offered) {
			void readServerDesktop().then((answer) => (desk = answer));
			return;
		}
		void read();
		/* Returned from `onMount`, so the listener goes when the pane does. */
		return bridge.onStorageProgress((next) => (progress = next));
	});

	async function read() {
		report = await bridge.storage();
	}

	/* No size before the first walk ends: the word and the working mark, never a 0 B. */
	const unmeasured = $derived(report !== null && report.measuredAt === null);
	const REREAD_MS = 1_000;
	$effect(() => {
		if (!report?.measuring) return;
		const again = setTimeout(() => void read(), REREAD_MS);
		return () => clearTimeout(again);
	});

	async function moveIt() {
		moving = true;
		problem = null;
		progress = null;
		try {
			const outcome = await bridge.moveStorage();
			if (outcome.ok) {
				await read();
				toasts.show(COPY.moved, { tone: 'success' });
			} else if (outcome.reason !== null) {
				/* Kept on the screen rather than thrown as a toast: it says what is wrong with
				   the folder somebody picked, and it is read while they pick another one. */
				problem = outcome.reason;
			}
		} finally {
			moving = false;
			progress = null;
		}
	}
</script>

{#if offered}
	<SettingGroup id="library.storage_folders" heading={COPY.name} help={COPY.help} />

	{#snippet measuring()}<Spinner size={12} /> {COPY.measuring}{/snippet}

	{#if report}
		<ActionRow
			label={COPY.library.label}
			help={COPY.library.help(report.dataDir)}
			note={unmeasured ? undefined : formatBytes(report.dataBytes)}
			figure={unmeasured ? measuring : undefined}
			action={COPY.move}
			icon="folder"
			busy={moving}
			disabled={moving}
			onclick={() => void moveIt()}
		/>

		<ActionRow
			label={COPY.generated.label}
			help={COPY.generated.help(report.cacheDir)}
			note={unmeasured ? undefined : formatBytes(report.cacheBytes)}
			figure={unmeasured ? measuring : undefined}
			action={COPY.move}
			icon="folder"
			busy={moving}
			disabled={moving}
			onclick={() => void moveIt()}
		/>
	{/if}

	{#if moving}
		<!-- `null` while the total is still being measured, which is a sweep rather than a bar that
		     has not moved. On the same drive nothing is copied at all and this simply passes. -->
		<ProgressBar
			value={progress ? progress.copied : null}
			max={progress?.total || 1}
			label={COPY.moving}
		/>
	{/if}

	<!-- The shared one. -->
	<Problem message={problem} />
{:else if offersServer(desk)}
	<ServerStorageFolders machine={desk.machine} />
{/if}

<!--
	Where a file SAVED OUT of Sift lands: the other folder on this device that Sift writes to and never
	imports from, so it sits under the two it keeps for itself.
-->
<SaveFolderSection />
