// Prints `repositories=<name>,<name>,...` for $GITHUB_OUTPUT: the repositories the Renovate
// App token is scoped to. Read from global.js, so the token covers exactly the repositories
// Renovate works on, plus this repository (argv[2]), which holds the shared preset.
const { repositories } = require("./global.js");

const self = process.argv[2];
if (!self) throw new Error("usage: node token-scope.js <this-repository-name>");

const names = repositories.map((r) => (typeof r === "string" ? r : r.repository));
for (const n of names) {
  if (!n.startsWith("unified-systems-com/")) throw new Error(`outside the org: ${n}`);
}
const short = names.map((n) => n.split("/")[1]).concat(self);
console.log(`repositories=${[...new Set(short)].join(",")}`);
