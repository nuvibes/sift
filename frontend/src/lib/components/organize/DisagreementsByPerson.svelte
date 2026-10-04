<script lang="ts">
	/*
	 * The people the Disagreements tab is about, one row each, the most files first.
	 *
	 * A disagreement is a name a pass filed (a folder, a filename, a stash-box) on a file whose one
	 * face Sift recognized as somebody else. A pass files a whole folder at once, so its mistakes come
	 * by the hundred under one name, so a thousand files are about a handful of people. One card per
	 * file would ask the same question about the same person a thousand times; one row per person asks
	 * it once, and her faces are the wall beside it.
	 *
	 * Each row is her own picture, her name, how many of her files are here and where the name came
	 * from. Pressing a row shows her faces; the row showing wears the picked ring. The rows are the
	 * list and nothing else: which one is showing, and what showing it reads, is the panel's.
	 */
	import { coverUrl } from '$lib/entity/art';
	import { Avatar, Pressable } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import { filedFrom, type DisagreeingPerson } from '$lib/organize/disagreements';

	let {
		people,
		current,
		onpick
	}: {
		people: DisagreeingPerson[];
		/** The person whose faces are showing. */
		current: string | null;
		onpick: (personId: string) => void;
	} = $props();

	/** "1 file", "734 files": the count reads with its separators and its noun agrees. */
	function files(count: number): string {
		return count === 1 ? '1 file' : `${counted(count)} files`;
	}

	/* Where her name came from: the server's words where they name the stash-box ("Added by
	   FansDB"), else the word the pass wrote. A folder's sentence names folders and stays under her
	   heading, where it has room. */
	function filed(person: DisagreeingPerson): string {
		return person.source === 'stash_box' && person.filed ? person.filed : filedFrom(person.source);
	}
</script>

<ul class="people" aria-label="People with faces that don't look like them">
	{#each people as person (person.person_id)}
		<li>
			<Pressable
				class="person"
				feedback="wash"
				radius="md"
				picked={person.person_id === current}
				aria-current={person.person_id === current ? 'true' : undefined}
				aria-label={`${person.person_name}, ${files(person.count)}`}
				onclick={() => onpick(person.person_id)}
			>
				<span class="portrait">
					<Avatar
						src={coverUrl(`/people/${person.person_id}`)}
						name={person.person_name}
						shape="face"
						decorative
					/>
				</span>
				<span class="named">
					<span class="name">{person.person_name}</span>
					<span class="tally">
						<span class="data">{files(person.count)}</span> &middot; {filed(person)}
					</span>
				</span>
			</Pressable>
		</li>
	{/each}
</ul>

<style>
	.people {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	/* The whole row is the control. `:global` because the class is handed to `Pressable`. */
	.people :global(.person) {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		inline-size: 100%;
		padding: var(--space-2);
		text-align: start;
	}

	.portrait {
		flex: none;
		inline-size: var(--space-12);
		block-size: var(--space-12);
		border-radius: var(--radius-md);
		overflow: hidden;
	}

	.named {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.name {
		font: var(--text-body);
		color: var(--sift-ink);
		overflow-wrap: anywhere;
	}

	.tally {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.data {
		font-variant-numeric: tabular-nums;
	}
</style>
