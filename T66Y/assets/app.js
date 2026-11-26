/* T66Y 清单前端：标签筛选 + 关键词搜索 + 点过记录 + 虚拟滚动
 *
 * 数据分三层（见 _scripts/site/build.py 顶部说明）：
 *   index.js   元信息 + 每页标签位图 T66Y_PTAG（几十 KB，首屏就加载）
 *   shard_XXX  每片 50 页的完整记录（约 2 MB，按需注入）
 *   titles.js  全库标题索引（约 1 MB，只在输入关键词时加载）
 *
 * 筛选流程：先用位图算出「哪些页可能有命中」，只加载这些页所在的分片，
 * 再在记录级做精确筛选。结果集交给虚拟滚动，只渲染可见行。
 *
 * 点过记录：localStorage 存 tid -> 时间，支持取消、过滤（全部/只看/隐藏）。
 * 点标题打开原帖自动记为点过；行首 ○/✓ 按钮可手动切换。
 */
(function () {
  var META = window.T66Y_META || {};
  var PTAG = window.T66Y_PTAG || [];
  var TAGS = META.tags || [];
  var PPS = META.pages_per_shard || 50;
  var PAGE_MIN = META.page_min || 1;
  var PAGE_MAX = META.page_max || 0;

  var TAGMAP = {};
  TAGS.forEach(function (t, i) { t.idx = i; TAGMAP[t.key] = t; });

  var el = {
    list: document.getElementById('vlist'),
    viewport: document.getElementById('viewport'),
    spacer: document.getElementById('spacer'),
    empty: document.getElementById('empty'),
    chips: document.getElementById('tagchips'),
    q: document.getElementById('q'),
    foot: document.getElementById('foot'),
    stat: document.getElementById('stat'),
    sort: document.getElementById('sort'),
    seenfilter: document.getElementById('seenfilter'),
    seencount: document.getElementById('seencount'),
    clearseen: document.getElementById('clearseen'),
    reset: document.getElementById('reset'),
    active: document.getElementById('activefilters'),
    loading: document.getElementById('loading'),
    meta: document.getElementById('meta')
  };

  /* ---------------- 状态 ---------------- */
  var KEY = 't66y_state_v1';
  var SEEN_KEY = 't66y_seen_v1';
  var state = { tags: [], kw: '', sort: 'page', hideStick: false, depth: 100, seenFilter: 'all' };
  try {
    var s = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (s && typeof s === 'object') {
      state.tags = (s.tags || []).filter(function (k) { return TAGMAP[k]; });
      state.kw = s.kw || '';
      state.sort = s.sort || 'page';
      state.hideStick = !!s.hideStick;
      if (s.seenFilter === 'only' || s.seenFilter === 'hide') state.seenFilter = s.seenFilter;
    }
  } catch (e) {}
  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify({
        tags: state.tags, kw: state.kw, sort: state.sort,
        hideStick: state.hideStick, seenFilter: state.seenFilter
      }));
    } catch (e) {}
  }

  /* 点过记录：tid -> 时间戳。独立于筛选状态，单独存。 */
  var seen = {};
  try {
    var sv = JSON.parse(localStorage.getItem(SEEN_KEY) || 'null');
    if (sv && typeof sv === 'object') seen = sv;
  } catch (e) {}
  function saveSeen() {
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch (e) {}
    updateSeenCount();
  }
  function isSeen(tid) { return !!seen[String(tid)]; }
  function markSeen(tid, on) {
    tid = String(tid);
    if (on) seen[tid] = Date.now();
    else delete seen[tid];
    saveSeen();
  }
  function updateSeenCount() {
    var n = 0;
    for (var k in seen) if (seen.hasOwnProperty(k)) n++;
    el.seencount.textContent = '已点过 ' + n;
  }

  /* ---------------- 分片加载 ---------------- */
  var shardState = {};      // 序号 -> 'loading' | 'loaded'
  var shardWait = {};
  window.T66Y_PAGE = window.T66Y_PAGE || {};

  function pad3(n) { n = String(n); while (n.length < 3) n = '0' + n; return n; }

  function ensureShard(si, cb) {
    if (shardState[si] === 'loaded') { cb(true); return; }
    if (shardState[si] === 'loading') { (shardWait[si] = shardWait[si] || []).push(cb); return; }
    shardState[si] = 'loading';
    shardWait[si] = [cb];
    var sc = document.createElement('script');
    sc.src = 'data/shard_' + pad3(si) + '.js';
    sc.onload = function () { settle(si, true); };
    sc.onerror = function () { settle(si, false); };
    document.head.appendChild(sc);
  }
  function settle(si, ok) {
    shardState[si] = ok ? 'loaded' : undefined;
    var ws = shardWait[si] || [];
    shardWait[si] = [];
    for (var i = 0; i < ws.length; i++) ws[i](ok);
  }

  /* ---------------- 标题索引（关键词搜索，按分片渐进加载）---------------- */
  var titleShards = {};
  var titleState = {};
  var titleWait = {};

  function parseTitles(raw) {
    var out = [];
    if (!raw) return out;
    var lines = raw.split('\n');
    for (var i = 0; i < lines.length; i++) {
      var p = lines[i].split('\t');
      if (p.length >= 3) out.push({ tid: p[0], page: parseInt(p[1], 10), title: p[2] });
    }
    return out;
  }

  function ensureTitleShard(si, cb) {
    if (titleState[si] === 'loaded') { cb(true); return; }
    if (titleState[si] === 'loading') { (titleWait[si] = titleWait[si] || []).push(cb); return; }
    titleState[si] = 'loading';
    titleWait[si] = [cb];
    var sc = document.createElement('script');
    sc.src = 'data/titles_' + pad3(si) + '.js';
    sc.onload = function () {
      titleShards[si] = parseTitles((window.T66Y_T || {})[si]);
      settleTitle(si, true);
    };
    sc.onerror = function () { settleTitle(si, false); };
    document.head.appendChild(sc);
  }
  function settleTitle(si, ok) {
    titleState[si] = ok ? 'loaded' : undefined;
    var ws = titleWait[si] || [];
    titleWait[si] = [];
    for (var i = 0; i < ws.length; i++) ws[i](ok);
  }

  function scanTitles(kw, cap, done) {
    var shardList = (META.title_shards || []).map(function (n) { return parseInt(n.replace('titles_', ''), 10); });
    var i = 0, hits = [];
    function next() {
      if (i >= shardList.length || hits.length >= cap) { done(hits, i >= shardList.length); return; }
      var si = shardList[i++];
      el.loading.textContent = '搜索中… ' + i + '/' + shardList.length + ' 片（已命中 ' + hits.length + '）';
      ensureTitleShard(si, function (ok) {
        if (ok) {
          var arr = titleShards[si] || [];
          for (var j = 0; j < arr.length; j++) {
            if (arr[j].title.toLowerCase().indexOf(kw) >= 0) hits.push(arr[j]);
          }
        }
        next();
      });
    }
    next();
  }

  /* ---------------- 工具 ---------------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m];
    });
  }
  function tagChip(k, small) {
    var t = TAGMAP[k];
    if (!t) return '';
    var c = t.color ? ' style="--tc:' + t.color + '"' : '';
    return '<span class="tg' + (small ? ' sm' : '') + (t.struct ? ' st' : '') + '"' + c + '>'
      + esc(t.label) + '</span>';
  }

  /* ---------------- 候选页计算 ---------------- */
  function candidatePages(kwHits) {
    var wantIdx = state.tags.map(function (k) { return TAGMAP[k].idx; });
    var pages = [];

    if (state.kw) {
      var allow = {};
      (kwHits || []).forEach(function (h) { allow[h.page] = 1; });
      for (var pg = PAGE_MIN; pg <= PAGE_MAX; pg++) {
        if (!allow[pg]) continue;
        if (wantIdx.length && !pageHasAllTags(pg, wantIdx)) continue;
        pages.push(pg);
      }
      return pages;
    }

    if (wantIdx.length) {
      for (var p = PAGE_MIN; p <= PAGE_MAX; p++) {
        if (pageHasAllTags(p, wantIdx)) pages.push(p);
      }
      return pages;
    }

    // 无筛选：只给最新的 state.depth 页，其余靠「加载更早的页」追加
    var n = Math.min(PAGE_MAX - PAGE_MIN + 1, state.depth);
    for (var q = PAGE_MIN; q < PAGE_MIN + n; q++) pages.push(q);
    return pages;
  }

  function shardOfPage(pg) { return Math.floor((pg - 1) / PPS); }

  function pageHasAllTags(pg, wantIdx) {
    var bits = PTAG[pg - PAGE_MIN];
    if (!bits) return false;
    var have = {};
    var parts = bits.split(',');
    for (var i = 0; i < parts.length; i++) have[parts[i]] = 1;
    for (var j = 0; j < wantIdx.length; j++) {
      if (!have[wantIdx[j]]) return false;
    }
    return true;
  }

  /* ---------------- 记录级筛选 ---------------- */
  function pass(r) {
    if (state.hideStick && r.stick) return false;
    if (state.tags.length) {
      for (var i = 0; i < state.tags.length; i++) {
        if ((r.tags || []).indexOf(state.tags[i]) < 0) return false;
      }
    }
    if (state.kw) {
      var h = (r.title + ' ' + (r.author || '')).toLowerCase();
      if (h.indexOf(state.kw) < 0) return false;
    }
    if (state.seenFilter === 'only' && !isSeen(r.tid)) return false;
    if (state.seenFilter === 'hide' && isSeen(r.tid)) return false;
    return true;
  }

  /* ---------------- 渲染 ---------------- */
  var loadedPages = {};     // page -> records
  var currentRows = [];

  function renderTags() {
    el.chips.innerHTML = TAGS.map(function (t) {
      var on = state.tags.indexOf(t.key) >= 0;
      var c = t.color ? ' style="--tc:' + t.color + '"' : '';
      return '<span class="chip' + (on ? ' on' : '') + (t.struct ? ' st' : '') + '"'
        + ' data-k="' + esc(t.key) + '"' + c + '>'
        + esc(t.label) + '<i>' + t.n + '</i></span>';
    }).join('');
  }

  function renderActive() {
    var bits = [];
    if (state.kw) bits.push('<span class="af">关键词：' + esc(state.kw) + '</span>');
    state.tags.forEach(function (k) {
      if (TAGMAP[k]) bits.push('<span class="af">' + esc(TAGMAP[k].label) + '</span>');
    });
    if (state.hideStick) bits.push('<span class="af">隐藏置顶</span>');
    if (state.seenFilter === 'only') bits.push('<span class="af">只看点过</span>');
    if (state.seenFilter === 'hide') bits.push('<span class="af">隐藏点过</span>');
    if (bits.length) {
      bits.push('<span class="af clear" id="clearf">清空筛选</span>');
      el.active.innerHTML = bits.join('');
      var cf = document.getElementById('clearf');
      if (cf) cf.onclick = clearFilters;
    } else {
      el.active.innerHTML = '<span class="af none">未筛选 · 点上方标签开始过滤</span>';
    }
  }

  function clearFilters() {
    state.tags = []; state.kw = ''; state.hideStick = false; state.seenFilter = 'all';
    el.q.value = ''; el.seenfilter.value = 'all';
    save(); renderTags(); renderActive(); refresh();
  }

  function rowHtml(r) {
    var tgs = (r.tags || []).map(function (k) { return tagChip(k, true); }).join('');
    var pin = r.stick ? '<span class="pin">置頂</span>' : '';
    var lk = r.locked ? '<span class="lk">鎖</span>' : '';
    var multi = r.pages > 1 ? '<span class="pg">' + r.pages + '頁</span>' : '';
    var sv = isSeen(r.tid);
    return '<div class="row' + (r.stick ? ' stick' : '') + (sv ? ' seen' : '') + '" data-tid="' + esc(r.tid) + '">'
      + '<div class="rt">'
      + '<button class="seen-btn' + (sv ? ' on' : '') + '" data-seen="' + esc(r.tid) + '" title="' + (sv ? '取消点过' : '标记点过') + '">' + (sv ? '✓' : '○') + '</button> '
      + '<a href="' + esc(r.url) + '" target="_blank" rel="noopener" data-open="' + esc(r.tid) + '">'
      + pin + lk + esc(r.title) + '</a>' + multi + '</div>'
      + '<div class="rm">'
      + '<span class="au">' + esc(r.author || '匿名') + '</span>'
      + '<span class="dt" title="发帖时间">' + esc(r.post_date || '') + '</span>'
      + '<span class="n" title="回复">💬 ' + (r.replies || 0) + '</span>'
      + '<span class="n" title="下载">⬇ ' + (r.downloads || 0) + '</span>'
      + (r.praise ? '<span class="n" title="赞">👍 ' + r.praise + '</span>' : '')
      + (r.last_date ? '<span class="lz" title="最后回复">↻ ' + esc(r.last_date) + ' ' + esc(r.last_by || '') + '</span>' : '')
      + '<span class="pgno">P' + r.page + '</span>'
      + '</div>'
      + (tgs ? '<div class="rtt">' + tgs + '</div>' : '')
      + '</div>';
  }

  function sortRows(rows) {
    if (state.sort === 'replies') rows.sort(function (a, b) { return (b.replies || 0) - (a.replies || 0); });
    else if (state.sort === 'downloads') rows.sort(function (a, b) { return (b.downloads || 0) - (a.downloads || 0); });
    else if (state.sort === 'new') rows.sort(function (a, b) { return (b.post_ts || 0) - (a.post_ts || 0); });
    else if (state.sort === 'last') rows.sort(function (a, b) { return (b.last_ts || 0) - (a.last_ts || 0); });
    else rows.sort(function (a, b) { return (a.page - b.page) || ((b.post_ts || 0) - (a.post_ts || 0)); });
    return rows;
  }

  /* ---------------- 虚拟滚动 ---------------- */
  var ROW_H = 116;          // 行高 109 + margin-bottom 7
  var BUFFER = 8;           // 上下各多渲染几行，减少滚动白边
  var rafPending = false;

  function paintVirtual() {
    var total = currentRows.length;
    el.spacer.style.height = (total * ROW_H) + 'px';
    if (!total) {
      el.list.innerHTML = '';
      el.empty.style.display = '';
      el.stat.textContent = '命中 0 帖';
      return;
    }
    el.empty.style.display = 'none';

    var top = el.viewport.scrollTop;
    var vh = el.viewport.clientHeight || 600;
    var start = Math.max(0, Math.floor(top / ROW_H) - BUFFER);
    var end = Math.min(total, Math.ceil((top + vh) / ROW_H) + BUFFER);
    var chunk = currentRows.slice(start, end);

    el.list.style.transform = 'translateY(' + (start * ROW_H) + 'px)';
    el.list.innerHTML = chunk.map(rowHtml).join('');
    el.stat.textContent = '命中 ' + total + ' 帖 · 已渲染 ' + (end - start) + ' 行';
  }

  function onScroll() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(function () {
      rafPending = false;
      paintVirtual();
    });
  }
  el.viewport.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);

  /* ---------------- 主流程 ---------------- */
  var refreshToken = 0;

  function refresh() {
    var token = ++refreshToken;
    el.loading.style.display = '';
    el.loading.textContent = '筛选中…';

    function go(kwHits) {
      if (token !== refreshToken) return;
      var pages = candidatePages(kwHits);
      var needShards = {};
      pages.forEach(function (pg) { needShards[shardOfPage(pg)] = 1; });
      var shardList = Object.keys(needShards).map(Number).sort(function (a, b) { return a - b; });

      // 无筛选时：只加载最新的 state.depth 页，其余靠「加载更早的」按钮按片追加
      if (!state.tags.length && !state.kw) {
        var maxShard = shardOfPage(PAGE_MIN + state.depth - 1);
        shardList = shardList.filter(function (si) { return si <= maxShard; });
      }

      var i = 0;
      function nextShard() {
        if (token !== refreshToken) return;
        if (i >= shardList.length) { finish(); return; }
        var si = shardList[i++];
        el.loading.textContent = '加载分片 ' + i + '/' + shardList.length + '…';
        ensureShard(si, function (ok) {
          if (token !== refreshToken) return;
          if (ok) {
            for (var pg = si * PPS + 1; pg <= (si + 1) * PPS; pg++) {
              var recs = window.T66Y_PAGE[String(pg)];
              if (recs) loadedPages[pg] = recs;
            }
          }
          nextShard();
        });
      }
      nextShard();

      function finish() {
        if (token !== refreshToken) return;
        var rows = [];
        for (var k in loadedPages) {
          if (!loadedPages.hasOwnProperty(k)) continue;
          var recs = loadedPages[k];
          for (var j = 0; j < recs.length; j++) {
            if (pass(recs[j])) rows.push(recs[j]);
          }
        }
        currentRows = sortRows(rows);
        el.loading.style.display = 'none';
        el.viewport.scrollTop = 0;
        paintVirtual();
        syncOlder();
      }
    }

    if (state.kw) {
      scanTitles(state.kw, 3000, function (hits) { go(hits); });
    } else {
      go(null);
    }
  }

  /* ---------------- 事件 ---------------- */
  el.chips.addEventListener('click', function (e) {
    var c = e.target.closest('.chip');
    if (!c) return;
    var k = c.getAttribute('data-k');
    var i = state.tags.indexOf(k);
    if (i >= 0) state.tags.splice(i, 1); else state.tags.push(k);
    save(); renderTags(); renderActive(); refresh();
  });

  var timer = null;
  el.q.addEventListener('input', function (e) {
    clearTimeout(timer);
    var v = e.target.value.trim();
    timer = setTimeout(function () {
      state.kw = v.toLowerCase(); save(); renderActive(); refresh();
    }, 220);
  });

  el.sort.addEventListener('change', function (e) {
    state.sort = e.target.value; save();
    currentRows = sortRows(currentRows);
    el.viewport.scrollTop = 0;
    paintVirtual();
  });

  el.seenfilter.addEventListener('change', function (e) {
    state.seenFilter = e.target.value; save();
    renderActive(); refresh();
  });

  el.clearseen.addEventListener('click', function () {
    var n = Object.keys(seen).length;
    if (!n || !confirm('清空全部 ' + n + ' 条点过记录？')) return;
    seen = {}; saveSeen(); paintVirtual();
  });

  // 点过按钮 / 点标题自动标记：事件委托
  el.list.addEventListener('click', function (e) {
    var sb = e.target.closest('.seen-btn');
    if (sb) {
      e.preventDefault();
      var tid = sb.getAttribute('data-seen');
      var on = !isSeen(tid);
      markSeen(tid, on);
      if (state.seenFilter === 'only' && !on) {
        // 在「只看点过」里取消 → 该行要消失，重筛
        refresh();
      } else {
        // 更新当前可见行的按钮状态，不整页重刷
        var row = sb.closest('.row');
        if (row) {
          row.classList.toggle('seen', on);
          sb.classList.toggle('on', on);
          sb.textContent = on ? '✓' : '○';
          sb.title = on ? '取消点过' : '标记点过';
        }
      }
      return;
    }
    var a = e.target.closest('a[data-open]');
    if (a) {
      var id = a.getAttribute('data-open');
      if (!isSeen(id)) {
        markSeen(id, true);
        var row2 = a.closest('.row');
        if (row2) {
          row2.classList.add('seen');
          var btn = row2.querySelector('.seen-btn');
          if (btn) { btn.classList.add('on'); btn.textContent = '✓'; btn.title = '取消点过'; }
        }
      }
    }
  });

  el.reset.addEventListener('click', function () {
    if (!confirm('清空所有筛选条件和排序偏好？（点过记录保留，可用「清空点过」单独清）')) return;
    try { localStorage.removeItem(KEY); } catch (e) {}
    state = { tags: [], kw: '', sort: 'page', hideStick: false, depth: 100, seenFilter: 'all' };
    el.q.value = ''; el.sort.value = 'page'; el.seenfilter.value = 'all';
    renderTags(); renderActive(); refresh();
  });

  /* 无筛选时「加载更早的页」：按片追加，每次多 100 页 */
  var olderBtn = document.getElementById('older');
  function syncOlder() {
    if (!olderBtn) return;
    var noFilter = !state.tags.length && !state.kw;
    var more = noFilter && (PAGE_MIN + state.depth - 1) < PAGE_MAX;
    olderBtn.style.display = more ? '' : 'none';
    if (more) {
      var nextEnd = Math.min(PAGE_MIN + state.depth + 99, PAGE_MAX);
      olderBtn.textContent = '加载更早的页（P' + (PAGE_MIN + state.depth) + '–P' + nextEnd + '）';
    }
  }
  if (olderBtn) {
    olderBtn.addEventListener('click', function () {
      state.depth += 100;
      syncOlder();
      refresh();
    });
  }

  /* ---------------- 启动 ---------------- */
  el.q.value = state.kw;
  el.sort.value = state.sort;
  el.seenfilter.value = state.seenFilter;
  el.meta.textContent = META.forum + ' (fid=' + META.fid + ') · '
    + META.pages_done + '/' + META.total_pages + ' 页 · ' + META.count + ' 帖 · 生成于 ' + META.generated;
  updateSeenCount();
  renderTags();
  renderActive();
  refresh();
})();
