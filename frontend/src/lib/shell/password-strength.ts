/*
 * Live guidance while somebody types a password.
 *
 * This is guidance and never a gate. The server holds the policy and refuses what it refuses; if
 * the two ever disagree the server wins and the person sees its reason. What this adds is telling
 * them BEFORE they press the button, which matters here more than in most applications: there is no
 * reset email, so the cost of getting a password wrong on the setup screen is an install nobody can
 * open.
 *
 * The one rule it cannot mirror is the breach list. That is a hundred thousand entries checked on
 * the server; shipping it to the browser to save one round trip would be a megabyte on every page
 * load. So this module cannot see it, and rather than leave the meter saying "strong" about a
 * password the server is about to refuse, the component that draws it ASKS: a small unauthenticated
 * endpoint answers the one question, debounced, and the answer arrives as a fifth rule. What is
 * here stays pure and instant; the rule that needs the server is drawn as pending until it lands.
 */

/** The server's own floor, mirrored. Changing it there means changing it here. */
export const MIN_LENGTH = 10;

/** How long a password has to be before length alone stops being the weak part. */
const COMFORTABLE_LENGTH = 16;

export interface Rule {
	/** What the person still has to do, phrased as the thing rather than as a complaint. */
	label: string;
	met: boolean;
}

export type Strength = 'empty' | 'weak' | 'fair' | 'good' | 'strong';

interface Assessment {
	rules: Rule[];
	strength: Strength;
	/** 0 to 4, for a meter to draw. */
	score: number;
	/** Whether every rule the server enforces and this can see is satisfied. */
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

/**
 * How this password is doing, as rules and as one word.
 *
 * The score is deliberately crude. A number claiming to know how many years a machine would need is
 * a guess dressed as a measurement, and the guesses are wrong in both directions: they call a
 * memorable passphrase weak and a leaked eight-character password with a symbol in it strong. Length
 * and variety are what the policy is about, so they are what is shown.
 */
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

	// Everything below the policy is weak, whatever it looks like. Calling a nine-character
	// password "good" because it has a symbol in it would be telling somebody they are done when the
	// server is about to disagree.
	if (!acceptable) return { rules, strength: 'weak', score: 1, acceptable };

	// Past the policy, length is what still buys anything.
	if (password.length >= COMFORTABLE_LENGTH && classes(password) === 4) {
		return { rules, strength: 'strong', score: 4, acceptable };
	}
	if (password.length >= MIN_LENGTH + 2) return { rules, strength: 'good', score: 3, acceptable };
	return { rules, strength: 'fair', score: 2, acceptable };
}
