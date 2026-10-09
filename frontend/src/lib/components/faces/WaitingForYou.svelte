<script lang="ts" module>
	import { api } from '$lib/api/client';
	import { isFinished } from '$lib/jobs/queue.svelte';
	import type { JobsPage } from '$lib/jobs/family';
	import { SvelteSet } from 'svelte/reactivity';

	const WATCH_EVERY = 2000;

	/*
	 * People whose Yes queued a re-match, so their card and page mark it until the queue holds
	 * none.
	 */
	class Rematching {
		readonly people = new SvelteSet<string>();
		#watching = false;

		after(personId: string): void {
			this.people.add(personId);
			if (!this.#watching) void this.#watch();
		}

		async #watch(): Promise<void> {
			this.#watching = true;
			try {
				while (this.people.size > 0) {
					await new Promise((done) => setTimeout(done, WATCH_EVERY));
					if (this.people.size > 0 && !(await stillMatching())) this.people.clear();
				}
			} finally {
				this.#watching = false;
			}
		}
	}

	/* A queue that cannot be read is not matching: a mark that never goes is worse than none. */
	async function stillMatching(): Promise<boolean> {
		try {
			for (const type of ['face_agree', 'face_rematch']) {
				const page = await api.get<JobsPage>('/jobs', { query: { type, limit: 5 } });
				if (page.jobs.some((one) => !isFinished(one.state))) return true;
			}
			return false;
		} catch {
			return false;
		}
	}

	export const rematching = new Rematching();

	export function agreeingWith(faces: number): string {
		return faces === 1 ? 'Agreeing with 1 face' : `Agreeing with ${faces.toLocaleString()} faces`;
	}

	export function matchingSaid(name: string | null | undefined): string {
		const first = name?.trim().split(/\s+/)[0];
		return `Sift is matching the rest of the library against ${first ? `${first}'s` : 'their'} face`;
	}
</script>

<script lang="ts">
	/*
	 * What Sift knows of one face, as three counts: confirmed, recognized by Sift, needs your
	 * input, in those words, then the files a folder filed whose face waits unnamed. Counts and
	 * ways in, one smallest page asked once; a zero is absent.
	 */
	import type { components } from '$lib/api/schema';
	import { identifiedForPerson } from '$lib/people/faces.svelte';
	import { primedOr, readingAbout } from '$lib/entity/subject.svelte';
	import { Spinner, Tooltip } from '$lib/components/common';

	interface Props {
		personId: string;
		/** Whose faces, said in the tooltips; absent, the pronoun. */
		name?: string;
		refresh?: number;
		help?: string;
	}

	let { personId, name, refresh = 0, help }: Props = $props();

	const whom = $derived(name?.trim() || 'them');
	const first = $derived(name?.trim().split(/\s+/)[0] || 'they');
	const asWhom = $derived(name?.trim() ? first : 'them');
	const learnsTip = $derived(
		name?.trim()
			? `Faces you confirmed to be ${whom}. These teach Sift what ${first} looks like.`
			: 'Faces you confirmed to be them. These teach Sift what they look like.'
	);
	const waitingTip = $derived(`Faces Sift thinks may be ${whom}, awaiting your input.`);

	const ONE = { limit: 1, offset: 0 };

	/* The three an identified card carries, off the server's own shape. */
	type Counts = Pick<components['schemas']['IdentifiedCard'], 'confirmed' | 'matched' | 'waiting'> &
		Pick<components['schemas']['AppearancePage'], 'unnamed_from_folder'>;

	const NOTHING: Counts = { confirmed: 0, matched: 0, waiting: 0, unnamed_from_folder: 0 };

	/* Kept on screen while re-read (`readingAbout`). */
	const counts = readingAbout<Counts>(
		() => personId,
		async (id) => {
			// The three are over everything this viewer may see; `total` is not one of them.
			const answer = await primedOr('identified', id, () => identifiedForPerson(id, ONE));
			return {
				confirmed: answer.confirmed,
				matched: answer.matched,
				waiting: answer.waiting,
				unnamed_from_folder: answer.unnamed_from_folder ?? 0
			};
		},
		NOTHING,
		() => refresh
	);

	const found = $derived(counts.value);

	/* Both go to THIS person's review screen, never the wall of everybody. */
	const matches = $derived(`/organize/known-people/${encodeURIComponent(personId)}?show=matched`);
	const toCheck = $derived(`/organize/known-people/${encodeURIComponent(personId)}?show=suggested`);
	/* None of the three reaches these faces, so the Files wall filtered to them. */
	const unnamed = $derived(`/browse?${new URLSearchParams({ unnamed_face: personId })}`);
	const unnamedTip = $derived(
		`Files filed under ${whom} from a folder, with a face nobody has named yet.`
	);

	const anything = $derived(
		found.confirmed > 0 || found.matched > 0 || found.waiting > 0 || found.unnamed_from_folder > 0
	);
