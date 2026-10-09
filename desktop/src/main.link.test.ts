/* What `main.ts` answers when the backend asks for an act only this shell can do, for an admin
 * on another computer (`shelllink.ts`): refused in words before anything stops, or answered
 * first and done after. */

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DataLocations } from "./paths";
import {
  boot,
  LIBRARY,
  OTHER,
  resetScene,
  scene,
  settle,
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

describe("asked by the backend, for an admin on another computer", () => {
  const known = (where: DataLocations) => ({
    ...where,
    name: "Other",
    lastOpened: 1,
  });

  it("refuses a folder before anything stops, and moves into a good one only after answering", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.order = [];
    scene.storageRefusal = "That folder is not empty. Choose an empty one, or make a new one.";

    const refused = await scene.link.moveStorage("E:\\Full");
    expect(refused).toEqual({
      answer: { ok: false, refusal: scene.storageRefusal },
    });
    expect(scene.order).toEqual([]);

    scene.storageRefusal = null;
    const taken = await scene.link.moveStorage("D:\\Other");
    expect(taken.answer).toEqual({ ok: true, refusal: null });
    expect(scene.order).toEqual([]);
    await taken.after?.();
    expect(scene.order).toEqual(["backend.stop", "storage.move", "backend.start"]);
    expect(await scene.link.storage()).toMatchObject({
      ...OTHER,
      lastMove: { ok: true, refusal: null },
    });
  });

  it("refuses an older library in words and never raises the upgrade question on this screen", async () => {
    const electron = await boot({
      mode: "standalone",
      ...LIBRARY,
      libraries: [known(OTHER)],
    });
    scene.verdict = "older";

    const taken = await scene.link.openLibrary(OTHER.dataDir);

    expect(taken.after).toBeUndefined();
    expect(taken.answer.ok).toBe(false);
    expect(taken.answer.refusal).toContain("computer running Sift");
    expect(electron.dialogCalls).toEqual([]);
    expect(scene.backends[0]?.stopped).toBe(0);
  });

  it("opens a remembered library only after answering, and refuses one it has not opened", async () => {
    await boot({ mode: "standalone", ...LIBRARY, libraries: [known(OTHER)] });

    expect((await scene.link.openLibrary("Z:\\Nowhere\\data")).answer).toEqual({
      ok: false,
      refusal: "Sift has not opened that library before.",
    });
    expect((await scene.link.openLibrary(LIBRARY.dataDir)).answer.ok).toBe(false);
    const taken = await scene.link.openLibrary(OTHER.dataDir);
    expect(taken.answer).toEqual({ ok: true, refusal: null });
    expect(scene.backends[0]?.stopped).toBe(0);
    await taken.after?.();
    expect(scene.backends[0]?.stopped).toBe(1);
    expect(scene.backends.at(-1)?.locations).toEqual(OTHER);
    expect(scene.link.libraries().current).toBe(OTHER.dataDir);
  });

  it("changes sharing only after answering", async () => {
    await boot({ mode: "standalone", ...LIBRARY, shareOnNetwork: true });

    const taken = await scene.link.setSharing(false);
    expect(taken.answer).toEqual({ ok: true, refusal: null });
    expect(scene.backends[0]?.listened).toEqual([]);
    await taken.after?.();
    expect(scene.backends[0]?.listened).toEqual([false]);
  });

  it("answers an update once it is verified, and stops the backend only after that answer", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    let launched = false;
    scene.update = async (hooks) => {
      await hooks.beforeLaunch?.("0.1.300");
      launched = true;
      return { ok: true, version: "0.1.300" };
    };

    const taken = await scene.link.update();
    expect(taken.answer).toEqual({ ok: true, version: "0.1.300" });
    expect(scene.backends[0]?.stopped).toBe(0);
    await taken.after?.();
    await settle();
    expect(scene.backends[0]?.stopped).toBe(1);
    expect(launched).toBe(true);
  });

  it("answers an update refused, and stops nothing", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.update = async () => ({ ok: false, reason: "none" });

    const taken = await scene.link.update();

    expect(taken).toEqual({ answer: { ok: false, reason: "none" } });
    expect(scene.backends[0]?.stopped).toBe(0);
  });

  it("puts the library back on the air when the installer does not open after the stop", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.update = async (hooks) => {
      await hooks.beforeLaunch?.("0.1.300");
      return { ok: false, reason: "failed" };
    };

    const taken = await scene.link.update();
    await taken.after?.();
    await settle();

    expect(scene.backends[0]?.stopped).toBe(1);
    expect(scene.backends).toHaveLength(2);
  });

  it("answers an update that threw as failed, and stops nothing", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.update = async () => {
      throw new Error("the feed answered something unreadable");
    };

    const taken = await scene.link.update();

    expect(taken).toEqual({ answer: { ok: false, reason: "failed" } });
    expect(scene.backends[0]?.stopped).toBe(0);
    expect(scene.backends).toHaveLength(1);
  });

  it("puts the library back on the air when the update throws after the stop", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    scene.update = async (hooks) => {
      await hooks.beforeLaunch?.("0.1.300");
      throw new Error("the installer could not be started");
    };

    const taken = await scene.link.update();
    expect(taken.answer).toEqual({ ok: true, version: "0.1.300" });
    await taken.after?.();
    await settle();

    expect(scene.backends[0]?.stopped).toBe(1);
    expect(scene.backends).toHaveLength(2);
  });

  it("names this computer and says what is shared, from the page's own variables", async () => {
    await boot({ mode: "standalone", ...LIBRARY, shareOnNetwork: true });
    const { PORT } = await import("./backend");

    expect(scene.link.machine()).toBe("STUDY-PC");
    expect(scene.link.sharing()).toEqual({
      enabled: true,
      live: true,
      address: `http://10.0.0.9:${PORT}`,
      port: PORT,
    });

    /* A restart that did not take: the setting moved and what is live did not, on both readers. */
    scene.listenAnswer = false;
    await (await scene.link.setSharing(false)).after?.();
    expect(scene.link.sharing()).toMatchObject({ enabled: false, live: true });
    expect(scene.hooks.sharing.read()).toMatchObject({
      enabled: false,
      live: true,
    });
  });

  it("reads and opens the firewall on the port the backend listens on, in the scope asked", async () => {
    await boot({ mode: "standalone", ...LIBRARY });
    const { PORT } = await import("./backend");

    expect(await scene.link.firewall()).toMatchObject({ state: "closed" });
    expect(await scene.link.openFirewall("any")).toMatchObject({
      state: "open",
      scope: "any",
    });
    expect(scene.firewall).toEqual([
      ["read", PORT],
      ["open", PORT, undefined, "any"],
    ]);
  });

  it("reads the end of its own log", async () => {
    await boot({ mode: "standalone", ...LIBRARY });

    expect(scene.link.log(5).lines).toEqual(["5 lines"]);
  });
});
