/* The shell's composition: what `main.ts` does with the pieces every other file supplies.
 *
 * `main.ts` runs on import (it asks for the single-instance lock, waits for the app to be
 * ready, wires the verbs, makes the window and walks first run), so each test here imports it
 * fresh, after setting the scene, and then presses what a person or Windows would press: a verb
 * the page calls, an `app` event, a link on the page, the close button.
 *
 * Every module it composes is doubled (`test/main-scene.ts`). Each of them has its own suite; what is under test here is
 * only the order and the choice: stop before move, check before save, the connect screen rather
 * than a stack trace.
 *
 * Out of reach here: that a real Electron honours the options the window is made with (the hidden
 * title bar, renderer isolation), that the privileged scheme is declared early enough for a real
 * `app`, and the real process lifecycle. Those are checked by driving the installed application.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DataLocations } from "./paths";
import {
  aSift,
  boot,
  lastSaved,
  LIBRARY,
  LOCAL,
  OTHER,
  resetScene,
  scene,
  SERVER,
  settle,
  SHELL,
  windowOf,
  type Stub,
} from "../test/main-scene";

vi.mock("./verbs", async (original) =>
  (await import("../test/main-scene")).doubles.verbs(original),
);
vi.mock("./backend", async (original) =>
  (await import("../test/main-scene")).doubles.backend(original),
);
vi.mock("./settings", async (original) =>
  (await import("../test/main-scene")).doubles.settings(original),
);
vi.mock("./firstrun", async (original) =>
  (await import("../test/main-scene")).doubles.firstrun(original),
);
vi.mock("./libraries", async (original) =>
  (await import("../test/main-scene")).doubles.libraries(original),
);
vi.mock("./browsers", async (original) =>
  (await import("../test/main-scene")).doubles.browsers(original),
);
vi.mock("./shelllink", async (original) =>
  (await import("../test/main-scene")).doubles.shelllink(original),
);
vi.mock("./machine", async (original) =>
  (await import("../test/main-scene")).doubles.machine(original),
);
vi.mock("./firewall", async (original) =>
  (await import("../test/main-scene")).doubles.firewall(original),
);
vi.mock("./update", async (original) =>
  (await import("../test/main-scene")).doubles.update(original),
);
vi.mock("./storage", async () =>
  (await import("../test/main-scene")).doubles.storage(),
);
vi.mock("./tray", async (original) =>
  (await import("../test/main-scene")).doubles.tray(original),
);
vi.mock("./shellpage", async (original) =>
  (await import("../test/main-scene")).doubles.shellpage(original),
);
vi.mock("./smoke", async () =>
  (await import("../test/main-scene")).doubles.smoke(),
);
vi.mock("./logbundle", async () =>
  (await import("../test/main-scene")).doubles.logbundle(),
);
vi.mock("./uninstall", async () =>
  (await import("../test/main-scene")).doubles.uninstall(),
);
vi.mock("./assets", async () =>
  (await import("../test/main-scene")).doubles.assets(),
);
vi.mock("./paths", async (original) =>
  (await import("../test/main-scene")).doubles.paths(original),
);
vi.mock("./log", async () =>
  (await import("../test/main-scene")).doubles.log(),
);

beforeEach(resetScene);

describe("starting", () => {
  it("a smoke run answers and does nothing else, not even asking for the lock", async () => {
    scene.smoke = true;
    const electron = await boot({}, (stub) => {
      stub.appCalls.locked = false;
    });

    expect(electron.appCalls.exit).toEqual([0]);
    /* Had it asked, a copy already open would have made this run quit "successfully". */
    expect(electron.appCalls.quit).toBe(0);
    expect(electron.BrowserWindow.instances).toHaveLength(0);
    expect(scene.order).not.toContain("verbs");
  });

  it("a second copy quits and leaves the first one alone", async () => {
    const electron = await boot({}, (stub) => {
      stub.appCalls.locked = false;
    });

    expect(electron.appCalls.quit).toBe(1);
    expect(electron.BrowserWindow.instances).toHaveLength(0);
  });

  it("declares the scheme before anything else, and wires the verbs before any window exists", async () => {
    const electron = await boot();

    expect(scene.order.slice(0, 4)).toEqual([
      "scheme",
      "clear",
      "pages",
      "verbs",
    ]);
    expect(electron.BrowserWindow.instances).toHaveLength(1);
  });

  it("makes the window with the page sealed off from Node", async () => {
    const electron = await boot();
    const options = windowOf(electron).options as {
      show: boolean;
      titleBarStyle: string;
      titleBarOverlay: { height: number };
      webPreferences: Record<string, unknown>;
    };

    expect(options.show).toBe(false);
    expect(options.titleBarStyle).toBe("hidden");
    /* One number for the window and for the verb that repaints it. */
    expect(options.titleBarOverlay.height).toBe(scene.hooks.titleBarHeight);
    expect(options.webPreferences).toMatchObject({
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
    });
  });

  it("focuses and restores the open window when Sift is started a second time", async () => {
    const electron = await boot();
    const window = windowOf(electron);
    window.minimized = true;

    electron.appListeners.get("second-instance")?.();

    expect(window.restored).toBe(1);
    expect(window.focused).toBe(1);
  });
});

describe("first run", () => {
  it("asks which way Sift is set up, and shows the window only once the question is loaded", async () => {
    const electron = await boot();
    const window = windowOf(electron);

    expect(window.loaded).toEqual([`${SHELL}/start`]);
    expect(window.shown).toBe(1);
    expect(scene.backends).toHaveLength(0);
  });

  it('goes on to where the library lives once "this computer" is chosen', async () => {
    const electron = await boot();

    expect(await scene.hooks.setup.mode("standalone")).toEqual({
      ok: true,
      refusal: null,
    });

    expect(lastSaved().mode).toBe("standalone");
    expect(windowOf(electron).loaded.at(-1)).toBe(`${SHELL}/library-location`);
  });

  it("takes the offered folder, starts the backend on it and asks for a sign-in", async () => {
    const electron = await boot({ mode: "standalone" });
    const steps: string[] = [];

    expect(
      await scene.hooks.setup.library(false, (step) => steps.push(step)),
    ).toEqual({
      ok: true,
      refusal: null,
    });
    /* Each step said as it starts, for the screen: the folder checked, then Sift started there. */
    expect(steps).toEqual(["checking", "starting"]);

    expect(scene.backends[0]?.locations).toEqual(scene.offered.locations);
    /* The stale sign-in goes, or every saved secret is quietly sealed behind a live session. */
    expect(electron.cookiesRemoved).toEqual([[LOCAL, "sift_session"]]);
    expect(windowOf(electron).loaded.at(-1)).toBe(LOCAL);
    expect(scene.uninstaller.at(-1)).toEqual(scene.offered.locations);
    /* Recorded where a library is OPENED, so the switcher can go back to it. */
    expect(lastSaved().libraries.map((one) => one.dataDir)).toEqual([
      scene.offered.locations.dataDir,
    ]);
  });

  it("stays where it was when the picker is closed, and says why a picked folder is refused", async () => {
    await boot({ mode: "standalone" });

    expect(await scene.hooks.setup.library(true, () => {})).toEqual({
      ok: false,
      refusal: null,
    });

    scene.picked = OTHER;
    scene.refusal = "Sift cannot write to that folder.";
    const steps: string[] = [];
    expect(
      await scene.hooks.setup.library(true, (step) => steps.push(step)),
    ).toEqual({
      ok: false,
      refusal: "Sift cannot write to that folder.",
    });
    expect(scene.backends).toHaveLength(0);
    /* A refused folder was checked and nothing was started. */
    expect(steps).toEqual(["checking"]);
  });

  it("going back stops the backend the sign-in screen was served by, and keeps the folder", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    expect(await scene.hooks.setup.back()).toEqual({ ok: true, refusal: null });

    expect(scene.backends[0]?.stopped).toBe(1);
    expect(lastSaved().mode).toBeNull();
    expect(lastSaved().dataDir).toBe(LIBRARY.dataDir);
    expect(windowOf(electron).loaded.at(-1)).toBe(`${SHELL}/start`);
  });

  it("suggests the folder the screen offers", async () => {
    await boot();

    expect(await scene.hooks.setup.suggested()).toEqual({
      path: scene.offered.locations.dataDir,
      existing: false,
    });
  });
});

