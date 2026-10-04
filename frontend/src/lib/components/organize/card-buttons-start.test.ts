/*
 * On every Organize card the buttons start the line, under the words they answer. Decided once,
 * on the card (`DecisionCard`), and read by every button line inside one; a line outside a card keeps its buttons at the end.
 */
import { expect, it } from 'vitest';
import card from './DecisionCard.svelte?raw';
import answers from './Answers.svelte?raw';
import filed from './FiledPanel.svelte?raw';
import shoots from './ShootsPanel.svelte?raw';

/** The declarations of the one rule `selector` names in a component's stylesheet. */
function ruleOf(source: string, selector: string): string {
	const style = /<style>([\s\S]*)<\/style>/.exec(source)?.[1] ?? '';
	const at = style.indexOf(`${selector} {`);
	expect(at, selector).toBeGreaterThan(-1);
	return style.slice(at, style.indexOf('}', at));
}

it('says on the card that its buttons start the line', () => {
	const rule = ruleOf(card, '.card');
	expect(rule).toContain('--card-actions-justify: flex-start');
	expect(rule).toContain('--card-actions-push: 0');
});

it('has every button line inside a card read the card, keeping the end outside one', () => {
	expect(ruleOf(answers, '.answers')).toContain(
		'justify-content: var(--card-actions-justify, flex-end)'
	);
	expect(ruleOf(answers, '.answers')).toContain(
		'margin-inline-start: var(--card-actions-push, auto)'
	);
	expect(ruleOf(filed, '.fold')).toContain('var(--card-actions-justify, flex-end)');
	expect(ruleOf(shoots, '.rest')).toContain('var(--card-actions-justify, flex-end)');
});
