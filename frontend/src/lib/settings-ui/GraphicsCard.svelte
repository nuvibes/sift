<script lang="ts">
	/* Turning the graphics card on. A SCREEN THAT ONLY SAYS NO IS NOT AN ANSWER. */
	import { onMount } from 'svelte';
	import { ProgressBar } from '$lib/components/common';
	import { accelWatch } from '$lib/jobs/accelerator.svelte';
	import ActionRow from './ActionRow.svelte';
	import FactRow from './FactRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY } from './GraphicsCard.search';
	import type { GraphicsCardState } from './graphics-card-state.svelte';

	/* The reading, shared with the danger row at the foot of the pane (`GraphicsCardRemove`):
	   the way out of a pane is its last group, so deleting the support is drawn there, not here. */
	let { card }: { card: GraphicsCardState } = $props();

	onMount(() => {
		void card.read();
		// A download started here and then left runs on.
		void accelWatch.resume();
	});

	/* The download ended, however it ended, so what this says is now out of date. */
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
	 * and what they unpack to are both on the disk until the last one is through. */
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
		/* IT LEADS WITH THE DRIVER, because that is the question everybody asks: the card is
		   named right above this line, the driver is plainly working, and the screen still wants
		   a download. */
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
		<ActionRow
			label={COPY.test.label}
			help={COPY.test.help}
			action={card.testing ? COPY.test.testing : COPY.test.action}
			icon="readiness_score"
			disabled={card.testing}
			note={card.tested ? (card.tested.works ? COPY.test.passed : COPY.test.refused) : undefined}
			onclick={() => void card.test()}
		/>
		{#if card.tested && !card.tested.works}
			<FactRow label={COPY.test.said} fact={card.tested.problem ?? COPY.test.noModel} stacked />
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
		<FactRow label={COPY.lastDownload} fact={accelWatch.outcome} stacked />
	{/if}
{/if}