/* The shell's own log (`Settings > Activity > Log > This app`): every act a bug report needs is a
 * line in it, or "Copy for a bug report" carries nothing about the app. */
describe("the shell's own log", () => {
  function fieldsOf(event: string): Record<string, unknown> | undefined {
    return scene.said.find(([said]) => said === event)?.[1];
  }

  const known = (where: DataLocations) => ({ ...where, name: "Other", lastOpened: 1 });

  it("says which Sift started, the library it opened, its port, how long, and the first show", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    expect(scene.logged[0]).toBe("shell.started");
    expect(fieldsOf("shell.started")).toMatchObject({ mode: "standalone" });
    expect(fieldsOf("backend.started")).toMatchObject({
      library: LIBRARY.dataDir,
      port: 5171,
      network: false,
    });
    expect(typeof fieldsOf("backend.started")?.took_ms).toBe("number");
    expect(scene.logged.filter((one) => one === "window.first_shown")).toHaveLength(1);
  });

  it("writes a library switch, and one that would not start", async () => {
    await boot({ mode: "standalone", ...LIBRARY, libraries: [known(OTHER)] });

    await scene.hooks.libraries.open(OTHER.dataDir);
    expect(fieldsOf("library.switched")).toEqual({
      from: LIBRARY.dataDir,
      library: OTHER.dataDir,
    });

    scene.startFailures.push(new Error("a library from a newer Sift"));
    await scene.hooks.libraries.open(LIBRARY.dataDir);
    expect(scene.logged).toContain("backend.start_failed");
    expect(fieldsOf("library.switch_failed")).toEqual({
      library: LIBRARY.dataDir,
      reason: "a library from a newer Sift",
    });
  });

  it("writes a start that failed as an error, before the dialog that says so", async () => {
    scene.startFailures.push(new Error("boom"));
    await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(1);
    });

    expect(scene.logged).toEqual(
      expect.arrayContaining(["backend.start_failed", "shell.start_failed", "shell.stopped"]),
    );
  });

  it("writes the page going away, and the way out", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    const gone = windowOf(electron).pageListeners.get("render-process-gone") as unknown as
      | ((event: unknown, details: { reason: string; exitCode: number }) => void)
      | undefined;
    gone?.({}, { reason: "crashed", exitCode: 3 });
    electron.appListeners.get("before-quit")?.();

    expect(fieldsOf("window.page_gone")).toEqual({ reason: "crashed", code: 3 });
    expect(scene.logged.at(-1)).toBe("shell.quitting");
  });
});

