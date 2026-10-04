<script lang="ts">
	/*
	 * Why this is hidden, and the way back out, and nothing about sharing.
	 *
	 * Not the sharing panel: somebody pressing the crossed-out eye is asking what is keeping this
	 * off their own screen, not who else may see it. A share is about who may; hiding is about what
	 * you want on your own screen, withheld from you and nobody else, and no share overrides it.
	 * One glyph each, one panel each. No sharing state is shown here at all; the other glyph is a
	 * few pixels away.
	 *
	 * The list can be empty while the mark is drawn. The names in it are the concealed things, so
	 * the server answers with nothing while the vault is shut; naming them would be the vault
	 * handing out its contents. An empty answer is drawn as "unlock Hidden to see what is doing
	 * this" rather than "nothing is".
	 */
	import { Button, Modal } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import { libraryChanges } from '$lib/library/changes.svelte';
	import { HIDDEN_VERB } from '$lib/library/sharing-marks';
	import { loadCounts } from '$lib/entity/related.svelte';
	import {
		NOUNS,
		fetchVaultSources,
		hiddenLabel,
		includesSitesWithin,
		unhide,
		type ShareTarget,
		type VaultSource
	} from '$lib/library/sharing';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { vault } from '$lib/shell/vault.svelte';

	interface Props {
		open?: boolean;
		/** What the panel is about. One thing: "why is THIS hidden" has no plural reading. */
		target: ShareTarget | null;
	}

	let { open = $bindable(false), target }: Props = $props();

	let sources = $state<VaultSource[]>([]);
	let loading = $state(false);
	let failed = $state(false);
	/** Which line is being acted on, so only that one goes busy. */
	let taking = $state<string | null>(null);

	/*
	 * Asked when the panel opens, and again whenever the vault moves.
	 *
	 * The vault is the thing that decides whether this question has an answer at all, so a panel left
	 * open across an unlock would otherwise go on saying "unlock Hidden to see" over a vault that is
	 * now open. `generation` is the counter every other screen re-reads on.
	 */
	$effect(() => {
		void vault.generation;
		const asking = open ? target : null;
		if (!asking) {
			sources = [];
			failed = false;
			return;
		}
		let current = true;
		loading = true;
		failed = false;
		void fetchVaultSources(asking)
			.then((found) => {
				if (current) sources = found;
			})
			.catch(() => {
				if (current) failed = true;
			})
			.finally(() => {
				if (current) loading = false;
			});
		return () => {
			current = false;
		};
	});

	/*
	 * Take one of the named things back out, from the line that named it.
	 *
	 * Written immediately rather than staged behind an Apply. There is nothing to weigh it against:
	 * it is not a grant and nothing else on this panel is pending, so staging it would leave the
	 * line describing a state that is not true yet.
	 */
	async function takeOut(source: VaultSource) {
		taking = source.source_id ?? 'here';
		try {
			await unhide(source);
			sources = sources.filter((each) => each.source_id !== source.source_id);
			// Whatever came back out is on a screen again, and if this was the last thing hiding it,
			// so is the thing this panel is about.
			libraryChanges.changed();
			if (sources.length === 0) open = false;
		} catch {
			toasts.show("That couldn't be unhidden", { tone: 'error' });
		} finally {
			taking = null;
		}
	}

	const noun = $derived(NOUNS[target?.type ?? 'item']?.[0] ?? 'item');

	/*
	 * The Sites within the site this panel is about, so it can say how far the concealment goes.
	 *
	 * The same sentence the sharing sheet says, from the same place, because it is the same fact: a
	 * network holds Sites and the files are filed under them, so hiding a network takes all of
	 * them off this screen while the heading names only the network. Read from `sites_within`, the
	 * page's own "Sites within": `sites` is the Sites a set of files came from, and for a Site it is
	 * null, which would draw no sentence at all. Its own effect, so a count that
	 * cannot be fetched cannot stop the panel drawing what is concealing something.
	 */
	let within = $state(0);

	$effect(() => {
		const only = open && target?.type === 'site' ? target.id : null;
		if (!only) {
			within = 0;
			return;
		}
		let current = true;
		void loadCounts('site', only).then((counts) => {
			if (current) within = counts.sites_within ?? 0;
		});
		return () => {
			current = false;
		};
	});
