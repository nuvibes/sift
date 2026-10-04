<script lang="ts">
	/*
	 * The name of what is showing in Settings: a section, or a sub-page opened over one.
	 *
	 * ## Drawn by the frame, never by a pane
	 *
	 * A title each pane writes for itself is a rule each pane can forget: a section with no title
	 * at all, or one called "Backup and restore" in the list and "Backup" above the pane. A title
	 * is a property of BEING a section, so the frame draws it from the section's one declaration in
	 * `sections.ts` (the same words and the same icon the list on the left draws), and no pane
	 * can leave it out or call itself something else.
	 *
	 * ## The icon, in the accent
	 *
	 * The section's own icon, the one the lit row in the list is wearing, in that row's accent ink:
	 * the title answers "you are here" with the same mark the list does. The WORDS stay in full
	 * ink. Accent on words means a link in this app, and a title in accent would also compete with
	 * the lit row it is meant to agree with.
	 *
	 * A sub-page has no icon of its own. It is one level down inside a section, the way back above
	 * it names that section, and giving it the section's icon would say it is the section.
	 *
	 * `settings-headings` (scripts/check_settings_headings.js) names this file as the one place a
	 * settings screen writes an `h1`.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import PathCopy from './PathCopy.svelte';

	interface Props {
		label: string;
		icon?: IconName;
	}

	let { label, icon }: Props = $props();
</script>

<h1 class="settings-title path-host">
	<PathCopy />
	{#if icon}<Icon name={icon} size={20} />{/if}
	{label}
</h1>

<style>
	/* The display size, set once for every section there is. One declaration here rather than
	   one per pane: a pane asking for a `--text-h1` that is not a token has its whole `font:`
	   declaration dropped and its title drawn at body size with nothing warning. The same height
	   and gap as a page's own title (`PageHeader`), so a section in Settings and a screen in the
	   app are named the same way. */
	.settings-title {
		position: relative;
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0 0 var(--space-4);
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		color: var(--sift-ink);
	}

	/* The lit row's ink: `SettingsShell` dresses `.item.active` with the same token. */
	.settings-title :global(.icon) {
		color: var(--sift-accent-text);
	}
	/* The marker the copy press stands against: PathCopy places itself in the gutter of whatever
	   carries this class, so the class itself is what makes that place. */
	.path-host {
		position: relative;
	}
</style>