describe("a copy set up for its own library", () => {
  it("starts the backend, forgets the old sign-in and loads the library", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    expect(scene.backends).toHaveLength(1);
    expect(scene.backends[0]?.sharing).toBe(false);
    expect(electron.cookiesRemoved).toEqual([[LOCAL, "sift_session"]]);
    expect(windowOf(electron).loaded).toEqual([LOCAL]);
    expect(scene.uninstaller).toEqual([LIBRARY]);
  });

  it("starts on the library chosen to open when Sift starts, not the one open last", async () => {
    scene.opening = OTHER;
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    expect(scene.backends.map((one) => one.locations)).toEqual([OTHER]);
    expect(scene.uninstaller.at(-1)).toEqual(OTHER);
    expect(windowOf(electron).loaded).toEqual([LOCAL]);
  });

  it("falls back to the library open last when the chosen one will not start", async () => {
    scene.opening = OTHER;
    scene.startFailures.push(new Error("a library from a newer Sift"));
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    /* The chosen one was tried, stopped, and the one open last started in its place: a choice
		   about which library comes first never becomes a reason nothing opens. */
    expect(scene.backends.map((one) => one.locations)).toEqual([
      OTHER,
      LIBRARY,
    ]);
    expect(scene.backends[0]?.stopped).toBe(1);
    expect(scene.logged).toContain("library.opening_refused");
    expect(scene.uninstaller).toEqual([LIBRARY]);
    expect(windowOf(electron).loaded).toEqual([LOCAL]);
    expect(electron.appCalls.quit).toBe(0);
  });

  it("offers a way back when the backend will not start, and quits when that is refused", async () => {
    const { BackendStartError } = await import("./backend");
    scene.startFailures.push(
      new BackendStartError("Port 5171 is in use", "something else holds it"),
    );
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(1);
    });

    expect(electron.dialogCalls[0]?.message).toBe("Port 5171 is in use");
    expect(electron.dialogCalls[0]?.detail).toContain(
      "something else holds it",
    );
    expect(electron.appCalls.quit).toBe(1);
  });

  it("asks where the library is only when it isn't where this copy was told", async () => {
    scene.libraryHere = false;
    const { BackendStartError } = await import("./backend");
    scene.startFailures.push(new BackendStartError("Sift stopped while it was starting up.", "", true, 1));
    const opened = [{ ...LIBRARY, name: "Library", lastOpened: 1 }];
    const electron = await boot({ mode: "standalone", ...LIBRARY, libraries: opened }, (stub) => {
      stub.dialogAnswers.push(1);
    });
    await settle();

    expect(electron.dialogCalls[0]?.message).toBe(
      `Sift can't open your library at ${LIBRARY.dataDir}`,
    );
    expect(electron.dialogCalls[0]?.buttons).toEqual([
      "Start again",
      "Choose again",
      "Download log",
      "Quit",
    ]);
    expect(electron.dialogCalls[0]?.detail).toContain(
      "starts a new, empty library there, and your library stays where it is",
    );
    expect(electron.appCalls.quit).toBe(0);
    expect(lastSaved()).toMatchObject({ mode: "standalone", dataDir: null, cacheDir: null });
    expect(windowOf(electron).loaded.at(-1)).toBe(`${SHELL}/library-location`);
  });

  it("starts again on the place it was told, once the folder answers", async () => {
    scene.refusal = "That folder is on a network drive.";
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(0, 1);
    });

    scene.backends[0]?.gaveUp("it stopped four times", true, 1);
    await settle();

    expect(electron.dialogCalls[0]?.detail).toContain("That folder is on a network drive.");
    expect(scene.backends).toHaveLength(2);
    expect(lastSaved().dataDir).toBe(LIBRARY.dataDir);
  });

  it("says what to do, and never offers setup, when a running backend gives up", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(1);
    });

    scene.backends[0]?.gaveUp("it stopped three times", false, 3221225477);
    await settle();

    expect(electron.dialogCalls[0]?.detail).toContain(
      "It stopped inside one of the libraries it runs on.",
    );
    expect(electron.dialogCalls[0]?.detail).toContain("github.com/nuvibes/sift/issues");
    expect(electron.dialogCalls[0]?.buttons).toEqual(["Download log", "Quit"]);

    expect(electron.dialogCalls[0]?.title).toBe("Sift has stopped");
    expect(electron.dialogCalls[0]?.detail).toContain("it stopped three times");
    expect(electron.appCalls.quit).toBe(1);
  });

  it("offers a start without the optional features when the backend kept dying, and says it did", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(0);
    });

    scene.backends[0]?.gaveUp("it stopped four times", true);
    await settle();

    expect(electron.dialogCalls[0]?.buttons).toEqual([
      "Start without them",
      "Download log",
      "Quit",
    ]);
    expect(electron.dialogCalls[0]?.detail).toContain(
      "Your settings don't change.",
    );
    expect(scene.backends.map((one) => one.held)).toEqual([false, true]);
    expect(electron.dialogCalls[1]?.message).toBe(
      "Sift started without face recognition, Smart Search and watermark reading",
    );
    expect(scene.logged).toContain("backend.optional_held");
    expect(lastSaved().mode).toBe("standalone");
  });

  it("does not offer the held start again once it is the one that stopped", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(0, 0, 1);
    });
    scene.backends[0]?.gaveUp("it stopped four times", true);
    await settle();

    scene.backends[1]?.gaveUp("and again", true);
    await settle();

    expect(electron.dialogCalls[2]?.buttons).toEqual(["Download log", "Quit"]);
    expect(electron.appCalls.quit).toBe(1);
  });

  it("offers the held start when the backend died while it was starting", async () => {
    const { BackendStartError } = await import("./backend");
    scene.startFailures.push(
      new BackendStartError("Sift stopped while it was starting up.", "", true, 0xc0000135),
    );
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(2);
    });

    expect(electron.dialogCalls[0]?.detail).toContain("It couldn't start: a file it needs");

    expect(electron.dialogCalls[0]?.buttons?.[0]).toBe("Start without them");
    expect(electron.appCalls.quit).toBe(1);
  });

  it("downloads the log with no backend, shows the file, and asks again", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(0, 1);
    });

    scene.backends[0]?.gaveUp("it stopped three times");
    await settle();

    const asked = scene.bundled[0] as {
      appLogs: string;
      libraryLogs: string;
      into: string;
      facts: { sift: string };
    };
    expect(asked.appLogs).toBe(electron.app.getPath("userData"));
    expect(asked.libraryLogs).toBe(LIBRARY.dataDir);
    expect(asked.into).toBe(electron.app.getPath("downloads"));
    expect(asked.facts.sift).toBe(electron.app.getVersion());
    expect(electron.revealed).toEqual(["C:\\Downloads\\Sift log.zip"]);
    expect(electron.dialogCalls[1]?.title).toBe("Sift has stopped");
    expect(electron.appCalls.quit).toBe(1);
  });

  it("makes the page's log archive the same way, into the save folder", async () => {
    await boot({ mode: "standalone", ...LIBRARY, downloadDir: "E:\\Saved" });

    expect(await scene.logArchive("Sift log.zip")).toEqual({
      ok: true,
      file: "C:\\Downloads\\Sift log.zip",
    });
    scene.bundleAnswer = { ok: false, reason: "no room" };
    expect(await scene.logArchive("Sift log.zip")).toMatchObject({ ok: false });

    const asked = scene.bundled[0] as { name: string; into: string; libraryLogs: string };
    expect(asked).toMatchObject({ name: "Sift log.zip", into: "E:\\Saved", libraryLogs: LIBRARY.dataDir });
    expect(scene.logged).toContain("shell.log_download_failed");
  });

  it("says why the log could not be made, and asks again", async () => {
    scene.bundleAnswer = { ok: false, reason: "The folder is read-only." };
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(0, 0, 1);
    });

    scene.backends[0]?.gaveUp("it stopped three times");
    await settle();

    expect(electron.dialogCalls[1]?.message).toBe(
      "Sift couldn't make the log file",
    );
    expect(electron.dialogCalls[1]?.detail).toBe("The folder is read-only.");
    expect(electron.dialogCalls[2]?.title).toBe("Sift has stopped");
    expect(electron.revealed).toEqual([]);
    expect(scene.logged).toContain("shell.log_download_failed");
    expect(electron.appCalls.quit).toBe(1);
  });

  it("reads the update feed from the settings as they are now", async () => {
    await boot({
      mode: "standalone",
      ...LIBRARY,
      feedUrl: "https://updates.example/sift.json",
    });

    expect(scene.hooks.feedUrl()).toBe("https://updates.example/sift.json");
  });

  /* ONE feed: the backend's update check is handed the address this shell installs from. */
  it("hands the backend the feed it installs updates from", async () => {
    await boot({
      mode: "standalone",
      ...LIBRARY,
      feedUrl: "https://updates.example/sift.json",
    });
    expect(scene.backends[0]?.feed).toBe("https://updates.example/sift.json");
  });

  it("hands the backend Sift's own feed where the settings name none", async () => {
    const { DEFAULT_FEED_URL } = await import("./update");
    await boot({ mode: "standalone", ...LIBRARY });
    expect(scene.backends[0]?.feed).toBe(DEFAULT_FEED_URL);
  });

  it("offers the way back when the old library will not start again either", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      libraries: [{ ...OTHER, name: "Other", lastOpened: 1 }],
    });
    scene.startFailures.push(
      new Error("database is locked"),
      new Error("and so is this one"),
    );
    electron.dialogAnswers.push(1);

    expect((await scene.hooks.libraries.open(OTHER.dataDir)).ok).toBe(false);
    await settle();

    expect(electron.dialogCalls[0]?.detail).toContain("database is locked");
    expect(electron.appCalls.quit).toBe(1);
  });

  it("draws nothing when the way back is taken after the window has gone", async () => {
    scene.libraryHere = false;
    const electron = await boot({ mode: "standalone", ...LIBRARY }, (stub) => {
      stub.dialogAnswers.push(1);
    });
    const window = windowOf(electron);
    (window.listeners.get("closed") as unknown as () => void)();

    scene.backends[0]?.gaveUp("it stopped three times", true, 1);
    await settle();

    expect(lastSaved().dataDir).toBeNull();
    expect(window.loaded).toEqual([LOCAL]);
  });

  it("stops the backend before an update installs", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    await scene.hooks.stopForUpdate();

    expect(scene.backends[0]?.stopped).toBe(1);
  });

  /* Restart is a quit and a fresh launch, in that order, with the backend stopped cleanly before
	   either: a relaunch asked for while the backend still held the database would start a copy
	   that finds it busy. */
  it("restarts by stopping the backend, then asking for a fresh copy, then quitting", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    let stoppedAtRelaunch: number | undefined;
    const relaunch = vi.fn(() => {
      stoppedAtRelaunch = scene.backends[0]?.stopped;
    });
    (electron.app as unknown as { relaunch: () => void }).relaunch = relaunch;

    await scene.hooks.restart();

    expect(relaunch).toHaveBeenCalledTimes(1);
    expect(stoppedAtRelaunch, "relaunched before the backend had stopped").toBe(
      1,
    );
    expect(electron.appCalls.quit).toBe(1);
  });
});

