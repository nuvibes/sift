<script lang="ts">
	/* LIVE: nothing moves it (the Sites Sift can download from are its code, and a new version reloads the window) */
	import { onMount } from 'svelte';
	import { Badge, Fold, Problem, Scroller, SectionHeading } from '$lib/components/common';
	import { api, ApiError } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { NEED_BADGE } from '$lib/components/downloads/cookies';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { COPY } from './SupportedSites.search';

	/* Which sites Sift knows, and what it can do with each.
	 *
	 * Reference material rather than a setting: it answers "what happens when I paste a link from
	 * here", which is a question asked before anything is configured. Nothing on it is adjustable.
	 *
	 * The known issues column is the one people actually come here for. The question asked in
	 * front of this table is rarely "is this site supported": it is "why did that not work", and a
	 * site's age gate or region block answers with an ordinary 403 that says nothing. What is listed
	 * is only what somebody has watched a site actually say.
	 *
	 * The tested column is the honest one. It says whether downloads from a site have actually been
	 * run and found working, not whether the code for it exists. A matrix that marks a site tested
	 * because it looks right is worth less than no matrix, because it is believed.
	 *
	 * And a row is not a claim of support by itself. Some sites are listed only so that traffic to
	 * them can be given a way out, which needs the address recognised and nothing else, so the
	 * table says which, rather than leaving somebody to read support into the site being here.
	 *
	 * ON A PHONE EACH SITE IS A CARD, not a row of six columns. The table is 650 pixels wide at its
	 * narrowest (the badges and the addresses do not wrap below that), so at 393 it would
	 * scroll sideways inside the pane, the one thing on a phone that does. A card says the same words: the
	 * Site's name, then each column's heading beside what the row said under it, drawn by the same
	 * snippets below, so the two shapes cannot come to say one Site two ways.
	 */

	type SupportedSite = components['schemas']['SupportedSite'];

	let sites = $state<SupportedSite[]>([]);
	let problem = $state<string | null>(null);

	onMount(() => {
		void (async () => {
			try {
				sites = await api.get<SupportedSite[]>('/supported-sites');
			} catch (error) {
				problem = error instanceof ApiError ? error.message : COPY.unreachable;
			}
		})();
	});
</script>

