<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PasswordStrength',
		category: 'control',
		role: 'which rules a typed password meets, one line each',
		basis: 'own',
		states: ['weak', 'strong']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui's Meter would fit the bar and not the component. The accessible content here is the
	   list of rules being ticked off, not a number: announcing "3 of 5" instead of which rule is
	   still unmet is the summary this was written to avoid. */
	/*
	 * The meter under a password being chosen.
	 *
	 * Draws nothing until something has been typed: an empty box with a red bar under it is telling
	 * somebody off for not having started yet.
	 *
	 * The rules are listed rather than summarised, because "weak" on its own is a verdict with no
	 * action attached. Ticking them off as they are met is the part that helps.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { assess } from '$lib/shell/password-strength';
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';

	interface Props {
		password: string;
		/** The element that names this, so a screen reader hears which box it is about. */
		id?: string;
	}

	let { password, id }: Props = $props();

	const result = $derived(assess(password));

	/*
	 * The one rule the browser cannot answer for itself.
	 *
	 * The server ships a list of about a hundred thousand leaked passwords and refuses anything in
	 * it, and this page had no way to see that list, so the meter said "Strong" about
	 * `Password123!` and the refusal arrived a click later. Now it asks, on a delay so the request
	 * follows a pause in typing rather than every keystroke, and draws the answer as one more rule.
	 *
	 * Unknown while a check is in flight, and unknown is drawn as pending rather than as met: a rule
	 * that ticks itself green before the answer arrives is worse than no rule. A request that fails
	 * leaves it unknown too, which is right: the server still refuses on submit, and guessing
	 * either way here would be inventing an answer.
	 */
	const AFTER_TYPING_MS = 350;
	let breached = $state<boolean | null>(null);

	$effect(() => {
		const typed = password;
		breached = null;
		if (typed.length === 0) return;
		let dropped = false;
		const timer = setTimeout(() => {
			void api
				.post<components['schemas']['PasswordCheckResponse']>('/auth/password/check', {
					body: { password: typed }
				})
				.then((answer) => {
					if (!dropped) breached = answer.breached;
				})
				.catch(() => {
					/* Left unknown. See above. */
				});
		}, AFTER_TYPING_MS);
		return () => {
			dropped = true;
			clearTimeout(timer);
		};
	});

	const LEAK_RULE = 'Not a password that has turned up in a leak';

	const WORDS: Record<string, string> = {
		weak: 'Weak',
		fair: 'Fair',
		good: 'Good',
		strong: 'Strong'
	};
</script>

{#if result.strength !== 'empty'}
	<!-- Polite, not assertive: it changes on every keystroke, and an assertive region would
	     interrupt a screen reader mid-word on each one. -->
	<div class="strength" {id} aria-live="polite">
		<div class="meter" data-strength={result.strength}>
			{#each [1, 2, 3, 4] as step (step)}
				<span class="step" class:lit={step <= result.score}></span>
			{/each}
			<span class="word">{WORDS[result.strength]}</span>
		</div>

		<ul class="rules">
			{#each result.rules as rule (rule.label)}
				<li class:met={rule.met}>
					<Icon name={rule.met ? 'check' : 'close'} size={16} />
					{rule.label}
				</li>
			{/each}
			<li class:met={breached === false} class:failed={breached === true}>
				<Icon
					name={breached === false ? 'check' : breached === true ? 'close' : 'schedule'}
					size={16}
				/>
				{breached === true ? 'This password has turned up in a leak' : LEAK_RULE}
			</li>
		</ul>
	</div>
{/if}

<style>
	.strength {
		margin-block-start: var(--space-2);
	}

	.meter {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.step {
		block-size: 4px;
		flex: 1 1 0;
		border-radius: 2px;
		background: var(--sift-surface-3);
		transition: background-color var(--dur-instant) var(--ease);
	}

	.word {
		flex: none;
		min-inline-size: 3.5em;
		text-align: end;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.meter[data-strength='weak'] .step.lit {
		background: var(--sift-bad);
	}

	.meter[data-strength='fair'] .step.lit {
		background: var(--sift-warn);
	}

	.meter[data-strength='good'] .step.lit,
	.meter[data-strength='strong'] .step.lit {
		background: var(--sift-ok);
	}

	.meter[data-strength='weak'] .word {
		color: var(--sift-bad-text);
	}

	.meter[data-strength='strong'] .word {
		color: var(--sift-ok);
	}

	/* ONE RULE PER LINE. This wrapped, so the two shortest ("A number" and "A symbol") packed
	   onto one row and read as a single rule with a comma in it. A checklist somebody is working
	   down is a column; how many fit on a line is not a reason to put two of them there. */
	.rules {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: var(--space-2) 0 0;
		padding: 0;
		list-style: none;
	}

	li {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	li.met {
		color: var(--sift-ok);
	}

	/* The only rule that goes red on its own. The others are things still to do, and a list of red
	   crosses on a half-typed password is a telling-off; this one is a fact about the password that
	   has been typed, and it will be refused. */
	li.failed {
		color: var(--sift-bad-text);
	}
</style>