describe("a copy pointed at another computer", () => {
  it("checks the saved address answers as Sift before loading it", async () => {
    const electron = await boot(
      { mode: "client", lastServer: `${SERVER}/` },
      (stub) => {
        stub.fetchAnswers.push(aSift(stub));
      },
    );

    expect(electron.fetched).toEqual([`${SERVER}/health`]);
    expect(windowOf(electron).loaded).toEqual([SERVER]);
    expect(lastSaved().lastServer).toBe(SERVER);
    expect(scene.backends).toHaveLength(0);
    /* The library is another computer's; nothing here may offer to delete it. */
    expect(scene.uninstaller).toEqual([null]);
    expect(scene.hooks.servers.problem()).toBeNull();
  });

  it("takes a 401 from a Sift as an answer: it is there and wants a sign-in", async () => {
    const electron = await boot(
      { mode: "client", servers: [{ label: "x", origin: SERVER }] },
      (stub) => {
        stub.fetchAnswers.push(aSift(stub, 401));
      },
    );

    expect(windowOf(electron).loaded).toEqual([SERVER]);
  });

  it("shows the connect screen, with what went wrong, when the address is not a Sift", async () => {
    const electron = await boot(
      { mode: "client", lastServer: SERVER },
      (stub) => {
        stub.fetchAnswers.push(stub.reply({ status: "ok" }));
      },
    );

    expect(windowOf(electron).loaded).toEqual([`${SHELL}/connect`]);
    expect(scene.hooks.servers.problem()).toBe(
      `Sift could not reach ${SERVER}. Something answered at that address, but it was not Sift.`,
    );
  });

  it("asks for an address when there is none", async () => {
    const electron = await boot({ mode: "client" });

    expect(windowOf(electron).loaded).toEqual([`${SHELL}/connect`]);
    expect(electron.fetched).toEqual([]);
  });

  it("goes to the connect screen, not an error box, when the load itself fails", async () => {
    const electron = await boot(
      { mode: "client", lastServer: SERVER },
      (stub) => {
        stub.fetchAnswers.push(aSift(stub));
        stub.BrowserWindow.loadFailures.push(
          new Error("ERR_CONNECTION_REFUSED"),
        );
      },
    );

    expect(windowOf(electron).loaded).toEqual([SERVER, `${SHELL}/connect`]);
    expect(electron.dialogCalls).toEqual([]);
    expect(scene.hooks.servers.problem()).toBe(
      `Sift could not reach ${SERVER}. ERR_CONNECTION_REFUSED`,
    );
  });

  it("refuses to save an address that is not one, or that nothing answers at", async () => {
    const electron = await boot({ mode: "client" });

    expect(
      await scene.hooks.servers.remember("not an address at all"),
    ).toContain("does not look like an address");
    electron.fetchAnswers.push(new Error("ECONNREFUSED"));
    expect(await scene.hooks.servers.remember(SERVER)).toContain(
      "Nothing answered at that address",
    );
    expect(scene.saved).toEqual([]);
  });

  /* Plain http outside the local network is refused before anything is sent to it. */
  it("refuses a plain-http address outside the local network without asking it anything", async () => {
    const electron = await boot({ mode: "client" });

    expect(
      await scene.hooks.servers.remember("http://sift.example.com"),
    ).toContain("https://");
    expect(electron.fetched).toEqual([]);
    expect(scene.saved).toEqual([]);
  });

  it("sends a saved plain-http address outside the local network to the connect screen", async () => {
    const electron = await boot({
      mode: "client",
      lastServer: "http://sift.example.com",
    });

    expect(windowOf(electron).loaded).toEqual([`${SHELL}/connect`]);
    expect(electron.fetched).toEqual([]);
    expect(scene.hooks.servers.problem()).toContain("https://");
  });

  /* Every window is watched for presses: without it, a remote page's Paste button never works. */
  it("watches the window for presses", async () => {
    const electron = await boot(
      { mode: "client", lastServer: SERVER },
      (stub) => {
        stub.fetchAnswers.push(aSift(stub));
      },
    );

    expect(windowOf(electron).pageListeners.has("input-event")).toBe(true);
  });

  it("saves an address that answers, once, and loads it", async () => {
    const electron = await boot({ mode: "client" });
    electron.fetchAnswers.push(aSift(electron), aSift(electron));

    expect(await scene.hooks.servers.remember(SERVER)).toBeNull();
    expect(await scene.hooks.servers.remember(`${SERVER}/`)).toBeNull();

    expect(lastSaved().servers).toEqual([{ label: SERVER, origin: SERVER }]);
    expect(scene.hooks.servers.last()).toBe(SERVER);
    expect(scene.hooks.servers.saved()).toEqual([
      { label: SERVER, origin: SERVER },
    ]);
    expect(windowOf(electron).loaded.at(-1)).toBe(SERVER);
  });

  it("forgets an address, and the last address with it when it is the same one", async () => {
    const other = "http://192.168.1.30:5171";
    await boot({
      mode: "client",
      lastServer: SERVER,
      servers: [
        { label: "a", origin: SERVER },
        { label: "b", origin: other },
      ],
    });

    expect(scene.hooks.servers.forget(other)).toEqual([
      { label: "a", origin: SERVER },
    ]);
    expect(lastSaved().lastServer).toBe(SERVER);
    expect(scene.hooks.servers.forget(`${SERVER}/`)).toEqual([]);
    expect(lastSaved().lastServer).toBeNull();
  });

  it("trusts the shell's own pages and the server it was pointed at, and nothing else", async () => {
    await boot({
      mode: "client",
      lastServer: SERVER,
      servers: [{ label: "a", origin: SERVER }],
    });

    expect(scene.trust(`${SHELL}/connect`)).toBe("local");
    expect(scene.trust(`${SERVER}/browse`)).toBe("remote");
    expect(scene.trust(`${LOCAL}/browse`)).toBe("local");
    expect(scene.trust("https://example.com/")).toBeNull();
  });
});

