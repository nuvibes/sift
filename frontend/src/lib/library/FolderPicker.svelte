<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import { Button, Checkbox, Pressable, Problem } from '$lib/components/common';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { Entry, Picker } from './picker.svelte';

	/* Choosing a folder, or a file in one, by clicking it. A breadcrumb over a list, rather than a
	 * tree that opens in place. Every chooser of a place on the server is this one. */

	interface Props {
		picker: Picker;
		/** The id of the element naming this picker. A list of folders is not a form control, so it
		 * is named the way a widget is rather than by a `label for=`. */
		labelledBy: string;
		/** The ids of the help and error text belonging to it, space separated. */
		describedBy?: string;
		/** Whether each folder gets a tick to add it to a set. */
		selectable?: boolean;
		/** Whether the rest of this device can be walked too, beside the folders Sift has; the
		 * list's name is then drawn here, with the press between the two. */
		device?: boolean;
	}

	let { picker, labelledBy, describedBy, selectable = false, device = false }: Props = $props();

	/* What the list is: the folders Sift has at the top, the drives at the top of the computer,
	   and otherwise the question the list answers. */
	const listName = $derived(
		!picker.atTopLevel
			? picker.names.length > 0
				? 'Which file'
				: 'Which folder'
			: picker.scope === 'machine'
				? 'Drives where Sift runs'
				: 'Folders Sift already has'
	);

	function enter(entry: Entry) {
		picker.open(entry.path);
	}
</script>

