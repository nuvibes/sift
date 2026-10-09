/*
 * Guidance while somebody types a password, never a gate: the server holds the policy. The breach
 * list is the server's, asked by the component as a fifth rule.
 */

/** The server's own floor, mirrored. */
export const MIN_LENGTH = 10;

const COMFORTABLE_LENGTH = 16;

export interface Rule {
	label: string;
	met: boolean;
}

export type Strength = 'empty' | 'weak' | 'fair' | 'good' | 'strong';

interface Assessment {
	rules: Rule[];
	strength: Strength;
	score: number;
	acceptable: boolean;
}

function classes(password: string): number {
	let met = 0;
	if (/[a-z]/.test(password)) met += 1;
	if (/[A-Z]/.test(password)) met += 1;
	if (/[0-9]/.test(password)) met += 1;
	if (/[^A-Za-z0-9]/.test(password)) met += 1;
	return met;
}

/** Deliberately crude: length and variety are what the policy is about. */
export function assess(password: string): Assessment {
	const rules: Rule[] = [
		{ label: `At least ${MIN_LENGTH} characters`, met: password.length >= MIN_LENGTH },
		{
			label: 'An uppercase and a lowercase letter',
			met: /[a-z]/.test(password) && /[A-Z]/.test(password)
		},
		{ label: 'A number', met: /[0-9]/.test(password) },
		{ label: 'A symbol', met: /[^A-Za-z0-9]/.test(password) }
	];

	if (password.length === 0) {
		return { rules, strength: 'empty', score: 0, acceptable: false };
	}

	const acceptable = rules.every((rule) => rule.met);

	// Everything below the policy is weak.
	if (!acceptable) return { rules, strength: 'weak', score: 1, acceptable };

	if (password.length >= COMFORTABLE_LENGTH && classes(password) === 4) {
		return { rules, strength: 'strong', score: 4, acceptable };
	}
	if (password.length >= MIN_LENGTH + 2) return { rules, strength: 'good', score: 3, acceptable };
	return { rules, strength: 'fair', score: 2, acceptable };
}
