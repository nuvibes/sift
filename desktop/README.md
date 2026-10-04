# The Sift desktop shell

An Electron window around Sift. It is thin on purpose: the application itself is the same static
client the backend serves, and the shell decides only **which backend it is talking to** and
**what the page is allowed to ask the computer to do**.

Windows x64 only.

## Two lifecycles, one backend

| | What it does |
|---|---|
| **Standalone** | Starts the bundled Python backend on `127.0.0.1:5171` and loads it. The default. |
| **Client** | Loads a Sift somebody else is running, and starts nothing at all. |

Both end up loading one origin and handing over to the ordinary application. That is what makes
client mode nearly free: the client is static files served by the backend and every request it makes
is a relative `/api` path, so there is no second origin, no cross-site state, and no separate way to
sign in.

## Before changing anything

**The port is fixed at 5171 and is never moved silently.** A shell that quietly picks a free port
gives a different address every launch, which breaks a bookmark, breaks a second machine pointed at
this one, and makes the one sentence the documentation can say untrue. If the port is held, the
shell says so and names the port.

**Launching the application is signing in, and that is not a preference.** The key that unseals saved
logins and stash-box keys lives in memory and is filled from the password at sign-in. The backend
dies with the window, so a launch that skipped the password would leave somebody signed in whose
saved logins had all silently stopped working, which looks exactly like the keys were deleted.
See the long comment in `src/main.ts`.

**The preload is attached only to origins the person saved.** Anything else opens in the real
browser. That is the whole security boundary and it is meant to stay small. The local backend gets
every verb; a server on another computer gets only what a window onto a library elsewhere needs
(`REMOTE_VERBS` in `src/verbs.ts`), reads the clipboard only just after a real press, and is
reached over plain http only on the local network.

**The native drag addon is bound to one Electron version.** Moving Electron means recompiling it, so
an Electron upgrade is a release event rather than a dependency bump.

## Running it

```
npm install
npm run build:native      # once, and again whenever Electron moves
npm start                 # standalone, against the built client
npm run dev               # loads the Vite dev server instead: hot reload, no rebuild
```

`npm run dev` still starts the real backend; Vite only serves the page and forwards `/api` and
`/health` to 5171. Without the backend the window renders and answers nothing.
