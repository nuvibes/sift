<script lang="ts">
	/*
	 * What a downloaded file is called: the box, the ready-made patterns, the tokens you press, and
	 * a worked example from the server that will do the naming.
	 *
	 * ## Why it is a component
	 *
	 * It is drawn once for the rule every address Sift has no Site for follows, and once inside
	 * every Site that has been given its own answer. A bare text box with a placeholder is an
	 * unlabelled blank box whose purpose nobody can tell, and two drawings of one control drift:
	 * one of them can stop being drawn while its rule is still stored. So both are this.
	 *
	 * ## Three states, and the box alone cannot tell two of them apart
	 *
	 * NO RULE (`null`): Sift names the file the way it does for this Site (`shipped`). KEEP (`''`):
	 * the file keeps the name the Site gave it. TYPED: the rule in the box. The first two are both
	 * an empty box, so the line under the label says which one this is and each has a chip of its
	 * own. Clearing the box is "no rule" (the way back to Sift's name), and never "keep".
	 *
	 * ## Why the example comes from the server
	 *
	 * The same code that will name the real files, rather than a second implementation here that
	 * agrees with it until it does not. It is asked PER SITE, because the answer differs: a file
	 * host has no uploader, so `{creator}` is empty there and filled on TikTok. A preview that
	 * always showed a creator would teach the opposite of what happens, on two Sites in three.
	 */
	import { Chip, Select, TextInput } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { api, ApiError } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { COPY as BLOCK } from './NamingTemplate.search';

	const COPY = BLOCK.field;

	interface Props {
		/** The template as stored. `null` is no rule (Sift's name for the Site applies); EMPTY is
		 *  the explicit choice to keep the name the Site gave the file. */
		value: string | null;
		/** What `null` stands for here: Sift's name for this Site, or EMPTY for "keep the name".
		 *  EMPTY for the rule for everything, which has no Site to name files after. */
		shipped?: string;
		/** The Site's display name, for the line that says which state the row is in. None for the
		 *  rule for everything. */
		site?: string | null;
		/** The words to offer, in order: those this Site can fill (`name_words` on the Sites list). */
		words: string[];
		/** What each word means, from the server. */
		tokens: Record<string, string>;
		/** The Site the example is built from, or null for the rule for everything. */
		scope?: string | null;
		label?: string;
		/** Said under the label in place of the state line. Only the rule for everything uses it. */
		help?: string;
		/** Called with the new rule when it is settled: a chip pressed, a word put in, or the box
		 *  left changed. Never per keystroke, and never for a box left as it was. */
		onsave: (next: string | null) => void;
	}

	let {
		value,
		shipped = '',
		site = null,
		words,
		tokens,
		scope = null,
		label = BLOCK.name,
		help = '',
		onsave
	}: Props = $props();

	/* Ready-made patterns, so the ordinary answers are one press rather than five words typed by
	   hand. Deliberately short: a list long enough to need reading is a list nobody reads, and the
	   words below cover anything not here. Each is offered only where this Site can fill every word
	   in it: a pattern that needs a creator on a Site that never names one is a pattern the save
	   refuses. "Name" is `{name}`, the name the file arrived with, and not the post's title. */
	const PRESETS: { label: string; template: string }[] = [
		{ label: 'Creator - Name', template: '{creator} - {name}' },
		{ label: 'Site - Name', template: '{site} - {name}' },
		{ label: 'Date - Name', template: '{date} - {name}' },
		{ label: 'Creator - Date - Name', template: '{creator} - {date} - {name}' }
	];

	/* The ready-made answers as one choice on the row, the way every setting on the pane is
	   chosen: Sift's name and keep, which an empty box can each stand for, then the patterns. A
	   rule typed by hand is none of them, and the choice then reads as the person's own rule,
	   which is shown and cannot be chosen. */
	const SIFTS_NAME = '__sifts_name__';
	const KEEP = '__keep__';
	const OWN_RULE = '__own_rule__';

	/** Between two words a pressed word lands beside: the shape Sift's own names use. */
	const SEPARATOR = ' - ';
	/** Text that ends in a word or a closing brace, so the next word needs a separator before it. */
	const ENDS_IN_A_WORD = /[\p{L}\p{N}\}]$/u;
	/** Text that starts with a word or an opening brace, so it needs a separator after one. */
	const STARTS_WITH_A_WORD = /^[\p{L}\p{N}\{]/u;

	let box = $state<HTMLInputElement | null>(null);
	/* Filled by the effect below, which runs before anything reads it: the stored value is a prop
	   that changes underneath, so a starting copy of it here would be a copy that goes stale. */
	let typed = $state('');
	let example = $state('');
	let problem = $state<string | null>(null);

	/* The stored value wins whenever it changes underneath: a Site put back to following the
	   default has its box emptied by the list above, not by anything in here. */
	$effect(() => {
		typed = value ?? '';
	});

	/** Keep, stored or standing in for no rule on a Site whose own name is keep. */
	const keeps = $derived(value === '' || (value === null && shipped === ''));

	/** The rule a file would be named by right now: the box while it has something in it, else
	 *  keep where keep was chosen, else Sift's name, which is what leaving the box will store. */
	const effective = $derived(typed.trim() !== '' ? typed : value === '' ? '' : shipped);

	$effect(() => {
		void preview(effective);
	});

	/** The words this Site can fill that the server has a meaning for, in the Site's order. */
	const offered = $derived(words.filter((word) => word in tokens));

	const presets = $derived(
		PRESETS.filter((preset) =>
			[...preset.template.matchAll(/\{(\w+)\}/g)].every((found) => offered.includes(found[1]))
		)
	);

	/** Which ready-made answer is in force, or the person's own rule. */
	const chosenPreset = $derived.by(() => {
		if (value === null && shipped) return SIFTS_NAME;
		if (keeps) return KEEP;
		return presets.some((preset) => preset.template === value) ? (value ?? OWN_RULE) : OWN_RULE;
	});

	const presetChoices = $derived([
		...(shipped ? [{ value: SIFTS_NAME, label: COPY.siftsName }] : []),
		{ value: KEEP, label: COPY.keep },
		...presets.map((preset) => ({ value: preset.template, label: preset.label })),
		...(chosenPreset === OWN_RULE ? [{ value: OWN_RULE, label: COPY.ownRule, disabled: true }] : [])
	]);

	function choosePreset(chosen: string) {
		if (chosen === OWN_RULE) return;
		if (chosen === SIFTS_NAME) usePreset(null);
		else if (chosen === KEEP) usePreset('');
		else usePreset(chosen);
	}

	/** Which of the three states this row is in, said in words, because two of them look the same. */
	const standing = $derived.by(() => {
		if (help) return help;
		const name = site ?? '';
		if (value === null) return shipped ? COPY.shippedRule(name, shipped) : COPY.shippedKeep(name);
		if (value === '') return COPY.chosenKeep(name);
		return shipped ? COPY.typedRule(shipped) : COPY.typedKeep(name);
	});

	/* Which preview was asked for last. The box asks on every keystroke and the answers are not
	   bound to come back in the order they were asked: a slow answer for `{` landing after the
	   one for `{site} x` would put "A file would be called {" under a box reading `{site} x`, and
	   leave it there. Only the newest question's answer, or its refusal, may reach the screen: the same
	   counter `SuggestInput` keeps for its completions. */
	let asked = 0;

	async function preview(template: string) {
		const mine = ++asked;
		try {
			const shown = await api.post<components['schemas']['NamePreview']>('/site-options/preview', {
				body: { naming: template, scope }
			});
			if (mine !== asked) return;
			example = shown.example;
			problem = null;
		} catch (error) {
			if (mine !== asked) return;
			problem = error instanceof ApiError ? (error.detail ?? error.message) : null;
		}
	}

	function usePreset(template: string | null) {
		typed = template ?? '';
		onsave(template);
	}

	/** The box left: what is in it, or no rule where it was emptied. Nothing when unchanged, so
	 *  leaving an empty box that stands for keep does not turn it into Sift's name. */
	function settle() {
		const next = typed.trim() === '' ? (value === '' ? '' : null) : typed;
		if (next !== value) onsave(next);
	}

	/* Put a word where the cursor is, not at the end, and with a separator between it and the word
	   beside it. Without one, two presses would make `{site}{creator}` and files
	   called `Instagramsomeone`; a person who wanted something else between them has typed it, and
	   a word next to their own punctuation or space is left as they put it. Appending is the easy
	   version and it is wrong the moment somebody is editing the middle of a template they already
	   have. The selection is restored past the word so a second one carries on from there. */
	function insert(token: string) {
		const field = box;
		const at = field?.selectionStart ?? typed.length;
		const to = field?.selectionEnd ?? at;
		const before = typed.slice(0, at);
		const after = typed.slice(to);
		const lead = ENDS_IN_A_WORD.test(before) ? SEPARATOR : '';
		const word = `{${token}}`;
		const tail = STARTS_WITH_A_WORD.test(after) ? SEPARATOR : '';
		typed = before + lead + word + tail + after;
		onsave(typed);
		const caret = at + lead.length + word.length;
		queueMicrotask(() => {
			field?.focus();
			field?.setSelectionRange(caret, caret);
		});
	}
</script>

<LabelledRow {label} help={standing}>
	<TextInput
		bind:element={box}
		bind:value={typed}
		aria-label={label}
		placeholder={keeps ? COPY.keep : shipped}
		spellcheck="false"
		onblur={settle}
	/>
</LabelledRow>

<!-- The ready-made answers as a row of the pane, its choice in the control column: Sift's name
     first where the Site has one that is not keep, then keep, then the patterns, so which one is
     in force is the choice's own reading and any is one press. -->
<LabelledRow label={COPY.presets} help={COPY.presetsHelp}>
	<Select
		label={site ? COPY.presetsFor(site) : COPY.presets}
		value={chosenPreset}
		options={presetChoices}
		onValueChange={choosePreset}
	/>
</LabelledRow>

<p class="tokens-help">{site ? COPY.tokensFor(site) : COPY.tokens}</p>
<dl class="tokens">
	{#each offered as token (token)}
		<dt>
			<!-- The shared chip, not a hand-rolled button. `onselect` makes it a real
			     `<button>`. Only the words this Site can fill are here: the save refuses
			     `{creator}` where nobody is ever named, so offering it would be offering a
			     refusal. -->
			<Chip size="sm" shape="square" tone="neutral" onselect={() => insert(token)}>
				<span class="token">{'{' + token + '}'}</span>
			</Chip>
		</dt>
		<dd>{tokens[token]}</dd>
	{/each}
</dl>

<!--
	Two roles, because they are two different announcements and one of them has to interrupt.

	One paragraph announced the polite way is right for an example of what a file would be called
	and wrong for a template the server refused: polite means read out when the reader next pauses,
	or not at all, so the refusal would reach somebody watching the screen and be withheld from
	somebody who was not.

	The role names are deliberately not spelled out in this comment: the gate that checks them reads
	the file as text, so writing one here is enough to re-flag the paragraph below it.

	Not the shared `Problem` block, which says in its own docstring that it is not the message under
	a form field. This one belongs to the field above it.
-->
{#if problem}
	<p class="example" role="alert">{problem}</p>
{:else}
	<p class="example" role="status">
		{#if example}
			{COPY.wouldBe} <strong>{example}</strong>
		{:else}
			{COPY.keeps}
		{/if}
	</p>
{/if}

<style>
	.tokens-help {
		margin: 0 0 var(--space-2);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.tokens {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: var(--space-2) var(--space-3);
		align-items: baseline;
		/* The pane's reading measure, as every help line under a row: a list of help is still
		   help, and the pane's rule bounds its paragraphs only. */
		max-inline-size: var(--reading-measure);
		margin: 0 0 var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	dt {
		margin: 0;
	}

	dd {
		margin: 0;
	}

	/* Only the face. The chip owns its surface, its border, its hover and its focus ring; what is
	   left to say here is that a token is DATA: the braces and the mono are what a reader then
	   finds in the box above. */
	.token {
		font: var(--text-data);
	}

	.example {
		margin: 0 0 var(--space-4);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}
</style>
