<script lang="ts">
	/* LIVE: nothing moves it (the stored default, read at the moment an offer is weighed and written back immediately) */
	/* Where downloads go, proposed the moment a library gains its first folder. */
	import { api, ApiError } from '$lib/api/client';
	import { Button, Panel, Problem } from '$lib/components/common';
	import PathText from '$lib/components/PathText.svelte';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import type { Library, Root } from './library.svelte';
	import type { components } from '$lib/api/schema';

	type SiteOptions = components['schemas']['SiteOptionsResponse'];
	type FolderView = components['schemas']['FolderView'];

	/** The scope everything follows unless a Site is given its own. The server's word. */
	const EVERYTHING = '*default*';
	/** The folder proposed inside the library's first folder. */
	const PROPOSED = 'Sift Downloads';

	let { library }: { library: Library } = $props();

	/** The library folder the proposal is inside, once there is one to propose. */
	let offered = $state<Root | null>(null);
	let busy = $state(false);
	let problem = $state<string | null>(null);

	/* Plain values rather than state: they record what has been SEEN, and reading them inside
	   the effect below must not make it run again. */
	let sawNoFolders = false;
	let considered = false;

	$effect(() => {
		if (library.loading || !library.canManage) return;
		if (library.roots.length === 0) {
			sawNoFolders = true;
			return;
		}
		if (!sawNoFolders || considered) return;
		considered = true;
		void consider(library.roots[0]);
	});

	/** Propose only where nothing is set: a default somebody already chose is their answer. */
	async function consider(root: Root): Promise<void> {
		try {
			const stored = await api.get<SiteOptions>('/site-options');
			if (stored.default.dest_folder_id) return;
		} catch {
			// Not known, so not proposed: an offer over a folder that may already be set is a guess.
			return;
		}
		offered = root;
	}

	/** Where the proposed folder would be, written the way the library's own path is. */
	const proposedPath = $derived.by(() => {
		if (!offered) return '';
		const separator = offered.path.includes('\\') ? '\\' : '/';
		return `${offered.path.replace(/[\\/]+$/, '')}${separator}${PROPOSED}`;
	});

	async function use(): Promise<void> {
		const root = offered;
		if (!root || busy) return;
		const top = library.folders.find((one) => one.root_id === root.id && one.parent_id === null);
		if (!top) {
			problem = 'Sift could not find that library folder. Choose a folder in Settings instead.';
			return;
		}
		busy = true;
		problem = null;
		try {
			// Placed rather than made: a Sift Downloads folder already on the disk that no scan has
			// recorded is the one meant, and a make would refuse its name as taken.
			const made = await api.post<FolderView>('/library/folders/placed', {
				body: { parent_id: top.id, name: PROPOSED }
			});
			// Read at the moment of the write, so the naming rule and the tool go back as they are.
			const stored = await api.get<SiteOptions>('/site-options');
			await api.put(`/site-options/${encodeURIComponent(EVERYTHING)}`, {
				body: {
					naming: stored.default.naming,
					dest_folder_id: made.id,
					downloader: stored.default.downloader ?? null
				}
			});
			offered = null;
			void library.load();
			toasts.show(['Downloads will go to ', thing('folder', made.id, made.name)], {
				tone: 'success'
			});
		} catch (error) {
			problem =
				error instanceof ApiError
					? (error.detail ?? error.message)
					: 'Sift could not create that folder.';
		} finally {
			busy = false;
		}
	}

	function change(): void {
		offered = null;
		openSettings('downloads', 'downloads.name_template');
	}

	function skip(): void {
		offered = null;
	}
</script>

{#if offered}
	<Panel label="Download folder">
		<!-- The path through PathText, as every path on screen: where the profile folder's name is
		     hidden, the server sends a marker for it, and PathText draws that as a blur rather than
		     as the marker's word. -->
		<p class="said">
			Would you like to create a default folder at <span class="path"
				><PathText path={proposedPath} /></span
			> to store things you download using Sift?
		</p>
		<Problem message={problem} />
		<div class="answers">
			<Button
				tone="primary"
				size="small"
				icon="create_new_folder"
				disabled={busy}
				onclick={() => void use()}
			>
				Create this folder
			</Button>
			<Button size="small" disabled={busy} onclick={change}>Choose another</Button>
			<Button size="small" disabled={busy} onclick={skip}>Skip</Button>
		</div>
	</Panel>
{/if}

<style>
	.said {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* A path is read a character at a time, and a long one must wrap rather than widen the page. */
	.path {
		overflow-wrap: anywhere;
	}

	.answers {
		display: flex;
		gap: var(--space-2);
	}
</style>