describe("the settings the verbs read and write", () => {
  it("says whether the library is shared now, and on which address", async () => {
    await boot({ mode: "standalone", ...LIBRARY, shareOnNetwork: true });

    expect(scene.hooks.sharing.read()).toEqual({
      enabled: true,
      live: true,
      mode: "standalone",
      address: "http://10.0.0.9:5171",
      port: 5171,
    });
    expect(scene.backends[0]?.sharing).toBe(true);
  });

  it("writes sharing first and re-addresses the running backend", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    await scene.hooks.sharing.write(true);

    expect(lastSaved().shareOnNetwork).toBe(true);
    expect(scene.backends[0]?.listened).toEqual([true]);
    expect(scene.hooks.sharing.read().live).toBe(true);
  });

  it("keeps the setting but reports it is not live when the restart fails", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.listenAnswer = false;

    await scene.hooks.sharing.write(true);

    expect(scene.hooks.sharing.read()).toMatchObject({
      enabled: true,
      live: false,
    });
  });

  it("refuses sharing for a copy pointed at another computer", async () => {
    await boot({ mode: "client" });

    await scene.hooks.sharing.write(true);

    expect(scene.saved).toEqual([]);
    expect(scene.logged).toContain("sharing.refused");
  });

  it("writes sharing without a backend to re-address when there is none running", async () => {
    await boot({ mode: "standalone" });

    await scene.hooks.sharing.write(true);

    expect(lastSaved().shareOnNetwork).toBe(true);
    expect(scene.hooks.sharing.read().live).toBe(true);
  });

  it("remembers the chosen browser and download folder", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    electron.paths.downloads = "C:\\Users\\somebody\\Downloads";

    scene.hooks.links.choose("firefox");
    expect(scene.hooks.links.chosen()).toBe("firefox");
    expect(scene.hooks.downloads.folder()).toBe(
      "C:\\Users\\somebody\\Downloads",
    );
    scene.hooks.downloads.choose("E:\\Saved");
    expect(scene.hooks.downloads.chosen()).toBe("E:\\Saved");
    expect(scene.hooks.downloads.folder()).toBe("E:\\Saved");
    expect(lastSaved()).toMatchObject({
      browser: "firefox",
      downloadDir: "E:\\Saved",
    });
  });

  it("puts the tray icon up and takes it down as close-to-tray is switched", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    expect(scene.trays).toHaveLength(1);

    scene.hooks.closing.keep(false);
    expect(scene.trays[0]?.destroyed).toBe(true);
    expect(scene.hooks.closing.keepRunning()).toBe(false);

    scene.hooks.closing.keep(true);
    expect(scene.trays).toHaveLength(2);
    /* Idempotent: a second "on" does not stack a second icon in the notification area. */
    scene.hooks.closing.keep(true);
    expect(scene.trays).toHaveLength(2);
  });

  it("the tray opens the window and quits", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    scene.trays[0]?.verbs.open();
    scene.trays[0]?.verbs.quit();

    expect(windowOf(electron).focused).toBe(1);
    expect(electron.appCalls.quit).toBe(1);
  });

  it("forgets the mode for the settings screen, and nothing else", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    scene.hooks.storage.forget();

    expect(lastSaved()).toMatchObject({ mode: null, dataDir: LIBRARY.dataDir });
  });
});

describe("moving the library", () => {
  it("stops, moves, writes the new folders, and starts again, in that order", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.order = [];
    const heard: number[] = [];

    const outcome = await scene.hooks.storage.move("D:\\Other", (copied) =>
      heard.push(copied),
    );

    expect(outcome).toEqual({ ok: true, locations: OTHER, renamed: false });
    expect(scene.order).toEqual([
      "backend.stop",
      "storage.move",
      "backend.start",
    ]);
    expect(heard).toEqual([1]);
    expect(scene.backends.at(-1)?.locations).toEqual(OTHER);
    expect(scene.uninstaller.at(-1)).toEqual(OTHER);
  });

  it("starts again on the old folder when the move is refused", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.moveOutcome = { ok: false, reason: "not enough room" };

    expect(await scene.hooks.storage.move("D:\\Other", () => {})).toEqual({
      ok: false,
      reason: "not enough room",
    });
    expect(scene.backends.at(-1)?.locations).toEqual(LIBRARY);
  });

  it("refuses when there is no backend of this machine to stop", async () => {
    await boot({ mode: "client" });

    expect(await scene.hooks.storage.move("D:\\Other", () => {})).toMatchObject(
      { ok: false },
    );
    expect(scene.moved).toEqual([]);
  });

  it("describes where the two folders are", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    expect(await scene.hooks.storage.read()).toMatchObject(LIBRARY);
  });
});

/** What the backend's reading says of a library an older Sift has to bring up to date first. */
const TOO_OLD =
  "This library was last opened by a version of Sift too old for this one to bring forward. " +
  "Open it once with an older Sift, then open it here. Nothing has been changed.";

