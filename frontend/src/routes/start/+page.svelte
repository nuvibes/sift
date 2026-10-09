<script lang="ts">
	/* The first question a new copy of Sift asks: does this computer hold the library, or look
	 * at one somebody else is holding? */
	import { DoorCard, Note, Problem } from '$lib/components/common';
	import ChoiceCard from '$lib/components/common/ChoiceCard.svelte';
	import { bridge } from '$lib/bridge';

	const inTheApp = bridge.canSetUp();

	let busy = $state<'standalone' | 'client' | null>(null);
	let problem = $state<string | null>(null);

	async function choose(mode: 'standalone' | 'client') {
		if (busy !== null) return;
		busy = mode;
		problem = null;
		const settled = await bridge.chooseMode(mode);
		/* Nothing is put back on `ok`. The shell is already loading the next screen, and clearing the
		 * busy state would flash both cards live again on a window that is about to go. */
		if (settled.ok) return;
		problem = settled.refusal;
		busy = null;
	}
</script>

<svelte:head>
	<title>Sift</title>
</svelte:head>

{#if inTheApp}
	<DoorCard heading="How Sift runs">
		<Problem message={problem} />

		<div class="choices">
			<ChoiceCard
				role="button"
				name="This device"
				aside="(most common)"
				note="Keeps your library on this device. Turn on Network sharing in Settings to open it from other devices on your network too."
				footnote="(Choose this if you are setting up Sift for the first time.)"
				chosen={busy === 'standalone'}
				onchoose={() => void choose('standalone')}
			/>
			<ChoiceCard
				role="button"
				name="Another device"
				note="Opens a Sift library that runs on a different device, in this app."
				footnote="(Only if Sift is already running on another device.)"
				chosen={busy === 'client'}
				onchoose={() => void choose('client')}
			/>
		</div>

		<!-- `inline` because it is one short line under the choice it belongs to: the mark stacked
		     above it would spend a whole row of the screen on a 16-pixel glyph. -->
		<Note>You can change this later with Run setup again, in Settings &gt; General.</Note>
	</DoorCard>
{:else}
	<DoorCard
		heading="This screen belongs to the Sift app"
		explain="It's where the desktop app asks whether this device holds the library or opens one on another device."
	>
		<Note>
			You are looking at a Sift that's already running, so this question has been answered. If you
			meant to set up a different copy, do it from the Sift app on that device.
		</Note>
	</DoorCard>
{/if}

<style>
	.choices {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
</style>