<!-- What each column says about one Site, drawn once for the table's cell and the phone's card. -->
{#snippet support(site: SupportedSite)}
	{#if site.supported}
		<Badge state="done" label={COPY.supported} />
	{:else}
		<!-- Not a shortcoming either: it is what this row is FOR. The site is here so its traffic
		     can be routed, and saying otherwise would be a claim nobody has earned. -->
		<Badge state="canceled" icon="error" label={COPY.routing} />
	{/if}
{/snippet}

{#snippet bulk(site: SupportedSite)}
	{#if site.bulk}
		<Badge state="done" label={COPY.yes} />
	{:else}
		<!-- Not a shortcoming. Asking a site for everything a creator has posted is the most
		     conspicuous thing a downloader can do, and where it has not been shown to be safe the
		     answer is no. -->
		<Badge state="canceled" label={COPY.single} />
	{/if}
{/snippet}

<!-- The cookies sheet's own badge and sentence for this Site, from the one table both read
     (`NEED_BADGE`): the two screens cannot say one Site's need two ways. -->
{#snippet cookies(site: SupportedSite)}
	<Badge
		state={NEED_BADGE[site.cookies].state}
		icon={NEED_BADGE[site.cookies].icon}
		label={NEED_BADGE[site.cookies].label}
	/>
	<span class="why">{site.cookies_why}</span>
{/snippet}

{#snippet walls(site: SupportedSite)}
	{#if site.walls.length > 0}
		<ul>
			{#each site.walls as wall, at (`${at}:${wall}`)}
				<li>{wall}</li>
			{/each}
		</ul>
	{:else}
		<span class="untried" aria-label={COPY.nothingRecorded}>—</span>
	{/if}
{/snippet}

<section>
	<!-- `sites.supported`: the address follows the table, which is on Sites, beside the tunnels
	     and cookies of the same Sites; a settings-search result landing on a pane that does not
	     draw the thing it named teaches somebody the wrong place to look. -->
	<SectionHeading id="sites.supported">{COPY.name}</SectionHeading>
	<p class="lede">{COPY.lede}</p>

	<!-- The three words the Support and Bulk downloads columns use, said once, drawn with the
	     same badges the rows are drawn with. Written out by hand it would be a legend
	     describing what the table once said: the reason `SharingLegend` is built the way it
	     is. -->
	<ul class="answer-key">
		<li>
			<Badge state="done" label={COPY.supported} />
			<span>{COPY.supportedSays}</span>
		</li>
		<li>
			<Badge state="canceled" icon="error" label={COPY.routing} />
			<span>{COPY.routingSays}</span>
		</li>
		<li>
			<Badge state="canceled" label={COPY.single} />
			<span>{COPY.singleSays}</span>
		</li>
		<!-- The Cookies column's three words, from the same table the column and the cookies sheet
		     draw. Each Site's own sentence says which of its content "Partial" covers. -->
		<li>
			<Badge
				state={NEED_BADGE.required.state}
				icon={NEED_BADGE.required.icon}
				label={NEED_BADGE.required.label}
			/>
			<span>{COPY.requiredSays}</span>
		</li>
		<li>
			<Badge
				state={NEED_BADGE.partial.state}
				icon={NEED_BADGE.partial.icon}
				label={NEED_BADGE.partial.label}
			/>
			<span>{COPY.partialSays}</span>
		</li>
		<li>
			<Badge
				state={NEED_BADGE.not_needed.state}
				icon={NEED_BADGE.not_needed.icon}
				label={NEED_BADGE.not_needed.label}
			/>
			<span>{COPY.notNeededSays}</span>
		</li>
	</ul>

	<Problem message={problem} />

	<!-- Every row of reference material, folded away under words that say how many Sites are
	     behind them: the count is the reason it is folded. -->
	{#if sites.length > 0}
		<Fold summary={COPY.showAll(sites.length)}>
			{#if phoneWidth.yes}
				<!-- One card per Site on a phone: the name, then each column's heading beside what the
				     table's row says under it. See the header. -->
				<ul class="site-cards">
					{#each sites as site (site.key)}
						<li class="site-card">
							<p class="site-name">{site.name}</p>
							<dl class="site-facts">
								<div>
									<dt>{COPY.columns.support}</dt>
									<dd>{@render support(site)}</dd>
								</div>
								<div>
									<dt>{COPY.columns.addresses}</dt>
									<dd class="hosts">{site.hosts.join(', ')}</dd>
								</div>
								<div>
									<dt>{COPY.columns.bulk}</dt>
									<dd>{@render bulk(site)}</dd>
								</div>
								<div>
									<dt>{COPY.columns.cookies}</dt>
									<dd class="cookies">{@render cookies(site)}</dd>
								</div>
								<div>
									<dt>{COPY.columns.issues}</dt>
									<dd class="walls">{@render walls(site)}</dd>
								</div>
							</dl>
						</li>
					{/each}
				</ul>
			{:else}
				<Scroller horizontal>
					<div>
						<table>
							<thead>
								<tr>
									<th scope="col">{COPY.columns.site}</th>
									<th scope="col">{COPY.columns.support}</th>
									<th scope="col">{COPY.columns.addresses}</th>
									<th scope="col">{COPY.columns.bulk}</th>
									<th scope="col">{COPY.columns.cookies}</th>
									<th scope="col">{COPY.columns.issues}</th>
								</tr>
							</thead>
							<tbody>
								{#each sites as site (site.key)}
									<tr>
										<th scope="row">{site.name}</th>
										<td>{@render support(site)}</td>
										<td class="hosts">{site.hosts.join(', ')}</td>
										<td>{@render bulk(site)}</td>
										<td class="cookies">{@render cookies(site)}</td>
										<td class="walls">{@render walls(site)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Scroller>
			{/if}
		</Fold>
	{/if}
</section>

<style>
	/* `answer-key`, the same name the Appearance pane's second legend takes, and for the same
	   reason: `.legend` is already drawn elsewhere in settings and Svelte scopes the styles
	   without scoping the name in the DOM. Rows rather than a wrapping line, because each of
	   these badges is explained by a whole sentence. */
	/* Two declared columns, the badges in the first and every sentence starting on one line in
	   the second, as the naming template's glossary lays its words: a row that started its
	   sentence where its own badge ended would put six sentences at six different x. Each item is a
	   subgrid of the list's two tracks, so the list stays a list. */
	.answer-key {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		column-gap: var(--space-3);
		row-gap: var(--space-3);
		margin: 0 0 var(--space-5);
		padding: 0;
		list-style: none;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		/* The pane's one reading measure, as the lede above it: a count of characters is measured in
		   this list's own smaller type, so it would run past every other paragraph on the pane. */
		max-inline-size: var(--reading-measure);
	}

	.answer-key li {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		align-items: baseline;
	}

	/* The badge starts its column and keeps its own size. */
	.answer-key li > :global(.badge) {
		justify-self: start;
	}

	.lede {
		margin: 0 0 var(--space-3);
	}

	table {
		border-collapse: collapse;
		inline-size: 100%;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	th,
	td {
		text-align: start;
		padding: var(--space-2) var(--space-3) var(--space-2) 0;
		border-bottom: 1px solid var(--sift-line);
		white-space: nowrap;
	}

	thead th {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	tbody th {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.hosts {
		white-space: normal;
		max-inline-size: 40ch;
		color: var(--sift-ink-3);
	}

	.untried {
		color: var(--sift-ink-3);
	}

	/* The badge on its own line and the Site's sentence under it, as the cookies sheet lays them. */
	.cookies {
		white-space: normal;
		max-inline-size: 36ch;
	}

	.why {
		display: block;
		margin-block-start: var(--space-1);
		color: var(--sift-ink-3);
	}

	.walls {
		white-space: normal;
		max-inline-size: 46ch;
	}

	.walls ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.walls li {
		margin-block-end: var(--space-1);
	}

	/*
	 * A SITE AS A CARD, on a phone: the table's row turned on its side. The name first, in the row
	 * header's type; then each column's heading beside what the row said, the headings in one
	 * column and every answer starting on one line, as the answer key above lays its badges and
	 * sentences. A hairline between two Sites, drawn on the later one's top edge as every row
	 * of a pane draws its line, so the first card has none over it and the last leaves none under.
	 */
	.site-cards {
		margin: 0;
		padding: 0;
		list-style: none;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.site-card {
		padding-block: var(--space-3);
	}

	.site-card + .site-card {
		border-top: 1px solid var(--sift-line);
	}

	.site-name {
		margin: 0 0 var(--space-2);
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.site-facts {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		column-gap: var(--space-3);
		row-gap: var(--space-2);
		margin: 0;
	}

	.site-facts > div {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		align-items: baseline;
	}

	.site-facts dt {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.site-facts dd {
		margin: 0;
		min-inline-size: 0;
	}

	/* The addresses are one run of words that wraps anywhere in the card, never past its edge; the
	   table's measures are a column's, and the card's column is the card. */
	.site-facts .hosts,
	.site-facts .cookies,
	.site-facts .walls {
		max-inline-size: none;
	}

	.site-facts .hosts {
		overflow-wrap: anywhere;
	}
</style>
