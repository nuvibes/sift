<script lang="ts">
	/* Settings > Folders: where a file SAVED OUT of Sift lands on this device. */
	import { onMount } from 'svelte';

	import { Button, SectionHeading } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { bridge, type DownloadFolder } from '$lib/bridge';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY as PANE } from './StorageFolders.search';

	const COPY = PANE.saveFolder;

	let saveTo = $state<DownloadFolder | null>(null);

	onMount(() => {
		if (bridge.canChooseDownloadFolder())
			void bridge.downloadFolder().then((where) => (saveTo = where));
	});

	/* The picker is the operating system's, and it has to be: a page naming a folder for this
	   application to write into would be a page choosing where it writes. */
	async function pickSaveFolder() {
		saveTo = await bridge.chooseDownloadFolder(true);
	}

	async function resetSaveFolder() {
		saveTo = await bridge.chooseDownloadFolder();
	}
</script>

{#if saveTo !== null}
	<section>
		<SectionHeading id="library.save_folder">{COPY.name}</SectionHeading>
		<p class="lede">{COPY.lede}</p>

		<SettingGroup>
			<!-- The path is the row's STATE, so its foot line: a long one wraps under the help. -->
			<LabelledRow
				label={COPY.label}
				help={COPY.help}
				helpId="save-folder-help"
				disclosure={COPY.disclosure}
			>
				{#snippet foot()}
					<code class="path" aria-describedby="save-folder-help">{saveTo?.path}</code>
				{/snippet}
				<div class="presses">
					<Button onclick={() => void pickSaveFolder()}>{COPY.choose}</Button>
					{#if saveTo?.chosen}
						<!-- Only where there is something to undo. A "Use Downloads" button beside a
						     path that already IS Downloads is a control that does nothing. -->
						<Button tone="ghost" onclick={() => void resetSaveFolder()}>
							{COPY.reset}
						</Button>
					{/if}
				</div>
			</LabelledRow>
		</SettingGroup>
	</section>
{/if}

<style>
	/* The heading is `SectionHeading`, which is how every group on every pane is headed. */
	.lede {
		margin-block-end: var(--space-4);
	}

	/* Where the presses stand is the row's (`--row-pack`); this only keeps them on one line. */
	.presses {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/* A chosen folder can be long, so it may wrap anywhere. */
	.path {
		overflow-wrap: anywhere;
		font: var(--text-body-sm);
		color: var(--muted-foreground);
	}
</style>
