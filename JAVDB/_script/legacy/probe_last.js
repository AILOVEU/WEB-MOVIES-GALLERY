// Probe last page of javdb watched_videos via CDP
const { execSync } = require('child_process');
const fs = require('fs');
const NODE = 'C:/Users/wh/.workbuddy/binaries/node/versions/22.22.2-6/node.exe';
const SCRIPT = 'C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy/cdp_fetch.js';
const TMP = 'C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy/probe.html';

function itemCount(page) {
  execSync(`"${NODE}" "${SCRIPT}" "https://javdb.com/users/watched_videos?page=${page}" "${TMP}"`,
    { stdio: 'pipe' });
  const h = fs.readFileSync(TMP, 'utf8');
  const n = (h.match(/class="video-title"/g) || []).length;
  const next = /pagination-next/.test(h);
  return { n, next };
}

(async () => {
  // exponential search for first page with 0 items
  let lo = 1, hi = 2;
  while (true) {
    const r = itemCount(hi);
    console.log(`page ${hi}: ${r.n} items`);
    if (r.n === 0) break;
    lo = hi; hi *= 2;
    if (hi > 400) break;
  }
  // binary search between lo (has items) and hi (0 items)
  while (hi - lo > 1) {
    const mid = Math.floor((lo + hi) / 2);
    const r = itemCount(mid);
    console.log(`probe ${mid}: ${r.n}`);
    if (r.n > 0) lo = mid; else hi = mid;
  }
  const last = itemCount(lo + 1);
  console.log(`page ${lo+1} check: ${last.n}`);
  console.log('LAST_PAGE_WITH_ITEMS =', lo);
})();
