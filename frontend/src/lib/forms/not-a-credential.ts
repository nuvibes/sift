/* Attributes that ask a password manager to leave a field alone.
 *
 * For the PIN fields, and only those. A PIN is `type="password"` because it must not be shoulder-
 * readable, and that is the only thing it has in common with a password: it is six
 * digits, it is typed rather than remembered, and it dies when Sift restarts. That last part is
 * the whole reason it is allowed to be short: a short secret is acceptable precisely because it
 * can never open anything a restart has already shut. A manager that saves it into long-term
 * storage, and fills it on every machine that syncs, quietly removes the property the shortness
 * was traded for.
 *
 * `autocomplete="off"` is already on every one of them and is not enough. Managers deliberately
 * ignore it, because sites used it to stop people using managers for real passwords. So the
 * vendors added their own opt-outs instead, and each one reads a different attribute. All four
 * are set because there is no shared standard, and one is harmless to a manager that does not
 * know it.
 *
 * This does NOT go near the real password fields. Somebody signing in should absolutely be
 * offered their password, and taking that away would push them towards a weaker one they can
 * type.
 *
 * ## Spread it BEFORE any `class`
 *
 * `{...NOT_A_CREDENTIAL}` written after a literal `class` attribute overwrites the class with
 * nothing, and a gate over every `.svelte` file refuses it. Put the spread first.
 */
export const NOT_A_CREDENTIAL = {
	/** 1Password */
	'data-1p-ignore': '',
	/** LastPass */
	'data-lpignore': 'true',
	/** Bitwarden */
	'data-bwignore': '',
	/** Dashlane */
	'data-form-type': 'other'
} as const;
