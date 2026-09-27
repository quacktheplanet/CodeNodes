// A live shape page in a real browser (headless Edge): it builds what Python builds, the
// sliders rebuild it, and a value the maths can't reach is reported without losing the model.
//
//   blender -b --factory-startup --python tests/test_web.py      (with CODENODES_SHAPE_PAGE_DIR set)
//   cd <folder with puppeteer-core installed> && node <repo>/tests/web_shape_check.mjs <dir> [screenshot.png]
import { createRequire } from "module";
import fs from "fs";
import path from "path";
import { pathToFileURL } from "url";

const require = createRequire(path.join(process.cwd(), "noop.js"));
const puppeteer = require("puppeteer-core");
const [dir, shot] = process.argv.slice(2);
const expected = JSON.parse(fs.readFileSync(path.join(dir, "lamp_expected.json"), "utf8"));
let checks = 0, failed = false;
const check = (ok, msg) => { checks++; console.log((ok ? "  ok: " : "FAIL: ") + msg); if (!ok) failed = true; };

const browser = await puppeteer.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: "new",
  args: ["--use-angle=d3d11"], defaultViewport: { width: 1100, height: 700 } });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.goto(pathToFileURL(path.join(dir, "lamp.html")).href, { waitUntil: "load" });
await page.waitForFunction("window.shapeStats", { timeout: 60000 });

const near = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 1e-4);
const stats = () => page.evaluate(() => window.shapeStats);
async function slide(values) {
  await page.evaluate((values) => {
    for (const ctl of document.querySelectorAll(".ctl")) {
      const name = ctl.querySelector("label").textContent.replace(/ /g, "_");
      if (name in values) {
        const input = ctl.querySelector("input");
        input.value = values[name];
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
  }, values);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  return stats();
}

let s = await stats();
check(s.faces === expected[0].faces && near(s.size, expected[0].size),
      `it opens as Python builds it at the tuned height (${s.faces} faces, size ${s.size.map((v) => v.toFixed(3))}, ${s.ms.toFixed(1)} ms)`);
for (const want of expected.slice(1)) {
  s = await slide(Object.assign({ height: 0.5, shade_r: 0.14, stem_r: 0.01 }, want.values));
  check(!s.error && s.faces === want.faces && near(s.size, want.size),
        `moving ${Object.keys(want.values).join(" and ")} rebuilds it to match Python (size ${s.size.map((v) => v.toFixed(3))}, ${s.ms.toFixed(1)} ms)`);
}
s = await slide({ base_r: 0.3 });
const status = await page.evaluate(() => document.getElementById("status").textContent);
const meshes = await page.evaluate(() => document.querySelector("canvas") !== null);
check(Boolean(s.error) && /too small/.test(status) && meshes,
      `a value the maths can't reach is explained, and the last model stays ("${status.slice(0, 60)}")`);
s = await slide({ base_r: 0.12 });
check(!s.error && s.faces === expected[0].faces, "and moving back builds again");
await page.click("#reset");
s = await stats();
check(near(s.size, expected[0].size), "Reset goes back to the tuned values");
check(errors.length === 0, `no script errors (${errors.join("; ") || "none"})`);
if (shot) await page.screenshot({ path: shot });
await browser.close();
console.log(failed ? "\nFAIL" : `\nALL ${checks} CHECKS PASSED`);