describe("switching library", () => {
  const known = (where: DataLocations) => ({
    ...where,
    name: "Other",
    lastOpened: 1,
  });

  it("lists what this copy has opened and which one is open", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    const list = scene.hooks.libraries.list();

    expect(list.current).toBe(LIBRARY.dataDir);
    expect(list.libraries.map((one) => one.dataDir)).toEqual([LIBRARY.dataDir]);
  });

  it("names no current library in a copy pointed at another computer", async () => {
    await boot({ mode: "client" });

    expect(scene.hooks.libraries.list().current).toBeNull();
  });

  it("refuses a library it has not opened before", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    expect(await scene.hooks.libraries.open(OTHER.dataDir)).toEqual({
      ok: false,
      refusal: "Sift has not opened that library before.",
    });
  });

  it("stops, starts on the other library, and loads it", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      libraries: [known(OTHER)],
    });

    expect(await scene.hooks.libraries.open(OTHER.dataDir)).toEqual({
      ok: true,
      refusal: null,
    });

    expect(scene.backends[0]?.stopped).toBe(1);
    expect(scene.backends.at(-1)?.locations).toEqual(OTHER);
    expect(lastSaved().dataDir).toBe(OTHER.dataDir);
    expect(electron.cookiesRemoved).toHaveLength(2);
    expect(windowOf(electron).loaded.at(-1)).toBe(LOCAL);
  });

  /* THE SERVER'S SWITCH: a page (in a browser anywhere) asked the backend, which checked the
   * library, left a note and stopped asking to be restarted. The backend's exit handler asks this
   * shell first; a note means the restart is this shell's to make, on the library named. */
  it("starts on the library the server named when its backend stops asking to be restarted", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    scene.note = OTHER;

    expect(scene.backends[0]?.takeOver()).toBe(true);
    await settle();

    expect(scene.notesTaken).toEqual([LIBRARY.dataDir]);
    expect(scene.backends.at(-1)?.locations).toEqual(OTHER);
    expect(lastSaved().dataDir).toBe(OTHER.dataDir);
    /* Signed out, as every switch is: the master key belonged to the backend that stopped. */
    expect(electron.cookiesRemoved).toHaveLength(2);
    expect(windowOf(electron).loaded.at(-1)).toBe(LOCAL);
  });

  it("leaves an ordinary restart alone when there is no note", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    expect(scene.backends[0]?.takeOver()).toBe(false);
    await settle();

    expect(scene.backends).toHaveLength(1);
  });

  it("starts the library that was open again when this machine refuses the one named", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.note = OTHER;
    scene.refusal = "That folder is on a network drive.";

    expect(scene.backends[0]?.takeOver()).toBe(true);
    await settle();

    expect(scene.backends.at(-1)?.locations).toEqual(LIBRARY);
    expect(lastSaved().dataDir).toBe(LIBRARY.dataDir);
    expect(scene.logged).toContain("library.switch_refused");
  });

  it("does nothing for the library already open", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    expect(await scene.hooks.libraries.open(LIBRARY.dataDir)).toEqual({
      ok: true,
      refusal: null,
    });
    expect(scene.backends[0]?.stopped).toBe(0);
  });

  it.each([
    ["empty", "That library is not there any more."],
    ["newer", "last opened by a newer version of Sift"],
    ["unreadable", "There is no Sift library in that folder"],
    ["unknown", "Sift could not read that folder"],
  ])(
    "refuses a remembered library that reads %s, before stopping anything",
    async (verdict, said) => {
      await boot({ mode: "standalone", ...LIBRARY, libraries: [known(OTHER)] });
      scene.verdict = verdict;

      const outcome = await scene.hooks.libraries.open(OTHER.dataDir);

      expect(outcome.ok).toBe(false);
      expect(outcome.refusal).toContain(said);
      expect(scene.backends[0]?.stopped).toBe(0);
    },
  );

  it("says the reading's own sentence for a library too old to bring forward", async () => {
    await boot({ mode: "standalone", ...LIBRARY, libraries: [known(OTHER)] });
    scene.verdict = "unreadable";
    scene.detail = TOO_OLD;

    const outcome = await scene.hooks.libraries.open(OTHER.dataDir);

    expect(outcome).toEqual({ ok: false, refusal: TOO_OLD });
    expect(scene.backends[0]?.stopped).toBe(0);
  });

  it("asks before upgrading an older library, and a no is not a failure", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      libraries: [known(OTHER)],
    });
    scene.verdict = "older";
    electron.dialogAnswers.push(1);

    expect(await scene.hooks.libraries.open(OTHER.dataDir)).toEqual({
      ok: false,
      refusal: null,
    });
    expect(electron.dialogCalls[0]?.defaultId).toBe(0);
    expect(scene.backends[0]?.stopped).toBe(0);
  });

  it("will not upgrade without the backup it promised", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      libraries: [known(OTHER)],
    });
    scene.verdict = "older";
    scene.backup = null;
    electron.dialogAnswers.push(0);

    expect((await scene.hooks.libraries.open(OTHER.dataDir)).refusal).toContain(
      "could not back up",
    );
    expect(scene.backends[0]?.stopped).toBe(0);
  });

  it("upgrades once the backup is made", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      libraries: [known(OTHER)],
    });
    scene.verdict = "older";
    electron.dialogAnswers.push(0);

    expect(await scene.hooks.libraries.open(OTHER.dataDir)).toEqual({
      ok: true,
      refusal: null,
    });
    expect(scene.logged).toContain("library.backed_up");
  });

  it("goes back to the library that was open when the other will not start", async () => {
    await boot({ mode: "standalone", ...LIBRARY, libraries: [known(OTHER)] });
    scene.startFailures.push(new Error("database is locked"));

    const outcome = await scene.hooks.libraries.open(OTHER.dataDir);

    expect(outcome).toEqual({
      ok: false,
      refusal: "Sift could not open that library. database is locked",
    });
    expect(scene.backends.at(-1)?.locations).toEqual(LIBRARY);
  });

  it("adds a library through the file picker only, and refuses what the picker refuses", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    /* Closing the picker is not a failure and has nothing to say. */
    expect(await scene.hooks.libraries.add()).toEqual({
      ok: false,
      refusal: null,
    });
    electron.folderAnswers.push([`${OTHER.dataDir}\\sift.sqlite3`]);
    scene.refusal = "Sift cannot write to that folder.";
    expect(await scene.hooks.libraries.add()).toEqual({
      ok: false,
      refusal: "Sift cannot write to that folder.",
    });
    scene.refusal = null;
    scene.verdict = "empty";
    /* A database somebody has just CHOSEN is there; empty is an interrupted first start. */
    electron.folderAnswers.push([`${OTHER.dataDir}\\sift.sqlite3`]);
    expect(await scene.hooks.libraries.add()).toEqual({
      ok: true,
      refusal: null,
    });
    expect(scene.backends.at(-1)?.locations).toEqual(OTHER);
    expect(scene.adopted).toEqual([]);
  });

  it("refuses a file that is not a database before asking anything", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    electron.folderAnswers.push(["D:\\Backups\\holiday.jpg"]);

    expect((await scene.hooks.libraries.add()).refusal).toContain(".sqlite3");
    expect(electron.dialogCalls).toHaveLength(0);
  });

  it("refuses to switch in a copy pointed at another computer", async () => {
    const electron = await boot({ mode: "client" });
    electron.folderAnswers.push([`${OTHER.dataDir}\\sift.sqlite3`]);

    expect((await scene.hooks.libraries.add()).refusal).toContain(
      "not running its own library",
    );
  });

  describe("a database file that is not a library of its own", () => {
    /* Somewhere that is certainly not there, so the folder the plan names does not exist. */
    const beside = () =>
      path.join(os.tmpdir(), `sift-no-such-${process.pid}-${Date.now()}`);

    it("says the folder it will make, makes it only on a yes, and opens the copy", async () => {
      const electron = await boot({ mode: "standalone", ...LIBRARY });
      const file = path.join(
        beside(),
        "sift-before-upgrade-20260901-120000.sqlite3",
      );
      const made = path.join(
        path.dirname(file),
        "sift-before-upgrade-20260901-120000",
      );
      electron.folderAnswers.push([file]);
      electron.dialogAnswers.push(0);

      expect(await scene.hooks.libraries.add()).toEqual({
        ok: true,
        refusal: null,
      });

      expect(electron.dialogCalls).toHaveLength(1);
      expect(electron.dialogCalls[0]?.message).toBe(
        "Sift will create a library folder called sift-before-upgrade-20260901-120000 next to this file.",
      );
      expect(electron.dialogCalls[0]?.defaultId).toBe(0);
      expect(scene.adopted).toEqual([[file, path.join(made, "data")]]);
      /* Asked, then copied, then stopped: nothing is stopped for a copy that was never made. */
      expect(scene.order.indexOf("adopt")).toBeLessThan(
        scene.order.lastIndexOf("backend.stop"),
      );
      expect(scene.backends.at(-1)?.locations).toEqual({
        dataDir: path.join(made, "data"),
        cacheDir: path.join(made, "cache"),
      });
      expect(scene.logged).toContain("library.adopted");
    });

    it("asks ONCE for an older copy, and still makes the backup it promised", async () => {
      const electron = await boot({ mode: "standalone", ...LIBRARY });
      electron.folderAnswers.push([path.join(beside(), "given.sqlite3")]);
      scene.fileVerdict = "older";
      scene.verdict = "older";
      electron.dialogAnswers.push(0);

      expect(await scene.hooks.libraries.add()).toEqual({
        ok: true,
        refusal: null,
      });

      expect(electron.dialogCalls).toHaveLength(1);
      expect(electron.dialogCalls[0]?.buttons).toEqual([
        "Upgrade and open",
        "Cancel",
      ]);
      expect(scene.logged).toContain("library.backed_up");
    });

    it("makes nothing when the answer is no", async () => {
      const electron = await boot({ mode: "standalone", ...LIBRARY });
      electron.folderAnswers.push([path.join(beside(), "given.sqlite3")]);
      electron.dialogAnswers.push(1);

      expect(await scene.hooks.libraries.add()).toEqual({
        ok: false,
        refusal: null,
      });
      expect(scene.adopted).toEqual([]);
      expect(scene.backends[0]?.stopped).toBe(0);
    });

    it.each([
      ["newer", "last opened by a newer version of Sift"],
      ["unreadable", "not a Sift library this copy can read"],
      ["empty", "not a Sift library"],
      ["unknown", "could not read that file"],
    ])(
      "refuses a file that reads %s before asking or making anything",
      async (verdict, said) => {
        const electron = await boot({ mode: "standalone", ...LIBRARY });
        electron.folderAnswers.push([path.join(beside(), "given.sqlite3")]);
        scene.fileVerdict = verdict;

        const outcome = await scene.hooks.libraries.add();

        expect(outcome.ok).toBe(false);
        expect(outcome.refusal).toContain(said);
        expect(electron.dialogCalls).toHaveLength(0);
        expect(scene.adopted).toEqual([]);
      },
    );

    it("says the reading's own sentence for a file too old to bring forward", async () => {
      const electron = await boot({ mode: "standalone", ...LIBRARY });
      electron.folderAnswers.push([path.join(beside(), "given.sqlite3")]);
      scene.fileVerdict = "unreadable";
      scene.fileDetail = TOO_OLD;

      const outcome = await scene.hooks.libraries.add();

      expect(outcome).toEqual({ ok: false, refusal: TOO_OLD });
      expect(electron.dialogCalls).toHaveLength(0);
      expect(scene.adopted).toEqual([]);
    });

    it("never makes a library into a folder that is already there", async () => {
      const electron = await boot({ mode: "standalone", ...LIBRARY });
      const holding = fs.mkdtempSync(path.join(os.tmpdir(), "sift-beside-"));
      fs.mkdirSync(path.join(holding, "given"));
      electron.folderAnswers.push([path.join(holding, "given.sqlite3")]);

      const outcome = await scene.hooks.libraries.add();

      expect(outcome.refusal).toContain("already a folder called given");
      expect(electron.dialogCalls).toHaveLength(0);
      expect(scene.adopted).toEqual([]);
      fs.rmSync(holding, { recursive: true, force: true });
    });

    it("refuses a database file in a copy pointed at another computer, before asking", async () => {
      const electron = await boot({ mode: "client" });
      electron.folderAnswers.push([path.join(beside(), "given.sqlite3")]);

      expect((await scene.hooks.libraries.add()).refusal).toContain(
        "not running its own library",
      );
      expect(electron.dialogCalls).toHaveLength(0);
      expect(scene.adopted).toEqual([]);
    });

    it("copies nothing and stops nothing when the folder checks refuse after the yes", async () => {
      const electron = await boot({ mode: "standalone", ...LIBRARY });
      electron.folderAnswers.push([path.join(beside(), "given.sqlite3")]);
      electron.dialogAnswers.push(0);
      scene.refusal = "Sift cannot write to that folder.";

      expect(await scene.hooks.libraries.add()).toEqual({
        ok: false,
        refusal: "Sift cannot write to that folder.",
      });
      expect(electron.dialogCalls).toHaveLength(1);
      expect(scene.adopted).toEqual([]);
      expect(scene.backends[0]?.stopped).toBe(0);
    });

    it("opens nothing when the copy could not be made", async () => {
      const electron = await boot({ mode: "standalone", ...LIBRARY });
      electron.folderAnswers.push([path.join(beside(), "given.sqlite3")]);
      electron.dialogAnswers.push(0);
      scene.adoptWorks = false;

      expect((await scene.hooks.libraries.add()).refusal).toContain(
        "could not copy that database",
      );
      expect(scene.backends[0]?.stopped).toBe(0);
    });
  });

  it("forgets a library from the list", async () => {
    await boot({ mode: "standalone", ...LIBRARY, libraries: [known(OTHER)] });

    const after = scene.hooks.libraries.forget(OTHER.dataDir);

    expect(after.libraries.map((one) => one.dataDir)).toEqual([
      LIBRARY.dataDir,
    ]);
  });
});