</script>

<!-- A filename is not a sentence and can be longer than this sheet is wide, so the subject wraps and
     breaks wherever it has to. `sheetClass` is what carries that rule down: the description is drawn
     inside the Modal, so a scoped selector here reaches nothing and the class has to be handed in,
     or a long filename runs out of the panel. -->
<Modal bind:open title="Hidden" description={target?.label ?? ''} sheetClass="hidden-sheet">
	<!--
		One sentence saying what hiding IS, above the list. It is the sentence people are missing when
		they press this: the commonest reading of a crossed-out eye is "somebody else cannot see this",
		and it means the opposite.
	-->
	<p class="what">
		Hidden is about your own screen. Nobody else is affected, and no share you hold overrides it.
	</p>

	<!-- ...and how far it goes, when the thing is a network. See `includesSitesWithin`. -->
	{#if includesSitesWithin(within)}
		<p class="reaches">
			<Icon name="hub" size={16} />
			<span>{includesSitesWithin(within)}</span>
		</p>
	{/if}

	{#if failed}
		<p class="note" role="alert">What is hiding this couldn't be read.</p>
	{:else if loading}
		<p class="note">Looking&hellip;</p>
	{:else if sources.length === 0}
		<!--
			Two different facts wearing one empty list, and only the vault can tell them apart. With
			Hidden shut the server withholds these names, because the names ARE the concealed things,
			so an empty answer there means "not telling you", never "nothing".
		-->
		<p class="note">
			{vault.unlocked
				? `Nothing you hid is concealing this ${noun}.`
				: `Unlock Hidden to see what is concealing this ${noun}.`}
		</p>
	{:else}
		<ul class="by">
			{#each sources as source, at (`${at}:${source.source_type}:${source.source_id ?? ''}`)}
				{@const words = hiddenLabel(source, target?.type ?? 'item')}
				<li>
					<!-- Solid where the switch is on this very thing (change it here) and hollow where
					     it is on something above, which is where to go. The same fill rule the marks use. -->
					<Icon name="visibility_off" size={16} filled={source.here} />
					<!-- The same three pieces the sharing sheet's own hidden block is built from, in the
					     same order and at the same weights: the status word carries the colour, the rest
					     of the sentence does not, and the name is ink rather than bold. Pieces, not one
					     concatenation, so the name keeps its space and the weight every other line uses. -->
					<span class="said">
						<span class="verb">{HIDDEN_VERB}</span>
						{words.lead}
						{#if words.name}<span class="named">{words.name}</span>{/if}
					</span>
					<Button
						size="small"
						icon="visibility"
						busy={taking === (source.source_id ?? 'here')}
						onclick={() => void takeOut(source)}
					>
						Unhide
					</Button>
				</li>
			{/each}
		</ul>
	{/if}
</Modal>

<style>
	.what {
		margin: 0 0 var(--space-3);
		color: var(--sift-ink-2);
		font: var(--text-body);
		max-width: 52ch;
	}

	/* The same line the sharing sheet draws, at the same weight, because it is the same fact. */
	.reaches {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0 0 var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.note {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body);
	}

	.by {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.by li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		/* The same face and the same ink as the sharing sheet's own hidden block, so one kind of line
		   looks like itself wherever it is drawn. */
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* The sentence takes what is left, so the button stays against the right edge however long a name
	   is, and it WRAPS, at the word, rather than pushing the button out of the panel. */
	.said {
		display: inline-flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 0.25em;
		flex: 1;
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}

	/* The status word wears the colour and the rest of the sentence does not, which is the split every
	   mark and every sharing line in the app uses. */
	.verb {
		color: var(--sift-ink);
	}

	/* The weight goes on the NAME, not the verb. Every line in this block starts with the same
	   handful of words, so bolding them emphasises the part that never varies and leaves the one
	   specific fact (which person, which folder) reading as an aside. */
	.named {
		font-weight: 600;
		color: var(--sift-ink);
	}
</style>
