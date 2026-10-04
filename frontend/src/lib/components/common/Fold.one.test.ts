/*
 * ONE FOLD. A part of a screen folded under words is `Fold`, so two folds on one pane cannot be
 * drawn two ways. A `<details>` written anywhere else is one of the three disclosures that are not
 * that fold, each named here with why.
 */
import { expect, it } from 'vitest';

const SOURCES = import.meta.glob(['/src/lib/**/*.svelte', '/src/routes/**/*.svelte'], {
	query: '?raw',
	import: 'default',
	eager: true
});

const NOT_A_FOLD: Readonly<Record<string, string>> = {
	'/src/lib/components/common/Fold.svelte': 'the fold itself',
	'/src/lib/components/common/MoreAbout.svelte':
		'the second half of an explanation, under words that never change',
	'/src/lib/components/common/HistorySentence.svelte': 'the rest of a list, inside a sentence',
	'/src/lib/settings-ui/NamingTemplate.svelte': "a Site's card, opened on its own name"
};

it('draws every fold with Fold', () => {
	const drawnByHand = Object.entries(SOURCES)
		.filter(([file]) => !file.startsWith('/src/routes/design/') && !(file in NOT_A_FOLD))
		.filter(([, text]) =>
			/(?<!`)<details[\s>]/.test((text as string).replace(/<!--[\s\S]*?-->|\/\*[\s\S]*?\*\//g, ''))
		)
		.map(([file]) => file);
	expect(drawnByHand).toEqual([]);
});
