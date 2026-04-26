const fs = require("node:fs");
const vm = require("node:vm");

const sourcePath = process.argv[2];
const prefs = [];

function pref(name, value) {
  prefs.push([name, value]);
}

vm.runInNewContext(fs.readFileSync(sourcePath, "utf8"), { pref }, { filename: sourcePath });

console.log(JSON.stringify(prefs));