describe("the window", () => {
  it("sends a link to a new window out to the browser, never into Sift", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    const window = windowOf(electron);

    expect(window.openHandler?.({ url: "https://example.com/a" })).toEqual({
      action: "deny",
    });
    expect(
      window.openHandler?.({ url: "file:///C:/Windows/system32/calc.exe" }),
    ).toEqual({
      action: "deny",
    });

    /* Only a web address leaves the application; anything else is dropped without a word. */
    expect(electron.openedExternally).toEqual(["https://example.com/a"]);
  });

  it("opens a link in the browser somebody chose, and in the default one if that fails", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      browser: "firefox",
    });
    const window = windowOf(electron);

    window.openHandler?.({ url: "https://example.com/a" });
    scene.openInWorks = false;
    window.openHandler?.({ url: "https://example.com/b" });

    expect(scene.openedIn).toEqual([
      ["firefox", "https://example.com/a"],
      ["firefox", "https://example.com/b"],
    ]);
    expect(electron.openedExternally).toEqual(["https://example.com/b"]);
  });

  it("lets the page navigate within Sift and sends anywhere else out", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    const navigate = windowOf(electron).pageListeners.get(
      "will-navigate",
    ) as unknown as (
      event: { preventDefault: () => void },
      url: string,
    ) => void;
    let prevented = 0;
    const event = { preventDefault: () => (prevented += 1) };

    navigate(event, `${LOCAL}/browse`);
    expect(prevented).toBe(0);

    navigate(event, "https://example.com/elsewhere");
    expect(prevented).toBe(1);
    expect(electron.openedExternally).toEqual([
      "https://example.com/elsewhere",
    ]);
  });

  it("hides on close while Sift keeps running, and closes once Sift is quitting", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    const window = windowOf(electron);
    const close = window.listeners.get("close") as unknown as (event: {
      preventDefault: () => void;
    }) => void;
    let prevented = 0;
    const event = { preventDefault: () => (prevented += 1) };

    close(event);
    expect(prevented).toBe(1);
    expect(window.hidden).toBe(1);

    electron.appListeners.get("before-quit")?.();
    close(event);
    expect(prevented).toBe(1);
    /* The way out stops the backend the clean way, so its log is folded. */
    expect(scene.backends[0]?.stopped).toBe(1);
  });

  it("closes straight away when close-to-tray is off", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      keepRunningWhenClosed: false,
    });
    const close = windowOf(electron).listeners.get(
      "close",
    ) as unknown as (event: { preventDefault: () => void }) => void;
    let prevented = 0;

    close({ preventDefault: () => (prevented += 1) });

    expect(prevented).toBe(0);
    expect(scene.trays).toHaveLength(0);
  });

  it("forgets the window once it is closed, so a second start has nothing to show", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });
    const window = windowOf(electron);

    (window.listeners.get("closed") as unknown as () => void)();
    electron.appListeners.get("second-instance")?.();

    expect(window.shown).toBe(1);
  });

  it("stops the backend and then quits when the last window closes", async () => {
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    electron.appListeners.get("window-all-closed")?.();
    await settle();

    expect(scene.backends[0]?.stopped).toBe(1);
    expect(electron.appCalls.quit).toBe(1);
  });
});