<!-- What the list is, with the way to the other list beside it; a caller without the device
     names the list itself. -->
{#if device}
	<div class="list-head">
		<span class="label" id={labelledBy}>{listName}</span>
		{#if picker.scope === 'granted'}
			<Button tone="quiet" size="small" onclick={() => void picker.look('machine')}>
				Browse this device
			</Button>
		{:else}
			<Button tone="quiet" size="small" onclick={() => void picker.look('granted')}>
				Back to the folders Sift has
			</Button>
		{/if}
	</div>
{/if}

<div class="picker">
	<!-- Only inside a folder: over the list of folders Sift already has there is no place to name,
	     and the heading above the picker already says what the list is. -->
	{#if !picker.atTopLevel}
		<nav class="crumbs" aria-label="Where you are">
			{#each picker.breadcrumb as crumb, index (crumb.path)}
				{#if index > 0}
					<Icon name="chevron_right" />
				{/if}
				<Button
					tone="ghost"
					size="small"
					class="crumb"
					aria-current={index === picker.breadcrumb.length - 1 ? 'true' : undefined}
					disabled={picker.loading}
					onclick={() => picker.open(crumb.path)}
				>
					{crumb.name}
				</Button>
			{/each}
		</nav>
	{/if}

	<Problem message={picker.failed} />

	<div class="viewport">
		<Scroller>
			<ul
				class="entries"
				aria-labelledby={labelledBy}
				aria-describedby={describedBy}
				aria-busy={picker.loading}
			>
				{#if picker.canGoUp}
					<li>
						<Pressable
							class="entry up"
							feedback="wash"
							radius="sm"
							disabled={picker.loading}
							onclick={() => picker.up()}
						>
							<Icon name="arrow_back" />
							<span>Back</span>
						</Pressable>
					</li>
				{/if}

				{#each picker.entries as entry (entry.path)}
					<li class="row">
						<!-- Tick to add the folder; click the name to go inside it. -->
						{#if selectable}
							<span class="tick">
								<Checkbox
									state={picker.isChosen(entry.path) ? 'on' : 'off'}
									disabled={picker.loading}
									onchange={() => picker.choose(entry)}
									label="Add {entry.name}"
								/>
							</span>
						{/if}
						<Pressable
							class="entry"
							feedback="wash"
							radius="sm"
							disabled={picker.loading}
							onclick={() => enter(entry)}
						>
							<Icon name="folder" filled />
							<span class="name">{entry.name}</span>
							<Icon name="chevron_right" />
						</Pressable>
					</li>
				{/each}

				<!-- In a chooser of a file, the files it offers here, after the folders. -->
				{#each picker.files as entry (entry.path)}
					<li class="row">
						<Pressable
							class="entry file"
							feedback="wash"
							radius="sm"
							picked={picker.file?.path === entry.path}
							disabled={picker.loading}
							onclick={() => picker.pick(entry)}
						>
							<!-- Wrapped, so the folder's yellow stays the folders'. -->
							<span class="glyph"><Icon name="article" /></span>
							<span class="name">{entry.name}</span>
							{#if picker.file?.path === entry.path}
								<Icon name="check" />
							{/if}
						</Pressable>
					</li>
				{/each}
			</ul>
		</Scroller>
	</div>

	<!-- What is in here that the list above does not show. -->
	{#if !picker.loading && !picker.nothingGranted && !picker.atTopLevel}
		{#if picker.names.length > 0}
			{#if picker.files.length === 0}
				<p class="quiet note">
					No file here that can be chosen. Click through to the folder that holds it.
				</p>
			{/if}
		{:else if picker.fileCount > 0}
			<p class="quiet note">
				{picker.fileCount === 1 ? '1 file' : `${counted(picker.fileCount)} files`} in this folder.
				{picker.entries.length === 0 ? 'Choose it and Sift will read them.' : ''}
			</p>
		{:else if picker.entries.length === 0}
			<p class="quiet note">
				This folder is empty. You can still choose it — anything put in it later is picked up.
			</p>
		{/if}
	{/if}
</div>

<style>
	/* The list's name on the left and the way to the other list at the right edge, on one line. */
	.list-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
	}

	.list-head .label {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.picker {
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
		overflow: hidden;
	}

	.crumbs {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-1);
		padding: var(--space-2) var(--space-3);
		border-bottom: 1px solid var(--sift-line);
		background: var(--sift-surface-3);
		color: var(--sift-ink-3);
	}

	/* The one you are in reads as the one you are in. */
	nav :global(.crumb[aria-current='true']) {
		color: var(--sift-ink);
		font-weight: 600;
	}

	/* A FIXED height, not a maximum. */
	.entries {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	/* An empty folder still has to hold the space open, or the one case with nothing in it puts
	   the jump straight back. */
	.entries:empty::after {
		content: '';
		display: block;
	}

	.row {
		display: flex;
		align-items: center;
	}

	/* Only where the box sits in the row. The box itself (its size, its edge, its tick) is
	   `Checkbox`'s. */
	.tick {
		display: inline-flex;
		flex: none;
		margin-inline: var(--space-3) 0;
	}

	/* A folder row: a whole surface that is pressed, so `Pressable`. */
	.entries :global(.entry) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		/* Fills the row beside the tick; `min-width: 0` lets a long name ellipsis rather than
		   push the row wider than the picker. */
		flex: 1;
		min-width: 0;
		height: 2rem;
		padding: 0 var(--space-3);
		color: var(--sift-ink);
		font: inherit;
		text-align: left;
	}

	/* The Back row has no tick, so it spans the whole width on its own. */
	.entries :global(.up) {
		width: 100%;
	}

	.entries :global(.entry .name) {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* The folder glyph in a warm Explorer-style folder yellow, so the list reads at a glance as
	   a file browser rather than a wall of one-colour rows. */
	.entries :global(.entry:not(.up) > .icon:first-child) {
		color: var(--sift-folder);
	}

	.entries :global(.up) {
		color: var(--sift-ink-3);
	}

	/* A file offered beside the folders: its own glyph, in the quieter ink, and no way inside. */
	.entries :global(.entry.file) {
		color: var(--sift-ink-2);
	}

	.glyph {
		display: inline-flex;
		color: var(--sift-ink-3);
	}

	/* The note under the list. Only the INSET is this picker's (the ink, the face and the margin
	   are the `.quiet` utility's), so the sentence lines up with the folder rows above it. */
	.note {
		padding: var(--space-3);
	}
	/* The cap moves onto the box that SCROLLS, and the rule is `:global` because that box is
	 * rendered by the shared region rather than written here. */
	.viewport :global(.scroll-root) {
		max-block-size: 15rem;
	}
</style>
