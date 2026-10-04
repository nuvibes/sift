<script lang="ts">
	/*
	 * Where downloads land.
	 *
	 * One folder, a property of the install, so this is the same control wherever it makes sense to
	 * ask: Settings, where somebody goes to set things, and the Downloads screen, where they find
	 * out they needed to.
	 *
	 * A folder, not a whole library: any folder inside a library can be the answer. There is one
	 * stored answer, and this is its control, the same one the naming panel shows beside its
	 * per-site rules, reading and writing the same place.
	 *
	 * The choices are the folders Sift may write in and nothing else: a free-text path would be a
	 * second way to add a library that skipped every check adding one does.
	 */
	import { onMount } from 'svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { api, ApiError } from '$lib/api/client';
	import { Problem, Select } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { movable } from '$lib/library/movable.svelte';
	import { disambiguate } from '$lib/library/folder-names';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import type { components } from '$lib/api/schema';

	/** The reserved scope everything follows unless a site is given its own. The server's word. */
	const EVERYTHING = '*default*';

	interface Props {
		/** Drawn small, for a screen where this is a footnote rather than the subject. */
		compact?: boolean;
	}

	let { compact = false }: Props = $props();

	let loading = $state(true);
	let saving = $state(false);
	/** Null until the answer arrives, so the control does not flash "none" on the way to one. */
	let chosen = $state<string | null>(null);
	/**
	 * What the naming rule is, carried so saving the folder does not write a blank over it: the two
	 * are stored together, so sending one and leaving the other out saves the missing one as empty.
	 */
	let naming = $state<string | null>(null);
	/**
	 * And the tool, carried for exactly the same reason the naming rule above is.
	 *
	 * This is the THIRD answer stored in that one row, and a whole-row write that names two of
	 * three saves the missing one as empty: choosing a folder here without it would silently put
	 * every Site back to "Sift decides".
	 */
	let downloader = $state<string | null>(null);
	let problem = $state<string | null>(null);

	/*
	 * Only folders Sift may write to.
	 *
	 * A read-only folder cannot hold a download, and the server refuses to store one, correctly.
	 * Offering it anyway would be a list where some entries produce an error and nothing on screen
	 * says which, so the ones that cannot work are left out and said out loud below.
	 */
	const usable = $derived(movable.folders);
	/* The name is the label and the path is the quiet phrase beside it, from the one function every
	   folder chooser in the app shares. See `disambiguate`. */
	const options = $derived(
		disambiguate(
			usable.map((folder) => ({
				value: folder.id,
				label: folder.name,
				path: folder.path || folder.name
			}))
		)
	);

	onMount(() => void load());
	/* The default folder is a setting (`site_options`, said on the settings bell): chosen in another
	   window, this follows. The choice here saves on the press, so a re-read holds nothing back. */
	whenChanged(settingChanges, () => void load());

	async function load() {
		loading = true;
		try {
			// Asked again rather than reused: a folder made or handed over since this was last drawn
			// is exactly the folder somebody is here to choose.
			movable.forget();
			await movable.ensure();
			const stored = await api.get<components['schemas']['SiteOptionsResponse']>('/site-options');
			chosen = stored.default.dest_folder_id ?? null;
			naming = stored.default.naming;
			downloader = stored.default.downloader ?? null;
			problem = null;
		} catch (error) {
			// A guest is refused these, and that is not a failure worth a red box: this control is
			// simply not theirs. Anything else is worth saying.
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				chosen = null;
			} else {
				problem = "Sift couldn't read your folders.";
			}
		} finally {
			loading = false;
		}
	}

	async function choose(id: string) {
		const previous = chosen;
		chosen = id;
		saving = true;
		try {
			await api.put(`/site-options/${encodeURIComponent(EVERYTHING)}`, {
				body: { naming, dest_folder_id: id, downloader }
			});
			await load();
			const name = usable.find((one) => one.id === id)?.name;
			toasts.show(['Downloads will go to ', name ? thing('folder', id, name) : 'that folder'], {
				tone: 'success'
			});
		} catch (error) {
			// Put back, so the control shows what the server actually thinks rather than staying where
			// the click left it and quietly lying about where files are going.
			chosen = previous;
			const said = error instanceof ApiError ? (error.detail ?? error.message) : null;
			toasts.show(said ?? "Couldn't change that", { tone: 'error' });
		} finally {
			saving = false;
		}
	}
</script>

<div class="folder" class:compact>
	{#if problem}
		<Problem message={problem} />
	{:else if loading}
		<p class="hint">Reading your folders&hellip;</p>
	{:else if usable.length === 0}
		<p class="hint">
			Sift has no folder it may write to, so there's nowhere to put a download. On the Folders
			screen, choose a folder and turn on "let Sift manage files here".
		</p>
	{:else}
		<!-- A settings row, not a stacked form field: it sits among other rows whose controls are
		     pinned right. The sentence is the label, and the chooser sits on its line. -->
		<LabelledRow label="Download folder">
			<Select
				label="Downloads go to"
				{options}
				value={chosen ?? undefined}
				placeholder="Choose a folder"
				disabled={saving}
				onValueChange={choose}
			/>
		</LabelledRow>
	{/if}
</div>

<style>
	/* No width of its own. It is a settings row, and a row takes the pane's own column: capping
	   it here would put the control inside the edge every other control is pinned to. */
	.folder {
		min-inline-size: 0;
	}

	.hint {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.compact .hint {
		font: var(--text-label);
	}
</style>
