<script lang="ts">
	/*
	 * The band under a file's picture: what the file belongs to, one row per kind, each headed by its
	 * glyph, in the order the question is asked (who is in it, where it came from, what holds it, what
	 * it is called). Every chip is the same chip with its hover card; a row with nothing in it is not
	 * drawn. The lists and their removals are `FileBand`'s (`file-band.svelte`).
	 */
	/* NOT ON THE GALLERY: a piece of `AssetView`, exempt as the dynamic host. */
	import ContextMenu from '$lib/components/common/ContextMenu.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import EntityPreview, { entityPicture } from '$lib/components/EntityPreview.svelte';
	import { Chip, Empty, EntityRow, Fold } from '$lib/components/common';
	import { pageOf } from '$lib/entity/related.svelte';
	import { filingName, filingRemovalLabel, type Filing } from '$lib/library/filings';
	import { session } from '$lib/shell/session.svelte';
	import type { FileBand, Membership } from './file-band.svelte';

	interface Props {
		band: FileBand;
		/** The file showing, which the lists must have answered for before "nothing here" is said. */
		fileId: string;
	}

	let { band, fileId }: Props = $props();
</script>

<Fold section summary="Enrichment" remember="sift.file.fold.enrichment" weight={450}>
	<!-- What this file belongs to, one row per kind, each headed by its glyph. -->
	<div class="entities">
		<!-- A fold that opens onto nothing reads as a fold that failed, so a file on
	     nothing says so, once the lists have answered. -->
		{#if band.bandFor === fileId && band.people.length + band.filings.length + band.partOf.length + band.tags.length === 0}
			<Empty scope="block">
				No People, Sites, Collections, Photo Sets, tags or song on this file yet.
			</Empty>
		{/if}
		{#if band.people.length > 0}
			<!-- Who is in this, as the People screen draws them. -->
			<EntityRow icon="person" label="People">
				{#each band.people as person (person.id)}
					<div class="slot">
						<!-- Right-click is the way in that needs no small target. -->
						<ContextMenu label="{person.name} on this file" triggerClass="trigger">
							{#snippet items()}
								{#if session.isAdmin}
									<ContextMenuItem
										label="Remove person"
										icon="close"
										onselect={() => void band.detach(person.id)}
									/>
								{/if}
							{/snippet}
							<!-- The hover card; the link still goes to the person's page. -->
							<EntityPreview kind="person" id={person.id} name={person.name} cover={person}>
								{#snippet children({ props })}
									<!-- The shared chip, its cross outside the pressable body; the person's cover at the
								     address every wall uses; the preview's listeners on a wrapper. -->
									<span {...props} class="hovered">
										<Chip
											tone="quiet"
											href={pageOf('person', person.id)}
											picture={entityPicture('person', person.id, person.name, person)}
											onremove={session.isAdmin ? () => void band.detach(person.id) : undefined}
											removeLabel="Remove {person.name} from this file"
											confirm={{
												what: person.name,
												where: pageOf('person', person.id)
											}}
										>
											{person.name}
										</Chip>
									</span>
								{/snippet}
							</EntityPreview>
						</ContextMenu>
					</div>
				{/each}
			</EntityRow>
		{/if}

		{#if band.filings.length > 0}
			<!-- Where this file came from: one chip per filing, since a file can be under a site
			     twice; a deleted site has no page and no card, only its cross. -->
			{#snippet siteChip(filing: Filing, props: Record<string, unknown>)}
				<span {...props} class="hovered">
					<Chip
						tone="quiet"
						href={filing.site_id ? pageOf('site', filing.site_id) : undefined}
						picture={filing.site_id && filing.site
							? entityPicture('site', filing.site_id, filing.site, filing)
							: undefined}
						onremove={session.isAdmin ? () => void band.unfile(filing.username_id) : undefined}
						removeLabel={filingRemovalLabel(filing)}
						confirm={{
							what: filingName(filing),
							where: filing.site_id ? pageOf('site', filing.site_id) : undefined
						}}
					>
						{filingName(filing)}
					</Chip>
				</span>
			{/snippet}
			<EntityRow icon="public" label="Sites">
				{#each band.filings as filing (filing.username_id)}
					<!-- A site by its mark (`entityPicture`'s rule), its cover fields naming the address. -->
					{#if filing.site_id && filing.site}
						<EntityPreview kind="site" id={filing.site_id} name={filing.site} cover={filing}>
							{#snippet children({ props })}
								{@render siteChip(filing, props)}
							{/snippet}
						</EntityPreview>
					{:else}
						{@render siteChip(filing, {})}
					{/if}
				{/each}
			</EntityRow>
		{/if}

		<!-- What the file is part of, as the flyouts draw it, written once for the kinds. -->
		{#snippet memberChip(one: Membership)}
			<EntityPreview kind={one.kind} id={one.id} name={one.name} cover={one}>
				{#snippet children({ props })}
					<span {...props} class="hovered">
						<Chip
							tone="quiet"
							href={pageOf(one.kind, one.id)}
							picture={entityPicture(one.kind, one.id, one.name, one)}
							onremove={session.isAdmin ? () => void band.leave(one) : undefined}
							removeLabel={one.kind === 'collection'
								? `Remove from the collection ${one.name}`
								: one.kind === 'song'
									? `Take off the song ${one.name}`
									: `Remove from the Photo Set ${one.name}`}
							confirm={{ what: one.name, where: pageOf(one.kind, one.id) }}
						>
							{one.name}
						</Chip>
					</span>
				{/snippet}
			</EntityPreview>
		{/snippet}

		{#if band.heldBy.length > 0}
			<EntityRow icon="box" label="Collections">
				{#each band.heldBy as one (one.id)}
					{@render memberChip(one)}
				{/each}
			</EntityRow>
		{/if}

		{#if band.shotIn.length > 0}
			<EntityRow icon="photo_library" label="Photo Sets">
				{#each band.shotIn as one (one.id)}
					{@render memberChip(one)}
				{/each}
			</EntityRow>
		{/if}

		{#if band.tags.length > 0}
			<!-- The tags, each the same chip; tagging is the Add to verb. -->
			<EntityRow icon="shoppingmode" label="Tags">
				{#each band.tags as tag (tag.id)}
					<EntityPreview kind="tag" id={tag.id} name={tag.name}>
						{#snippet children({ props })}
							<span {...props} class="hovered">
								<Chip
									tone="quiet"
									href={pageOf('tag', tag.id)}
									picture={entityPicture('tag', tag.id, tag.name)}
									onremove={session.isAdmin ? () => void band.untag(tag.id) : undefined}
									removeLabel="Remove {tag.name} from this file"
									confirm={{ what: tag.name, where: pageOf('tag', tag.id) }}
								>
									{tag.name}
								</Chip>
							</span>
						{/snippet}
					</EntityPreview>
				{/each}
			</EntityRow>
		{/if}

		<!-- The song, after Tags as the rail lists Songs. -->
		{#if band.setTo.length > 0}
			<EntityRow icon="music_note_2" label="Song">
				{#each band.setTo as one (one.id)}
					{@render memberChip(one)}
				{/each}
			</EntityRow>
		{/if}
	</div>
</Fold>

<style>
	/* The rows, stacked under the heading, which owns the gap above them; each row is `EntityRow`'s. */
	.entities {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The menu wraps the link, so the wrapper is what the row lays out. */
	.slot :global(.trigger) {
		display: block;
	}

	/*
	 * The hover target for the preview card: the box its listeners are spread onto, since a component
	 * cannot take them. A reading measure, like every capped line of text. `flex`, never
	 * `inline-flex`, which would reserve descender room and ride the chip above its neighbours.
	 */
	.hovered {
		display: flex;
		max-inline-size: 16ch;
	}
</style>
