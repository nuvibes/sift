<script lang="ts">
	/*
	 * Where Sift keeps its own two folders, and moving them somewhere else.
	 *
	 * ## Why this screen exists
	 *
	 * They are chosen once, at first run, and a library outgrows the disk it was started on: that
	 * is the ordinary case, not an edge one. Without this there would be no way to change them and
	 * nothing anywhere saying where they were, and the only answer would be to uninstall and start
	 * again.
	 *
	 * ## What it does not offer, and why
	 *
	 * A path to type. The folder is chosen in the operating system's own picker, on the far side of
	 * the bridge: a page that could name the folder a library moves into would be a page choosing
	 * where this application writes, and the whole bridge is built on it not being able to. The
	 * download folder follows the same pattern.
	 *
	 * ## What another computer sees here
	 *
	 * In a browser these folders are on somebody else's computer; in client mode they belong to the
	 * machine holding the library. `canMoveStorage` is the shell answering for itself rather than
	 * this file guessing from the user agent. Where the Sift app on that computer answers through the
	 * server (`server-shell.ts`), the same two rows are drawn about THAT computer's folders
	 * (`ServerStorageFolders`), and a move there is chosen in the server's own folder browser. Where
	 * nothing answers, nothing is drawn, because there is nothing true it could say.
	 *
	 * Where the library is (this device, or another one) and running setup again are on General:
	 * that choice changes how Sift starts, and these folders stay put whichever it is.
	 */
	import { onMount } from 'svelte';
	import { Problem, ProgressBar } from '$lib/components/common';
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
		/* Returned from `onMount`, so the listener goes when the pane does. A shell event handler
		   left attached to an unmounted component writes into state nothing is drawing. */
		return bridge.onStorageProgress((next) => (progress = next));
	});

	async function read() {
		report = await bridge.storage();
	}

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
				/* Kept on the screen rather than thrown as a toast: it says what is wrong with the
				   folder somebody picked, and it is read while they pick another one. A toast is
				   gone by then. `null` is them closing the picker, which is not a failure. */
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

	{#if report}
		<ActionRow
			label={COPY.library.label}
			help={COPY.library.help(report.dataDir)}
			note={formatBytes(report.dataBytes)}
			action={COPY.move}
			icon="folder"
			busy={moving}
			disabled={moving}
			onclick={() => void moveIt()}
		/>

		<ActionRow
			label={COPY.generated.label}
			help={COPY.generated.help(report.cacheDir)}
			note={formatBytes(report.cacheBytes)}
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

	<!-- The shared one. An alert paragraph of this file's own, with style rules of its own,
	     would be a second look at a problem and, sooner or later, a second answer about whether
	     one is announced: exactly what `Problem`'s contract test refuses.

	     That test matches the CLASS ATTRIBUTE anywhere in the file, comments included, so a
	     note here quoting the markup it forbids fails it just as the markup would. Say it in
	     words. -->
	<Problem message={problem} />
{:else if offersServer(desk)}
	<ServerStorageFolders machine={desk.machine} />
{/if}

<!-- Where a file SAVED OUT of Sift lands: the other folder on this device that Sift writes to
     and never imports from, so it sits under the two it keeps for itself. Outside the block
     above, because whether it is offered is its own question (the shell can choose a save
     folder where it cannot move storage) and it answers that itself. -->
<SaveFolderSection />
