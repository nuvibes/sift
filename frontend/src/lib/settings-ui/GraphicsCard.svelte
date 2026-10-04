<script lang="ts">
	/* Turning the graphics card on.
	 *
	 * A SCREEN THAT ONLY SAYS NO IS NOT AN ANSWER. A GPU offered on an installed Sift that cannot
	 * use it, refused with a correct sentence that has no next step in it, reads as a fault in
	 * the machine, and people go looking for a driver they already have.
	 *
	 * There are five states and they must be five, because the action differs and so does whose
	 * decision each one is:
	 *
	 *   no card              nothing to offer, and saying so is the whole answer
	 *   already capable      this machine's own runtime drives a card. Sift offers NOTHING, because
	 *                        which version is installed there is somebody else's decision
	 *   card, not installed  a download, with its size named BEFORE anybody agrees to it
	 *   downloading          a bar with a percentage, and a note that it can be left
	 *   installed            and then, separately, whether it actually WORKS, which is not the
	 *                        same question and cannot be assumed from the first
	 *
	 * The last split is the one worth defending. A runtime lists every device it was compiled for
	 * whether or not the hardware behind it can be reached, so "installed" is not evidence. The
	 * test button loads a real model onto the card in a process of its own and reports what
	 * happened.
	 *
	 * Drawn with `SettingGroup`, `ActionRow` and `FactRow` like every other block in Settings,
	 * and with no geometry of its own. A panel that drew its own card would sit at a different
	 * distance from the edge than the rows above and below it, a visible seam in Settings.
	 */
	import { onMount } from 'svelte';
	import { ProgressBar } from '$lib/components/common';
	import { accelWatch } from '$lib/jobs/accelerator.svelte';
	import ActionRow from './ActionRow.svelte';
	import FactRow from './FactRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY } from './GraphicsCard.search';
	import type { GraphicsCardState } from './graphics-card-state.svelte';

	/* The reading, shared with the danger row at the foot of the pane (`GraphicsCardRemove`): the
	   way out of a pane is its last group, so deleting the support is drawn there, not here. */
	let { card }: { card: GraphicsCardState } = $props();

	onMount(() => {
		void card.read();
		// A download started here and then left runs on. Coming back to this screen has to join it
		// rather than show the button again.
		void accelWatch.resume();
	});

	/* The download ended, however it ended, so what this says is now out of date. Read again rather
	   than inferred from the outcome: whether it is installed is the server's answer, not ours. */
	$effect(() => {
		if (accelWatch.outcome !== null) void card.read();
	});

	const accel = $derived(card.accel);

	/** Gigabytes, to one place. The number is the decision: over a gigabyte is not something to
	 *  start on somebody's connection and mention afterwards. */
	const size = $derived(
		accel ? `${(accel.download_bytes / 1_000_000_000).toFixed(1)} GB` : COPY.install.defaultSize
	);
	/** The room it needs while it installs, which is more than twice what comes down: the wheels
	 *  and what they unpack to are both on the disk until the last one is through. The server
	 *  refuses the download without it, and the same figure is said here first. */
	const room = $derived(
		accel && accel.peak_bytes > 0
			? COPY.install.room((accel.peak_bytes / 1_000_000_000).toFixed(1))
			: COPY.install.defaultRoom
	);

	/** What the block says under its heading, which is a different sentence in each state. */
	const help = $derived.by(() => {
		if (accel === null) return undefined;
		if (!accel.supported) {
			return COPY.unsupported;
		}
		if (accel.already_capable) {
			return COPY.alreadyCapable;
		}
		if (accel.installed) {
			return COPY.ready;
		}
		/* IT LEADS WITH THE DRIVER, because that is the question everybody asks: the card is named
		   right above this line, the driver is plainly working, and the screen still wants a download.
		   Saying what is actually missing is the difference between an offer and a puzzle. */
		return COPY.missing;
	});
</script>

{#if accel}
	<SettingGroup id="performance.graphics_card" heading={COPY.name} {help} />

	{#if accel.card}
		<FactRow label={COPY.gpu} fact={accel.card} />
	{/if}

	{#if accelWatch.running}
		<!-- The bar is the one thing here with no row shape, because it is not a row: it is the state
		     of something already happening, and it spans the block. -->
		<ProgressBar value={accelWatch.fraction * 100} label={COPY.downloading} />
		<FactRow
			label={accelWatch.waiting ? COPY.waiting : COPY.downloadingNow}
			fact={accelWatch.status}
			help={COPY.leave}
		/>
	{:else if accel.installed}
		{#if accel.restart_needed}
			<!-- Real, correct, and cannot take effect until Sift is opened again: two builds of
			     one library cannot both be loaded, and the processor build is already in this
			     process. Saying so is the point: the alternative is a screen claiming the
			     card is in use while every job goes to the processor.

			     AND IT OFFERS TO DO IT. A screen that says "close Sift and open it again" and
			     then leaves somebody to find the taskbar is asking them to do the machine's
			     job. In a browser there is no such button, because the application to restart
			     is on another computer. -->
			<ActionRow
				label={COPY.restart.label}
				help={COPY.restart.help}
				action={card.restarting ? COPY.restart.restarting : COPY.restart.action}
				icon="sync"
				busy={card.restarting}
				onclick={() => void card.restart()}
			/>
			{#if card.restartProblem}
				<FactRow label={COPY.restart.failed} fact={card.restartProblem} />
			{/if}
		{/if}
		<!-- NOT PRESSABLE UNTIL THE RESTART HAS HAPPENED, and that is not caution. The test is
		     honest about a session that has already loaded the processor version: it refuses,
		     correctly, and says the card was refused. Read before the restart, that sentence is
		     indistinguishable from a card that does not work, so the answer it gives is worse
		     than no answer. -->
		<ActionRow
			label={COPY.test.label}
			help={accel.restart_needed ? COPY.test.notYet : COPY.test.help}
			action={card.testing ? COPY.test.testing : COPY.test.action}
			icon="readiness_score"
			disabled={card.testing || accel.restart_needed}
			note={card.tested ? (card.tested.works ? COPY.test.working : COPY.test.refused) : undefined}
			onclick={() => void card.test()}
		/>
		{#if card.tested && !card.tested.works}
			<FactRow label={COPY.test.said} fact={card.tested.problem ?? COPY.test.noModel} />
		{/if}
	{:else if accel.supported && !accel.already_capable}
		<ActionRow
			label={COPY.install.label}
			help={COPY.install.help}
			action={COPY.install.action}
			icon="download"
			note={COPY.install.note(size, room)}
			onclick={() => void card.install()}
		/>
	{/if}

	{#if accelWatch.outcome && !accelWatch.running}
		<FactRow label={COPY.lastDownload} fact={accelWatch.outcome} />
	{/if}
{/if}
