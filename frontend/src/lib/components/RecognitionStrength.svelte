<script lang="ts" module>
	/** Where a person's waiting faces came from while Sift knows them only by starter pictures. */
	export function startersSay(boxes: readonly string[]): string {
		return `Found with the starter pictures from ${boxes.length > 0 ? boxes.join(' and ') : 'a stash-box'}.`;
	}
</script>

<script lang="ts">
	/*
	 * How reliably Sift recognizes one person, and what it rests on: the server's rate and verdict.
	 * Absent where recognition is off or has never named anybody.
	 */
	import {
		recognitionOf,
		referenceVerdict,
		type ReferenceStrengths,
		type Strength
	} from '$lib/people/faces.svelte';
	import { untrack } from 'svelte';
	import type { components } from '$lib/api/schema';
	import { primedOr, readingAbout } from '$lib/entity/subject.svelte';
	import { Meter, Tooltip } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		personId: string;
		name?: string | null;
		/** Bump to re-read: agreeing below moves this number. */
		refresh?: number;
		/** A wall's reading of every person, so twenty-four cards make no request each. */
		strengths?: ReferenceStrengths | null;
		/**
		 * Told the stash-boxes behind STARTER pictures: they are never counted, so the page says
		 * them apart.
		 */
		onstarters?: (boxes: readonly string[] | null) => void;
	}

	let { personId, name = null, refresh = 0, strengths = null, onstarters }: Props = $props();

	type Basis = components['schemas']['StrengthBasis'];
	type Reading = Strength & { rate?: number | null; basis?: Basis };
	type Wall = ReferenceStrengths & {
		rates?: Record<string, number>;
		basis?: Record<string, Basis>;
	};

	const handed = $derived(strengths !== null);

	/* Kept on screen while re-read (`readingAbout`), or the block would flash on every write. */
	const reading = readingAbout<Reading | null>(
		() => personId,
		async (id) => (handed ? null : await primedOr('recognition', id, () => recognitionOf(id))),
		null,
		() => refresh
	);
	/* The verdict token is the server's on both paths. */
	const wall = $derived(strengths as Wall | null);
	const fromWall = $derived<Reading | null>(
		wall === null
			? null
			: {
					references: wall.people[personId] ?? 0,
					starters: 0,
					starters_from: [],
					starters_retired: 0,
					target: wall.target,
					floor: wall.floor,
					strong: wall.strong,
					rate: wall.rates?.[personId] ?? null,
					basis: wall.basis?.[personId],
					fraction: wall.rates?.[personId] ?? 0,
					verdict: referenceVerdict(personId, wall)
				}
	);
	const strength = $derived(fromWall ?? reading.value);

	$effect(() => {
		const now = strength;
		const boxes = now && (now.starters ?? 0) > 0 ? (now.starters_from ?? []) : null;
		untrack(() => onstarters?.(boxes));
	});

	/* The server's verdict in words; "them" throughout, since Sift is told nobody's pronouns. */
	function wordsFor(first: string): Record<string, string> {
		return {
			none: `Sift can't identify ${first} yet`,
			few: `Sift needs a few more faces of ${first}`,
			unseen: `Sift hasn't found ${first} in your files yet`,
			weak: `Sift can't identify ${first} reliably yet`,
			fair: `Sift can now identify ${first} reasonably well`,
			good: `Sift identifies ${first} reliably`,
			strong: `Sift identifies ${first} reliably`
		};
	}
	const first = $derived(name?.trim().split(/\s+/)[0] || 'them');
	const WORDS = $derived(wordsFor(first));

	function many(count: number, one: string, more: string): string {
		return `${counted(count)} ${count === 1 ? one : more}`;
	}

	function basisOf(now: Reading): string {
		const basis = now.basis;
		if (!basis) return `From ${many(now.references, 'picture', 'pictures')}.`;
		const parts = [
			basis.imported > 0
				? many(basis.imported, 'imported fingerprint', 'imported fingerprints')
				: '',
			basis.confirmed > 0 ? many(basis.confirmed, 'face you confirmed', 'faces you confirmed') : '',
			basis.learned > 0 ? many(basis.learned, 'face Sift recognized', 'faces Sift recognized') : ''
		].filter(Boolean);
		const said =
			parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}` : parts[0];
		const turned =
			basis.turned > 0
				? ` ${many(basis.turned, 'face', 'faces')} facing away kept for them, never matched.`
				: '';
		return `From ${said ?? many(now.references, 'picture', 'pictures')}.${turned}`;
	}

	function rateSaid(now: Reading): string {
		const floor = `Drawn once Sift has ${many(now.floor, 'picture', 'pictures')} of them.`;
		if (now.rate === null || now.rate === undefined) return floor;
		return `${Math.round(now.rate * 100)}% of the faces Sift named as them were right, by your answers. ${floor}`;
	}
</script>

{#if strength && strength.strong > 0 && !(strength.verdict === 'none' && strength.references === 0)}
	<div class="block">
		<!-- The shared meter: a reading, not a task. -->
		<Tooltip label={rateSaid(strength)} wide stretch>
			<Meter value={strength.rate ?? 0} max={1} label="How reliably Sift can recognize this person">
				{#snippet fill(fraction)}
					<!--
					The band is the server's `verdict`; each band its own short gradient, the length
					clipped, not scaled.
					-->
					<div
						class="spectrum"
						data-band={strength.verdict}
						style:clip-path={`inset(0 ${(1 - fraction) * 100}% 0 0)`}
					></div>
				{/snippet}
			</Meter>
		</Tooltip>
		<div class="said">
			<p class="count" data-band={strength.verdict}>
				<Icon name="person_check" size={16} /><span class="words"
					>{WORDS[strength.verdict] ?? WORDS.none}</span
				>
			</p>
			<p class="basis">{basisOf(strength)}</p>
		</div>
	</div>
{/if}

<style>
	/* A bar and two lines; the why is in Settings. */
	.block {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.said {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	/*
	 * The one place red is not a failure: a scale from bad to good, in the semantic tokens. The red
	 * is `--sift-bad-text`, which clears 3:1 on every base's track. No attribute is the floor band.
	 */
	.spectrum {
		--band-orange: color-mix(in oklch, var(--sift-bad-text), var(--sift-warn));
		block-size: 100%;
		border-radius: 999px;
		background: linear-gradient(90deg, var(--sift-bad-text), var(--band-orange));
		transition: clip-path var(--dur-base) var(--ease);
	}

	.spectrum[data-band='fair'] {
		background: linear-gradient(90deg, var(--band-orange), var(--sift-warn));
	}

	.spectrum[data-band='good'] {
		background: linear-gradient(90deg, var(--sift-warn), var(--sift-ok));
	}

	/*
	 * Towards `--sift-ok-bg`, which keeps the hue (OKLCH would turn towards teal); 72% clears 3:1.
	 */
	.spectrum[data-band='strong'] {
		background: linear-gradient(
			90deg,
			color-mix(in oklch, var(--sift-ok) 72%, var(--sift-ok-bg)),
			var(--sift-ok)
		);
	}

	.count {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.count {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.words {
		min-inline-size: 0;
	}

	.basis {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.count {
		--band-orange: color-mix(in oklch, var(--sift-bad-text), var(--sift-warn));
	}

	.count > :global(.icon) {
		color: var(--sift-ink-3);
	}

	.count[data-band='fair'] > :global(.icon) {
		color: var(--band-orange);
	}

	.count[data-band='good'] > :global(.icon) {
		color: var(--sift-warn);
	}

	.count[data-band='strong'] > :global(.icon) {
		color: var(--sift-ok);
	}
</style>
