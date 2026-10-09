// SPDX-License-Identifier: AGPL-3.0-or-later
/* A row's help is one or two sentences; anything longer is folded under "More about this". */

/* A sentence ends at a full stop, question or exclamation mark followed by a space and a
   capital, a digit or an opening quote: the boundaries the server's prose uses. */
const BOUNDARY = /(?<=[.!?])\s+(?=[A-Z0-9"'])/;

interface SplitHelp {
	/** The first sentences, drawn as the row's help. */
	help: string;
	/** The rest, for "More about this", or null when there is no rest. */
	more: string | null;
}

export function splitHelp(text: string, keep = 2): SplitHelp {
	const sentences = text.trim().split(BOUNDARY);
	if (sentences.length <= keep) return { help: text.trim(), more: null };
	return {
		help: sentences.slice(0, keep).join(' '),
		more: sentences.slice(keep).join(' ')
	};
}
