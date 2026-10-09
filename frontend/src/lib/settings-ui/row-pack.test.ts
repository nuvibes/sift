/* A control made of parts follows its row onto a phone. */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';

const OWNERS = new Set(['ActionRow.svelte']);

function styleOf(path: string): string {
	const source = readFileSync(path, 'utf8');
	const at = source.indexOf('<style>');
	return at === -1 ? '' : source.slice(at);
}

describe('the row pack', () => {
	it('is published by the row and flips at a phone width', () => {
		const style = styleOf('src/lib/components/common/LabelledRow.svelte');
		expect(style).toMatch(/\.control \{[^}]*--row-pack: flex-end;/);
		const phone = style.slice(style.indexOf('@media (max-width: 767px)'));
		expect(phone).toMatch(/--row-pack: flex-start;/);
	});

	it('is what every settings pane packs a part of a control by', () => {
		const offenders = readdirSync('src/lib/settings-ui')
			.filter((name) => name.endsWith('.svelte') && !OWNERS.has(name))
			.filter((name) =>
				/justify-content:\s*(flex-)?end\s*;/.test(styleOf(`src/lib/settings-ui/${name}`))
			);
		expect(offenders).toEqual([]);
	});
});
