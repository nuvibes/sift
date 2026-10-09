<script lang="ts">
	/* The Downloads page's Options: the door every entity page wears in its header, holding what
	 * this page offers besides pasting a link. */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { ContextMenuGroup, ContextMenuItem, MenuButton } from '$lib/components/common';
	import { Destinations } from '$lib/library/destinations.svelte';
	import { noteFolderUse, recallInterfaceState } from '$lib/shell/interface-state.svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { fetchSettings, type SettingEntry } from '$lib/settings-ui/settings';
	import { openSettings } from '$lib/settings-ui/settings-view';

	/** The switch's setting. The key is the server's. */
	const REMEMBER = 'download.remember';

	interface Props {
		/** The folder being made the default while that save is out, else '' (the default). */
		dest?: string;
		/** Whether the next paste skips a link already downloaded. */
		remember?: boolean | null;
		/** Whether the menu is open. Bound, so the paste box can open it when it asks for a folder. */
		open?: boolean;
		/** How many Sites are asking for cookies: said on the Edit cookies row. */
		cookiesWanted?: number;
		/** Open the cookies sheet, which is the page's. */
		oncookies: () => void;
	}

	let {
		dest = $bindable(''),
		remember = $bindable(null),
		open = $bindable(false),
		cookiesWanted = 0,
		oncookies
	}: Props = $props();

	/** The setting as the server describes it: the label, and the default the switch starts from. */
	let entry = $state<SettingEntry | null>(null);

	/** Whether somebody flipped the switch on this page. Then it stops following the default. */
	let flipped = false;

	/** The folders a download can go to: the Add button's list, from the one module both read. */
	const destinations = new Destinations();
	destinations.follow();

	/* Held on the folder until its save answers, so the paste box stops asking immediately. */
	async function chooseFolder(folderId: string) {
		if (!folderId) return;
		dest = folderId;
		noteFolderUse(folderId);
		await destinations.makeDefault(folderId);
		dest = '';
	}

	whenChanged(settingChanges, () => void readSettings());

	onMount(() => {
		void readSettings();
		void destinations.load();
		// Which folders were used last decides the order they are listed in, as it does in Add.
		void recallInterfaceState();
	});

	/* One read of the settings, the same one Settings, Downloads draws from. */
	async function readSettings() {
		try {
			const every = (await fetchSettings()).flatMap((section) => section.settings ?? []);
			const found = every.find((one) => one.key === REMEMBER);
			entry = found !== undefined && typeof found.value === 'boolean' ? found : null;
			if (entry !== null && !flipped) remember = entry.value === true;
		} catch {
			// Nothing drawn rather than something invented: the rows need the server's words.
		}
	}

	/** A flip is this paste's answer. It is sent with the paste and written nowhere else. */
	function flip() {
		flipped = true;
		remember = remember !== true;
	}

	/** The folder the next download goes to, as the chooser names it. */
	const going = $derived(
		destinations.options.find((one) => one.value === dest)?.label ??
			destinations.defaultOption.label
	);

	/** The cookies row's small line: how many are asking, where any are. */
	const asking = $derived(
		cookiesWanted === 1
			? '1 is waiting for cookies'
			: cookiesWanted > 1
				? `${cookiesWanted.toLocaleString()} are waiting for cookies`
				: undefined
	);
</script>

<!-- The door every entity page wears, worded as theirs is. -->
<MenuButton label="Options for Downloads" words="Options" bind:open>
	<ContextMenuGroup>
		<ContextMenuItem label="Edit cookies" icon="edit" note={asking} onselect={oncookies} />
		<ContextMenuItem
			label="Open settings"
			icon="settings"
			onselect={() => openSettings('downloads')}
		/>
		{#if session.isAdmin}
			<!-- The way in to a swap with another Sift. Here because a swap is the other way files
			     arrive from outside, and a swap has no rail item of its own. -->
			<ContextMenuItem
				label="Start or join a swap"
				icon="swap_horiz"
				onselect={() => void goto('/swap')}
			/>
		{/if}
	</ContextMenuGroup>
	{#if entry !== null}
		<ContextMenuGroup>
			<ContextMenuItem
				label={entry.label ?? entry.key}
				checked={remember === true}
				onselect={flip}
			/>
		</ContextMenuGroup>
	{/if}
	<!-- Drawn once there are folders to choose from, as Add draws it; before that the default is
	     where the next download goes and there is nothing to pick. -->
	{#if destinations.folders.length > 0}
		<ContextMenuGroup>
			<ContextMenuItem label="Download folder" icon="folder" note={going}>
				{#each destinations.options as option (option.value)}
					<ContextMenuItem
						label={option.label}
						checked={dest === option.value}
						oneOf
						onselect={() => void chooseFolder(option.value)}
					/>
				{/each}
			</ContextMenuItem>
		</ContextMenuGroup>
	{/if}
</MenuButton>