describe("saving a file without asking", () => {
  let folder: string;

  beforeEach(() => {
    folder = fs.mkdtempSync(path.join(os.tmpdir(), "sift-main-"));
  });

  afterEach(() => {
    fs.rmSync(folder, { recursive: true, force: true });
  });

  function download(electron: Stub, name: string): string {
    const listener = electron.sessionListeners.get(
      "will-download",
    ) as unknown as (
      event: unknown,
      item: { getFilename: () => string; setSavePath: (to: string) => void },
    ) => void;
    let savedTo = "";
    listener(null, {
      getFilename: () => name,
      setSavePath: (to) => {
        savedTo = to;
      },
    });
    return savedTo;
  }

  it("saves into the chosen folder under the name Sift asked for", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      downloadDir: folder,
    });

    expect(download(electron, "clip.mp4")).toBe(path.join(folder, "clip.mp4"));
  });

  it("keeps both copies when the name is taken, the way Windows does", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      downloadDir: folder,
    });
    fs.writeFileSync(path.join(folder, "clip.mp4"), "");
    fs.writeFileSync(path.join(folder, "clip (1).mp4"), "");

    expect(download(electron, "clip.mp4")).toBe(
      path.join(folder, "clip (2).mp4"),
    );
  });

  /* Bounded: a thousand copies is somebody holding a key down, and the hunt for a free name stops
	   there rather than running for as long as that took. */
  it("stops counting at a thousand and hands back the name it was given", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      downloadDir: folder,
    });
    fs.writeFileSync(path.join(folder, "a"), "");
    for (let counter = 1; counter < 1000; counter += 1) {
      fs.writeFileSync(path.join(folder, `a (${counter})`), "");
    }

    expect(download(electron, "a")).toBe(path.join(folder, "a"));
  });

  it("falls back to the machine's Downloads when the chosen folder has gone", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      downloadDir: path.join(folder, "unplugged"),
    });
    electron.paths.downloads = folder;

    expect(download(electron, "clip.mp4")).toBe(path.join(folder, "clip.mp4"));
  });
});

/* Windows starting Sift at sign-in: the entry carries `--hidden`, and the start goes to the
   notification area (where close-to-tray already puts the window) rather than in front of
   whatever the person opened first. */
describe("a start Windows made at sign-in", () => {
  afterEach(() => {
    process.argv = process.argv.filter((one) => one !== "--hidden");
  });

  it("loads the library and stays in the notification area", async () => {
    process.argv.push("--hidden");
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      keepRunningWhenClosed: true,
    });
    const window = windowOf(electron);

    expect(scene.backends).toHaveLength(1);
    expect(window.loaded).toEqual([LOCAL]);
    expect(scene.trays).toHaveLength(1);
    expect(window.shown).toBe(0);
    expect(window.minimizes).toBe(0);

    /* The icon is the way in, exactly as after a close. */
    scene.trays[0]?.verbs.open();
    expect(window.shown).toBe(1);
  });

  /* No icon to go to: hidden, it would be an application nobody could find. */
  it("waits on the taskbar when close-to-tray is off", async () => {
    process.argv.push("--hidden");
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      keepRunningWhenClosed: false,
    });
    const window = windowOf(electron);

    expect(window.shown).toBe(0);
    expect(window.minimizes).toBe(1);
  });

  /* A question hidden in the notification area is one nobody would ever answer. */
  it("shows a first-run question whatever started it", async () => {
    process.argv.push("--hidden");
    const electron = await boot();

    expect(windowOf(electron).loaded).toEqual([`${SHELL}/start`]);
    expect(windowOf(electron).shown).toBe(1);
  });

  /* The sign-in start is spent by the first window it shows. Somebody who answered that question is
	   at the keyboard, and a library that then loaded out of sight would look like the answer doing
	   nothing. */
  it("shows the library a person has just answered their way to", async () => {
    process.argv.push("--hidden");
    const electron = await boot({
      mode: "standalone",
      keepRunningWhenClosed: true,
    });

    expect(await scene.hooks.setup.library(false, () => {})).toEqual({
      ok: true,
      refusal: null,
    });

    expect(windowOf(electron).loaded.at(-1)).toBe(LOCAL);
    expect(windowOf(electron).shown).toBe(2);
  });

  it("an ordinary start shows the window", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      keepRunningWhenClosed: true,
    });

    expect(windowOf(electron).shown).toBe(1);
  });

  /* A checkout's executable is Electron's own: the switch main hands the verbs answers nothing. */
  it("hands the verbs a switch that answers nothing from a checkout", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    expect(scene.hooks.startup.read()).toBeNull();
    expect(scene.hooks.startup.write(true)).toBeNull();
  });
});

describe("the development shell", () => {
  afterEach(() => {
    process.argv = process.argv.filter((one) => one !== "--dev");
  });

  it("runs the real backend and loads the development server", async () => {
    process.argv.push("--dev");
    const electron = await boot({ mode: "standalone", ...LIBRARY });

    expect(scene.backends).toHaveLength(1);
    expect(windowOf(electron).loaded).toEqual(["http://localhost:5173"]);
    expect(scene.trust("http://localhost:5173/browse")).toBe("local");
    expect(scene.trust(`${LOCAL}/browse`)).toBeNull();
  });
});
