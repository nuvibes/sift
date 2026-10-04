<script lang="ts">
	/*
	 * The Downloads page's Options: the door every entity page wears in its header, holding what
	 * this page offers besides pasting a link.
	 *
	 * ## One door, not six controls
	 *
	 * The page's three doors (Edit cookies, Open settings, Start or join a swap) and the paste's
	 * two choices (the switch and Download folder) would stand as five controls between the title
	 * and the list, most of them pressed rarely. They are rows behind one press, the shape a
	 * person's or a Site's page has, so the paste box and the queue start right under the title.
	 * The rows are the menu's own (`MenuButton` and `ContextMenuItem`, the door and the rows every
	 * Options menu is made of), never a panel dressed to look like one.
	 *
	 * ## The switch is a row that holds its value
	 *
	 * A checkable row, the way the menu holds an on or off answer everywhere (the Jobs screen's
	 * Compact rows): the tick says which way it is set, and pressing the row flips it and leaves
	 * the menu open, so the tick is seen to move. `aria-checked` carries the state.
	 *
	 * ## The page decides for THIS paste; Settings decides the default for every paste
	 *
	 * The switch starts from the setting of the same name, a flip changes only what is sent with
	 * the next paste (`PasteChoices` in `queue.svelte.ts`), and the stored default is changed in
	 * Settings, Downloads, and nowhere else. Nothing here writes a setting. Otherwise turning the
	 * skip off to fetch one link again would turn it off for every later download too.
	 *
	 * The words are the server's own label for the setting, read from the answer Settings draws
	 * from: two wordings of one question would be two questions to a reader.
	 *
	 * A switch nobody has touched FOLLOWS its default: a change made in Settings while this page is
	 * open is picked up on the next read. One somebody flipped is theirs until they leave the page,
	 * exactly as the folder chosen under Download folder is.
	 *
	 * ## Download folder opens onto the Add button's list
	 *
	 * The same folders from `$lib/library/destinations.svelte` that Add offers, the default named at
	 * the top, as rows out to the side with the chosen one ticked. The row's own small line names
	 * the folder the next download goes to, so it is read without opening anything.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { ContextMenuGroup, ContextMenuItem, MenuButton } from '$lib/components/common';
	import { Destinations } from '$lib/library/destinations.svelte';
	import { recallInterfaceState } from '$lib/shell/interface-state.svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { fetchSettings, type SettingEntry } from '$lib/settings-ui/settings';
	import { openSettings } from '$lib/settings-ui/settings-view';

	/** The switch's setting. The key is the server's. */
	const REMEMBER = 'download.remember';

	interface Props {
		/** Where the next download goes: '' for the default folder, else a folder id. Bound, because
		 *  the page sends it with the submit; nothing here is written anywhere. */
		dest?: string;
		/** Whether the next paste skips a link already downloaded. Null until the default is read,
		 *  which sends nothing and leaves the answer to the setting. Bound, for the same reason. */
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

	/** The setting as the server describes it: the label, and the default the switch starts
	 *  from. Absent until the read lands. */
	let entry = $state<SettingEntry | null>(null);

	/** Whether somebody flipped the switch on this page. Then it stops following the default. */
	let flipped = false;

	/** The folders a download can go to: the Add button's list, from the one module both read. */
	const destinations = new Destinations();

	whenChanged(settingChanges, () => void readSettings());

	onMount(() => {
		void readSettings();
		void destinations.load();
		// Which folders were used last decides the order they are listed in, as it does in Add.
		void recallInterfaceState();
	});

	/* One read of the settings, the same one Settings, Downloads draws from. The label and the
	   starting value both come from it, so neither is a copy kept here. */
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

<!-- The door every entity page wears, worded as theirs is. How many Sites are asking for cookies
     is said on the Edit cookies row, and on the rail's Downloads item, which is where it is seen
     from every screen. -->
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
						onselect={() => (dest = option.value)}
					/>
				{/each}
			</ContextMenuItem>
		</ContextMenuGroup>
	{/if}
</MenuButton>
