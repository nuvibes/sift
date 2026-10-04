<script lang="ts">
	/*
	 * More: who is signed in, every section of Settings, and Sign out at the foot, as a list at its
	 * own address.
	 *
	 * The phone's way into Settings. Each section opens over this list, so closing it or pressing
	 * Back lands here again, where the next section is one press away. A tab that opened Settings
	 * itself would put its first section up with nothing behind it, and every way back would lead
	 * to that same section.
	 *
	 * The sections are the ones the Settings list itself draws, from the same declaration and in the
	 * same groups, so the two lists cannot disagree about what Settings holds. A sub-page is one
	 * press inside its section, as it is on a desktop.
	 */
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import DestinationList, { type Destination } from '$lib/components/shell/DestinationList.svelte';
	import FullAmountRow from '$lib/components/shell/FullAmountRow.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { SETTINGS_GROUPS } from '$lib/settings-ui/sections';
	import ActionRow from '$lib/settings-ui/ActionRow.svelte';
	import { SIGN_OUT, WHO } from '$lib/settings-ui/Profile.search';
	import { openSettingsInstead } from '$lib/settings-ui/settings-view';
	import { signOut } from '$lib/shell/sign-out';

	/* The groups a guest can open, for the reason the Settings list gives: the server refuses the
	   rest, and a door that will not open is worse than none. */
	const groups = $derived(
		SETTINGS_GROUPS.map((group) => ({
			heading: group.heading,
			rows: group.sections
				.filter((section) => !section.admin || session.isAdmin)
				.map((section): Destination => ({
					id: section.id,
					href: `/settings/${section.id}`,
					label: section.label,
					icon: section.icon,
					open: (event) => openSettingsInstead(event, section.id)
				}))
		})).filter((group) => group.rows.length > 0)
	);

	let leaving = $state(false);

	async function leave() {
		leaving = true;
		try {
			await signOut();
		} finally {
			leaving = false;
		}
	}
</script>

<PageFrame measure>
	{#snippet header()}
		<PageHeader title="More" icon="more_vert">
			{#snippet lede()}
				{#if session.viewer}{WHO.label} <strong>{session.viewer.username}</strong>{/if}
			{/snippet}
		</PageHeader>
	{/snippet}

	<div class="section-stack">
		<!-- The rail's leaf or bolt, on the device that has no rail; nothing while it has nothing to say. -->
		<FullAmountRow />
		<SectionHeading>Settings</SectionHeading>
		{#each groups as group (group.heading)}
			<SectionHeading band>{group.heading}</SectionHeading>
			<DestinationList items={group.rows} label={group.heading} />
		{/each}

		<!-- The row Settings > Profile ends with, in the same words and in the same place, at the
		     foot: one way to sign out, drawn the same wherever it is offered, and last, where a
		     thumb reaching for a section never lands on it. -->
		<SectionHeading>{SIGN_OUT.name}</SectionHeading>
		<ActionRow
			label={SIGN_OUT.row}
			help={session.isAdmin ? SIGN_OUT.help : SIGN_OUT.helpGuest}
			action={SIGN_OUT.action}
			busy={leaving}
			onclick={() => void leave()}
		/>
	</div>
</PageFrame>