</script>

{#if anything}
	<div class="counts">
		{#if rematching.people.has(personId)}
			<p class="one working">
				<Tooltip label={matchingSaid(name)} placement="bottom">
					<Spinner size={12} label={matchingSaid(name)} />
				</Tooltip>
			</p>
		{/if}
		{#if found.confirmed > 0}
			<!-- Confirmed has nowhere to go, so it is words with a tooltip, not a control. -->
			<p class="one">
				<Tooltip label={learnsTip} placement="bottom">
					<span class="number">You confirmed {found.confirmed.toLocaleString()} as {asWhom}</span>
				</Tooltip>
			</p>
		{/if}
		{#if found.matched > 0}
			<p class="one">
				<!-- A link: it goes somewhere. The sentence names where, not a control's words. -->
				<Tooltip
					label="Faces Sift named without asking. Agree to each one from its card."
					placement="bottom"
				>
					<a class="number" href={matches}
						>Sift recognized {found.matched.toLocaleString()} as {asWhom}</a
					>
				</Tooltip>
			</p>
		{/if}
		{#if found.waiting > 0}
			<!-- The one that asks something, with its action on the right. -->
			<div class="asks">
				<div class="row">
					<span class="words">
						<Tooltip label={waitingTip} placement="bottom">
							<!-- The singular is its own sentence; the words are the way in. -->
							<a class="number" href={toCheck}>
								{found.waiting === 1
									? '1 awaiting your input'
									: `${found.waiting.toLocaleString()} awaiting your input`}
							</a>
						</Tooltip>
					</span>
				</div>
				{#if help}
					<p class="help">{help}</p>
				{/if}
			</div>
		{/if}
		{#if found.unnamed_from_folder > 0}
			<p class="one">
				<Tooltip label={unnamedTip} placement="bottom">
					<a class="number" href={unnamed}>
						{found.unnamed_from_folder === 1
							? '1 file from the folder with a face still unnamed'
							: `${found.unnamed_from_folder.toLocaleString()} files from the folder with a face still unnamed`}
					</a>
				</Tooltip>
			</p>
		{/if}
	</div>
{/if}

<style>
	/* Three readings in the cover's column. */
	.counts {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.one {
		display: flex;
		margin: 0;
		min-inline-size: 0;
	}

	.working {
		color: var(--sift-ink-3);
	}

	/* Tabular, so three stacked numbers line up. */
	.number {
		font: var(--text-body);
		font-variant-numeric: tabular-nums;
	}

	/* On the SPAN only: a class here outranks `app.css`'s link colour. */
	span.number {
		color: var(--sift-ink);
	}

	.asks {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.words {
		display: flex;
		min-inline-size: 0;
	}

	.row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}

	/* On a phone the words take the line and Open stands under them. */
	@media (max-width: 767px) {
		.row {
			flex-wrap: wrap;
			justify-content: flex-end;
		}

		/* The words' box, or end-packing slides them off the start edge. */
		.words {
			flex-basis: 100%;
		}
	}

	.help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
