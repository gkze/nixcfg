// Execute the exact patched vendor method with inert boundary implementations.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const { method, gate } = JSON.parse(fs.readFileSync(0, "utf8"));
const paths = {
  managedRoot: "/managed",
  pythonExecutable: "/managed/bin/python",
  localMoeServeExecutable: "/managed/bin/mach-serve",
};
const abortSignal = {};

async function run(scenario = {}) {
  const calls = [];
  const abortError = Object.assign(new Error("cancelled"), { name: "AbortError" });
  const engine = vm.runInNewContext(
    `(class {
    constructor() { this.context = {}; this.options = {}; }
    ${method}
  })`,
    {
      process: { env: {} },
      i0e: "zstandard>=0.23",
      eB: () => scenario.supported !== false,
      Dz: () => ({}),
      u0e: async () => (scenario.wheel ? null : { enginePath: "/bundle/source" }),
      LH: async () => (scenario.noWheel ? null : "/managed/engine.whl"),
      AB: async (path) =>
        !(scenario.missingSource && path === "/bundle/source") &&
        !(scenario.missingExecutable && path === paths.localMoeServeExecutable),
      MH: async () => {
        if (scenario.noUv) throw new Error("uv missing");
        return { binaryPath: "/uv" };
      },
      f0e: (error) => error.name === "AbortError",
    },
  );
  const instance = new engine();
  instance.options.onLog = (message) => calls.push({ log: message });
  instance.runCommand = async (executable, args, cwd, env, signal) => {
    calls.push({ executable, args: [...args], cwd, env, signal });
    if (scenario.cancel) throw abortError;
    if (scenario.installFails && executable === "/uv") throw new Error("install failed");
    if (scenario.importFails && executable === paths.pythonExecutable) {
      throw new Error("wrong import location");
    }
  };
  const result = await instance.provisionLocalMoeEngine(paths, abortSignal);
  return { result: JSON.parse(JSON.stringify(result)), calls };
}

const source = await run();
assert.deepEqual(source.result, { state: "ready" });
assert.equal(source.calls.length, 2);
assert.deepEqual(source.calls[0].args, [
  "pip",
  "install",
  "--python",
  paths.pythonExecutable,
  "--editable",
  "/bundle/source[dev,dflash]",
  "zstandard>=0.23",
]);
assert.equal(source.calls[1].executable, paths.pythonExecutable);
assert.equal(source.calls[1].args[0], "-c");
assert.equal(source.calls[1].args[2], "/bundle/source");
assert.equal(source.calls[0].signal, abortSignal);
assert.equal(source.calls[1].signal, abortSignal);
assert.equal(source.calls[1].cwd, paths.managedRoot);

const wheel = await run({ wheel: true });
assert.deepEqual(wheel.result, { state: "ready" });
assert.equal(wheel.calls.length, 1);
assert.deepEqual(wheel.calls[0].args, [
  "pip",
  "install",
  "--python",
  paths.pythonExecutable,
  "/managed/engine.whl[dflash]",
  "zstandard>=0.23",
]);

for (const scenario of [
  { noUv: true },
  { installFails: true },
  { importFails: true },
  { missingSource: true },
  { missingExecutable: true },
]) {
  const result = await run(scenario);
  assert.equal(result.result.state, "failed");
  assert.equal(typeof result.result.message, "string");
  assert.ok(result.result.message.length > 0);
  assert.ok(result.calls.some((call) => call.log));
}
await assert.rejects(run({ cancel: true }), (error) => error.name === "AbortError");
assert.deepEqual((await run({ supported: false })).result, { state: "unsupported-machine" });
const absent = await run({ wheel: true, noWheel: true });
assert.deepEqual(absent.result, { state: "not-configured" });
assert.equal(absent.calls.length, 0);

// Evaluate the exact new updater gate for every packaging/installation state.
for (const isPackaged of [false, true]) {
  for (const disabled of [false, true]) {
    for (const unsupportedInstallFormat of [false, true]) {
      const evaluate = vm.runInNewContext(`(e => { ${gate}; return [t,n]; })`);
      const state = evaluate({ app: { isPackaged }, disabled, unsupportedInstallFormat });
      assert.equal(state[0], true);
      assert.equal(state[1], false);
    }
  }
}
console.log(JSON.stringify({ validationScript: source.calls[1].args[1] }));
