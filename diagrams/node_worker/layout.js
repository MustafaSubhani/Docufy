// ELK layered layout worker. Reads one JSON graph per line on stdin, writes one JSON result per line.
// Kept alive by the Python side so layout costs milliseconds instead of a Node startup per diagram.
const ELK = require("elkjs/lib/elk.bundled.js");
const readline = require("readline");

const elk = new ELK();
const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on("line", async (line) => {
  if (!line.trim()) return;
  let req;
  try {
    req = JSON.parse(line);
  } catch (err) {
    process.stdout.write(JSON.stringify({ id: null, error: "bad json: " + err.message }) + "\n");
    return;
  }
  try {
    const graph = await elk.layout(req.graph);
    process.stdout.write(JSON.stringify({ id: req.id, graph }) + "\n");
  } catch (err) {
    process.stdout.write(JSON.stringify({ id: req.id, error: String(err && err.message ? err.message : err) }) + "\n");
  }
});
