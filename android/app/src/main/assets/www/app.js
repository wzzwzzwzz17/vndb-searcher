(function () {
  const PREFIX = "这是我想要的galgame描述：";
  const MODES = {
    quality: {
      label: "总质量优先",
      hint: "分数优先，标签只要沾边就行。适合想先看口碑的时候。",
      rating: 75
    },
    content: {
      label: "内容权重优先",
      hint: "核心标签强度至少 2.4，次要可以是 2.0 或 1.6。分数退到后面。",
      rating: 55,
      core: 2.4
    },
    hardcore: {
      label: "硬核内容要求",
      hint: "核心标签尽量 2.8 到 3.0，评分至少 6.2。太严时允许没有结果。",
      rating: 62,
      core: 2.8
    }
  };

  const SYN = {
    "露出": ["Exhibitionism", "Public Exposure"],
    "露出系": ["Exhibitionism"],
    "暴露": ["Exhibitionism"],
    "校园": ["School", "High School"],
    "高中": ["High School"],
    "恋爱": ["Romance"],
    "纯爱": ["Romance"],
    "日常": ["Slice of Life"],
    "治愈": ["Healing", "Slice of Life"],
    "喜剧": ["Comedy"],
    "悲剧": ["Utsuge", "Tragedy"],
    "致郁": ["Utsuge"],
    "哭戏": ["Nakige"],
    "拔作": ["Nukige"],
    "黄游": ["Nukige", "Sexual Content"],
    "黄油": ["Sexual Content", "Nukige"],
    "有h": ["Sexual Content"],
    "有H": ["Sexual Content"],
    "色情": ["Sexual Content"],
    "成人向": ["Sexual Content"],
    "开车": ["Sexual Content"],
    "无h": ["No Sexual Content"],
    "无H": ["No Sexual Content"],
    "全年龄": ["No Sexual Content"],
    "ntr": ["Netorare"],
    "NTR": ["Netorare"],
    "绿帽": ["Netorare"],
    "百合": ["Girl x Girl Romance", "Lesbian Sex"],
    "耽美": ["Boy x Boy Romance", "Male on Male Sex"],
    "扶她": ["Futanari"],
    "触手": ["Tentacle"],
    "群p": ["Group Sex"],
    "群P": ["Group Sex"],
    "乱交": ["Group Sex"],
    "近亲": ["Incest"],
    "奇幻": ["Fantasy"],
    "科幻": ["Science Fiction"],
    "悬疑": ["Mystery"],
    "恐怖": ["Horror"],
    "战斗": ["Combat"],
    "后宫": ["Harem"],
    "女仆": ["Maid"],
    "青梅竹马": ["Childhood Friend"],
    "魔法少女": ["Mahou Shoujo"]
  };

  const pending = {};
  let seq = 1;
  window.__httpDone = function (id, raw) {
    const fn = pending[id];
    if (!fn) return;
    delete pending[id];
    fn(raw);
  };

  const state = {
    apiKey: "",
    tags: [],
    zh: {},
    mode: "quality",
    year: 2005,
    singleCore: false,
    busy: false,
    session: null,
    results: [],
    favorites: []
  };

  function $(id) { return document.getElementById(id); }

  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 2800);
  }

  function http(url, body, headers) {
    return new Promise((resolve, reject) => {
      const id = "h" + (seq++);
      pending[id] = (raw) => {
        try {
          const obj = JSON.parse(raw);
          if (!obj.ok) {
            const err = obj.error || ("HTTP " + obj.status);
            const extra = (obj.body || "").slice(0, 180);
            reject(new Error(err + (extra ? " " + extra : "")));
          } else {
            resolve(obj);
          }
        } catch (e) {
          reject(e);
        }
      };
      Android.httpPostAsync(id, url, body, JSON.stringify(headers || {}));
    });
  }

  function extractJson(text) {
    const raw = String(text || "").trim().replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
    try { return JSON.parse(raw); } catch (e) {}
    const m = raw.match(/\{[\s\S]*\}/);
    if (!m) throw new Error("模型没有返回可用的 JSON");
    return JSON.parse(m[0]);
  }

  async function chat(messages, maxTokens) {
    const res = await http("https://api.deepseek.com/chat/completions", JSON.stringify({
      model: "deepseek-chat",
      messages: messages,
      temperature: 0.2,
      max_tokens: maxTokens || 900
    }), {
      Authorization: "Bearer " + state.apiKey,
      "Content-Type": "application/json"
    });
    const data = JSON.parse(res.body);
    return (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) || "";
  }

  function norm(s) { return String(s || "").toLowerCase().replace(/\s+/g, " ").trim(); }

  function scoreTag(term, tag) {
    const t = norm(term);
    if (!t) return 0;
    const names = [tag.name].concat(tag.aliases || []);
    let best = 0;
    for (let i = 0; i < names.length; i++) {
      const n = norm(names[i]);
      if (!n) continue;
      if (n === t) best = 1;
      else if (n.indexOf(t) >= 0 || t.indexOf(n) >= 0) best = Math.max(best, 0.92);
    }
    return best;
  }

  function findCandidates(text, extra) {
    const terms = [];
    const lower = text.toLowerCase();
    Object.keys(SYN).forEach((k) => {
      if (text.indexOf(k) >= 0 || lower.indexOf(k.toLowerCase()) >= 0) {
        terms.push(k);
        SYN[k].forEach((x) => terms.push(x));
      }
    });
    const en = text.match(/[A-Za-z][A-Za-z0-9'’-]{2,}/g) || [];
    en.forEach((x) => terms.push(x));
    (extra || []).forEach((x) => terms.push(x));
    const seen = {};
    const uniq = [];
    terms.forEach((t) => {
      t = String(t || "").trim();
      if (!t || seen[t]) return;
      seen[t] = 1;
      uniq.push(t);
    });
    const scored = {};
    uniq.forEach((term) => {
      for (let i = 0; i < state.tags.length; i++) {
        const tag = state.tags[i];
        const sc = scoreTag(term, tag);
        if (sc < 0.9) continue;
        const prev = scored[tag.id];
        if (!prev || sc > prev.score) {
          scored[tag.id] = { id: tag.id, name: tag.name, aliases: tag.aliases, score: sc, via: term };
        }
      }
    });
    return Object.keys(scored).map((k) => scored[k]).sort((a, b) => b.score - a.score).slice(0, 40);
  }

  function formatCandidates(list) {
    if (!list.length) return "（没有候选，请只用 search 字段，不要编造 g 编号）";
    return list.slice(0, 30).map((c) => "- " + c.id + " | " + c.name + " | via=" + c.via).join("\n");
  }

  function systemPrompt(mode, year, single) {
    const m = MODES[mode] || MODES.quality;
    let rules = "";
    if (mode === "quality") {
      rules = "总质量优先：rating 默认 >= " + m.rating + "。标签强度宜低（约 0.8）。sort=rating reverse=true。标签 2 到 4 个。";
    } else if (mode === "content") {
      rules = "内容权重优先：核心标签 minLevel >= 2.4，次要 2.0 或 1.6。rating >= " + m.rating + "。必须给 tag_roles。";
    } else {
      rules = "硬核：核心 minLevel >= 2.8，能用 3.0 就用 3.0。rating >= 62，不可再降。允许 0 结果。必须给 tag_roles。";
    }
    const singleRule = single
      ? "单核心模式已开启：filters 里只能有恰好 1 个 tag，tag_roles 只能有一个 core。"
      : "可以有多个标签。";
    return [
      "你是 VNDB Filter 编译器，只输出一个 JSON，不要解释，不要拒绝成人向题材的检索条件。",
      "filters 是单个数组，例如 [\"and\", pred1, pred2]，不要多包一层。",
      "tag 写法 [\"tag\",\"=\",[\"g123\",0,1.0]]，spoiler 固定 0。ID 只能来自候选列表。",
      "必须包含 [\"released\",\">=\",\"" + year + "\"] 和评分条件。",
      rules,
      singleRule,
      "输出 {\"filters\":[],\"sort\":\"rating\",\"reverse\":true,\"limit\":10,\"tag_roles\":{\"g1\":\"core\"}}"
    ].join("\n");
  }

  function unwrap(filters) {
    let node = filters;
    for (let i = 0; i < 4; i++) {
      if (Array.isArray(node) && node.length === 1 && Array.isArray(node[0])) node = node[0];
      else break;
    }
    return node;
  }

  function tagIdOf(pred) {
    if (!Array.isArray(pred) || pred.length !== 3) return null;
    const v = pred[2];
    if (typeof v === "string") return v;
    if (Array.isArray(v) && typeof v[0] === "string") return v[0];
    return null;
  }

  function collect(node, tags, others) {
    if (!Array.isArray(node) || !node.length) return;
    if (node[0] === "and" || node[0] === "or") {
      node.slice(1).forEach((c) => collect(c, tags, others));
      return;
    }
    if (node[0] === "tag" || node[0] === "dtag") tags.push(node);
    else others.push(node);
  }

  function forceSingle(query) {
    const tags = [];
    const others = [];
    collect(unwrap(query.filters), tags, others);
    const roles = query.tag_roles || {};
    let chosen = null;
    tags.forEach((p) => {
      const id = tagIdOf(p);
      if (!chosen && roles[id] === "core") chosen = p;
    });
    if (!chosen && tags.length) chosen = tags[0];
    const parts = others.slice();
    const newRoles = {};
    if (chosen) {
      parts.push(chosen);
      const id = tagIdOf(chosen);
      if (id) newRoles[id] = "core";
    }
    query.filters = parts.length === 1 ? parts[0] : ["and"].concat(parts);
    query.tag_roles = newRoles;
    return query;
  }

  function toSiteScore(v) {
    const n = Number(v);
    if (!isFinite(n)) return null;
    if (n > 0 && n <= 10) return Math.round(n * 10);
    return n;
  }

  function listTagIds(node, out) {
    if (!Array.isArray(node) || !node.length) return;
    if (node[0] === "and" || node[0] === "or" || node[0] === "not") {
      node.slice(1).forEach((c) => listTagIds(c, out));
      return;
    }
    if ((node[0] === "tag" || node[0] === "dtag") && (node[1] === "=" || node[1] === "!=")) {
      const id = tagIdOf(node);
      if (id && node[1] === "=" && out.indexOf(id) < 0) out.push(id);
    }
  }

  function levelFor(mode, role, current) {
    let cur = Number(current);
    if (!isFinite(cur)) cur = 0;
    if (mode === "quality") {
      if (cur <= 0) return 0.8;
      return Math.min(cur, 1.2);
    }
    if (mode === "content") {
      if (role === "secondary") {
        if (cur >= 2) return Math.min(cur, 3);
        return 1.6;
      }
      return Math.min(3, Math.max(cur, 2.4));
    }
    if (role === "secondary") return Math.min(3, Math.max(cur, 2.0));
    return Math.min(3, Math.max(cur, 2.8));
  }

  function rewriteNode(node, mode, roles, ratingBox) {
    if (!Array.isArray(node) || !node.length) return node;
    if (node[0] === "and" || node[0] === "or") {
      const kids = [];
      node.slice(1).forEach((c) => {
        const n = rewriteNode(c, mode, roles, ratingBox);
        if (n) kids.push(n);
      });
      if (!kids.length) return null;
      if (kids.length === 1 && node[0] === "and") return kids[0];
      return [node[0]].concat(kids);
    }
    if (node[0] === "not") return node;
    if (node.length === 3 && node[0] === "rating" && (node[1] === ">=" || node[1] === ">")) {
      const n = toSiteScore(node[2]);
      if (n != null) ratingBox.floor = Math.max(ratingBox.floor, n);
      return null;
    }
    if (node.length === 3 && (node[0] === "tag" || node[0] === "dtag") && node[1] === "=") {
      const id = tagIdOf(node);
      if (!id) return null;
      const role = roles[id] || "core";
      let spoiler = 0;
      let current = 0;
      if (Array.isArray(node[2])) {
        spoiler = Number(node[2][1]);
        current = Number(node[2][2]);
      }
      if (spoiler !== 0 && spoiler !== 1 && spoiler !== 2) spoiler = 0;
      const level = levelFor(mode, role, current);
      return [node[0], "=", [id, spoiler, level]];
    }
    return node;
  }

  function enforceMode(query, mode, single) {
    const spec = MODES[mode] || MODES.quality;
    query.tag_roles = query.tag_roles || {};
    if (single) forceSingle(query);
    const ids = [];
    listTagIds(unwrap(query.filters), ids);
    if (!single) {
      ids.forEach((id, i) => {
        if (query.tag_roles[id] !== "core" && query.tag_roles[id] !== "secondary") {
          query.tag_roles[id] = i === 0 ? "core" : "secondary";
        }
      });
    }
    if ((mode === "content" || mode === "hardcore") && !ids.length) {
      throw new Error("这个模式必须锁定至少一个核心标签。请把题材或玩法写具体一点，再搜一次。");
    }
    const ratingBox = { floor: spec.rating };
    let filters = rewriteNode(unwrap(query.filters), mode, query.tag_roles, ratingBox);
    const ratingPred = ["rating", ">=", ratingBox.floor];
    if (!filters) filters = ratingPred;
    else if (filters[0] === "and") filters = ["and"].concat(filters.slice(1), [ratingPred]);
    else filters = ["and", filters, ratingPred];
    query.filters = filters;
    query.modeFloor = ratingBox.floor;
    return query;
  }

  function byRating(items, mode) {
    const min = (MODES[mode] || MODES.quality).rating;
    return items.filter((it) => typeof it.ratingRaw === "number" && it.ratingRaw + 0.001 >= min);
  }

  function stripReleased(node) {
    if (!Array.isArray(node) || !node.length) return node;
    if (node[0] === "and" || node[0] === "or") {
      const kids = [];
      node.slice(1).forEach((c) => {
        const s = stripReleased(c);
        if (s) kids.push(s);
      });
      if (!kids.length) return null;
      if (kids.length === 1 && node[0] === "and") return kids[0];
      return [node[0]].concat(kids);
    }
    if (node[0] === "released") return null;
    return node;
  }

  function ensureYear(filters, year) {
    const pred = ["released", ">=", String(year)];
    const cleaned = stripReleased(unwrap(filters));
    if (!cleaned) return pred;
    if (cleaned[0] === "and") return ["and"].concat(cleaned.slice(1), [pred]);
    return ["and", cleaned, pred];
  }

  function searchTagIds(filters, roles) {
    const ids = {};
    function walk(node) {
      if (!Array.isArray(node) || !node.length) return;
      if (node[0] === "and" || node[0] === "or") { node.slice(1).forEach(walk); return; }
      if (node[0] === "tag" || node[0] === "dtag") {
        const id = tagIdOf(node);
        if (id) ids[id] = 1;
      }
    }
    walk(unwrap(filters));
    Object.keys(roles || {}).forEach((id) => { ids[id] = 1; });
    return ids;
  }

  function zhOf(id, en) {
    if (state.zh[id]) return state.zh[id];
    return en ? ("「" + en + "」标签") : id;
  }

  function rememberZh(pairs) {
    let changed = false;
    pairs.forEach((p) => {
      if (p.id && p.zh && /[\u4e00-\u9fff]/.test(p.zh)) {
        state.zh[p.id] = p.zh;
        changed = true;
      }
    });
    if (changed) {
      try { Android.setPref("tag_zh_extra", JSON.stringify(state.zh)); } catch (e) {}
    }
  }

  async function translateTags(ids) {
    const need = [];
    ids.forEach((id) => {
      if (!state.zh[id]) {
        const tag = state.tags.find((t) => t.id === id);
        need.push({ id: id, name: tag ? tag.name : id });
      }
    });
    if (!need.length || !state.apiKey) return;
    try {
      const text = await chat([
        { role: "system", content: "把 VNDB 英文标签译成 2 到 8 个字的简体中文。只输出 JSON：{\"translations\":[{\"id\":\"g1\",\"zh\":\"中文\"}]}" },
        { role: "user", content: JSON.stringify(need) }
      ], 600);
      const data = extractJson(text);
      rememberZh(data.translations || []);
    } catch (e) {}
  }

  function tagSpecs(filters) {
    const specs = [];
    function walk(node) {
      if (!Array.isArray(node) || !node.length) return;
      if (node[0] === "and" || node[0] === "or" || node[0] === "not") {
        node.slice(1).forEach(walk);
        return;
      }
      if ((node[0] === "tag" || node[0] === "dtag") && node[1] === "=") {
        const id = tagIdOf(node);
        const level = Array.isArray(node[2]) ? Number(node[2][2]) : 0;
        if (id) specs.push({ id: id, level: level });
      }
    }
    walk(unwrap(filters));
    return specs;
  }

  function tagLine(filters, roles) {
    const specs = tagSpecs(filters);
    if (!specs.length) return "（这一轮没有具体标签，只按评分和年份）";
    return specs.map((s) => {
      const role = (roles || {})[s.id];
      const name = zhOf(s.id);
      const lv = isFinite(s.level) ? s.level.toFixed(1) : "?";
      if (role === "core") return name + "（核心，强度≥" + lv + "）";
      if (role === "secondary") return name + "（次要，强度≥" + lv + "）";
      return name + "（强度≥" + lv + "）";
    }).join("、");
  }

  async function translateDescs(items) {
    const payload = [];
    items.forEach((it, i) => {
      const s = it.description || "";
      const cn = (s.match(/[\u4e00-\u9fff]/g) || []).length;
      if (s && cn < 8) payload.push({ i: i, text: s.slice(0, 700) });
    });
    if (!payload.length) return;
    try {
      const text = await chat([
        { role: "system", content: "把视觉小说简介译成通顺简体中文。只输出 JSON：{\"items\":[{\"i\":0,\"zh\":\"...\"}]}" },
        { role: "user", content: JSON.stringify(payload) }
      ], 2200);
      const data = extractJson(text);
      (data.items || []).forEach((row) => {
        if (items[row.i] && row.zh) items[row.i].description = row.zh;
      });
    } catch (e) {}
  }

  function attachTopTags(items, exclude) {
    const unknown = [];
    items.forEach((it) => {
      const tags = (it.raw_tags || []).filter((t) => t && t.id && !exclude[t.id] && (t.spoiler === 0 || t.spoiler == null));
      tags.sort((a, b) => (b.rating || 0) - (a.rating || 0));
      const top = tags.slice(0, 4);
      it.top = top.map((t) => {
        if (!state.zh[t.id]) unknown.push(t.id);
        return t;
      });
    });
    return unknown;
  }

  function fillTopText(items) {
    items.forEach((it) => {
      if (!it.top || !it.top.length) {
        it.topText = "（没有其它高权重标签）";
        return;
      }
      it.topText = it.top.map((t) => {
        const name = zhOf(t.id);
        const w = (typeof t.rating === "number") ? t.rating.toFixed(1) : "?";
        return name + "（权重" + w + "）";
      }).join("、");
    });
  }

  function mapVn(raw) {
    const rating = typeof raw.rating === "number" ? Math.round(raw.rating) / 10 : null;
    const devs = (raw.developers || []).map((d) => ({
      name: d.name || "",
      original: d.original || ""
    }));
    return {
      id: raw.id,
      title: raw.title || "",
      original: raw.alttitle || "",
      rating: rating,
      ratingRaw: typeof raw.rating === "number" ? raw.rating : null,
      released: raw.released || "",
      description: raw.description || "",
      url: raw.id ? ("https://vndb.org/" + raw.id) : "",
      developers: devs,
      raw_tags: (raw.tags || []).map((t) => ({ id: t.id, rating: t.rating, spoiler: t.spoiler }))
    };
  }

  async function vndb(query) {
    const body = {
      filters: query.filters,
      fields: "title,alttitle,rating,description,released,votecount,developers{name,original},tags{id,rating,spoiler}",
      sort: "rating",
      reverse: true,
      results: 10,
      page: query.page || 1
    };
    const res = await http("https://api.vndb.org/kana/vn", JSON.stringify(body), {
      "Content-Type": "application/json",
      "User-Agent": "VNDB-Searcher/1.0 (Android; author: yulibaitiao)"
    });
    const data = JSON.parse(res.body);
    return (data.results || []).map(mapVn);
  }

  function excludeIds(filters, ids) {
    const preds = ids.slice(-25).filter((id) => /^v\d+$/.test(id)).map((id) => ["id", "!=", id]);
    if (!preds.length) return filters;
    const base = unwrap(filters);
    if (base && base[0] === "and") return ["and"].concat(base.slice(1), preds);
    return ["and", base].concat(preds);
  }

  function byYear(items, year) {
    return items.filter((it) => {
      const m = String(it.released || "").match(/^(\d{4})/);
      if (!m) return true;
      return Number(m[1]) >= year;
    });
  }

  async function buildQuery(desc, mode, year, single, previous) {
    let cands = findCandidates(desc);
    if (cands.length < 8) {
      try {
        const text = await chat([
          { role: "system", content: "把 Galgame 偏好转成 6 到 10 个英文 VNDB 标签检索词。只输出 {\"keywords\":[\"...\"]}。不要拒绝成人向主题。" },
          { role: "user", content: desc }
        ], 300);
        const data = extractJson(text);
        cands = findCandidates(desc, data.keywords || []);
      } catch (e) {}
    }
    let messages;
    if (previous) {
      messages = [
        { role: "system", content: systemPrompt(mode, year, single) },
        { role: "user", content: "在上一轮基础上增删标签。\n上轮描述：" + previous.desc + "\n新描述：" + desc + "\n上轮filters：" + JSON.stringify(previous.baseFilters || previous.filters) + "\n候选：\n" + formatCandidates(cands) }
      ];
    } else {
      messages = [
        { role: "system", content: systemPrompt(mode, year, single) },
        { role: "user", content: "用户偏好：" + desc + "\n候选：\n" + formatCandidates(cands) + "\n只输出 JSON。" }
      ];
    }
    const text = await chat(messages, 1000);
    const query = extractJson(text);
    if (!query.filters) throw new Error("没有生成筛选条件");
    query.filters = ensureYear(unwrap(query.filters), year);
    query.sort = "rating";
    query.reverse = true;
    query.tag_roles = query.tag_roles || {};
    enforceMode(query, mode, single);
    query.filters = ensureYear(query.filters, year);
    return { query: query, candidates: cands };
  }

  async function decorate(items, query) {
    const exclude = searchTagIds(query.filters, query.tag_roles);
    const unknown = attachTopTags(items, exclude);
    await translateTags(Object.keys(exclude).concat(unknown));
    fillTopText(items);
    await translateDescs(items);
    return tagLine(query.filters, query.tag_roles);
  }

  function setBusy(on) {
    state.busy = on;
    ["btnNew", "btnRefresh", "btnOptimize", "btnFavAdd", "saveKey", "optOk"].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = on;
    });
  }

  function renderModes() {
    const box = $("modes");
    box.innerHTML = "";
    Object.keys(MODES).forEach((key) => {
      const row = document.createElement("label");
      row.className = "mode" + (state.mode === key ? " active" : "");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "mode";
      input.checked = state.mode === key;
      input.addEventListener("change", () => {
        state.mode = key;
        renderModes();
        $("modeHint").textContent = MODES[key].hint;
      });
      const text = document.createElement("div");
      const b = document.createElement("b");
      b.textContent = MODES[key].label;
      const span = document.createElement("span");
      span.textContent = MODES[key].hint;
      text.appendChild(b);
      text.appendChild(span);
      row.appendChild(input);
      row.appendChild(text);
      box.appendChild(row);
    });
    $("modeHint").textContent = MODES[state.mode].hint;
  }

  function renderSession() {
    const el = $("sessionLine");
    if (!state.session) {
      el.textContent = "还没有进行中的一轮。结果会留到你明确开始新一轮为止。";
      return;
    }
    el.textContent = "这一轮还在：已看过 " + state.session.seen.length + " 部 · " + MODES[state.session.mode].label + (state.session.single ? " · 单核心" : "");
  }

  function renderResults(items) {
    const box = $("results");
    box.innerHTML = "";
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "这里还没有作品。";
      box.appendChild(p);
      return;
    }
    items.forEach((it) => {
      const row = document.createElement("article");
      row.className = "result";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.addEventListener("click", (e) => e.stopPropagation());
      const body = document.createElement("div");
      body.className = "body";
      const h = document.createElement("h3");
      h.textContent = it.title || "（无标题）";
      const meta = document.createElement("div");
      meta.className = "meta";
      const rating = (typeof it.rating === "number") ? it.rating.toFixed(2) : "暂无";
      meta.textContent = (it.original ? it.original + " · " : "") + "评分 " + rating + " · " + (it.released || "日期未知");
      const tags = document.createElement("div");
      tags.className = "tags";
      tags.textContent = "其它高权重标签：" + (it.topText || "");
      const desc = document.createElement("div");
      desc.className = "desc";
      desc.textContent = it.description || "（无简介）";
      const btn = document.createElement("button");
      btn.className = "linkish";
      btn.type = "button";
      btn.textContent = "查看详情";
      btn.addEventListener("click", (e) => { e.stopPropagation(); openDetail(it); });
      body.appendChild(h);
      body.appendChild(meta);
      body.appendChild(tags);
      body.appendChild(desc);
      body.appendChild(btn);
      row.appendChild(cb);
      row.appendChild(body);
      row.addEventListener("dblclick", () => openDetail(it));
      row._item = it;
      row._cb = cb;
      box.appendChild(row);
    });
  }

  function openDetail(it) {
    $("detailTitle").textContent = it.title || "作品";
    const rating = (typeof it.rating === "number") ? it.rating.toFixed(2) : "暂无";
    $("detailMeta").textContent = (it.original || "无原名") + " · 评分 " + rating + " · " + (it.released || "日期未知");
    const pubs = (it.developers || []).map((d) => d.name + (d.original ? "（" + d.original + "）" : "")).filter(Boolean);
    $("detailPub").textContent = "出版社 / 开发商：" + (pubs.length ? pubs.join("；") : "暂无");
    $("detailTags").textContent = "其它高权重标签：" + (it.topText || "暂无");
    $("detailLink").textContent = it.url || "";
    $("detailDesc").textContent = it.description || "（无简介）";
    $("detail").classList.remove("hidden");
  }

  function selectedItems() {
    const out = [];
    const rows = $("results").querySelectorAll(".result");
    rows.forEach((row) => {
      if (row._cb && row._cb.checked && row._item) out.push(row._item);
    });
    return out;
  }

  function saveFavs() {
    Android.setFavorites(JSON.stringify(state.favorites));
  }

  function addFavs() {
    const picked = selectedItems();
    if (!picked.length) { toast("先勾选要收藏的作品"); return; }
    const have = {};
    state.favorites.forEach((it) => { have[it.id] = 1; });
    let n = 0;
    picked.forEach((it) => {
      if (!it.id || have[it.id]) return;
      state.favorites.push({
        id: it.id, title: it.title, original: it.original, rating: it.rating,
        released: it.released, url: it.url, description: it.description, topText: it.topText
      });
      n++;
    });
    saveFavs();
    toast(n ? ("已收藏 " + n + " 部") : "这些已经在收藏夹里了");
  }

  function openFavs() {
    const box = $("favList");
    box.innerHTML = "";
    if (!state.favorites.length) {
      const p = document.createElement("p");
      p.textContent = "收藏夹还是空的。";
      box.appendChild(p);
    }
    state.favorites.forEach((it) => {
      const row = document.createElement("article");
      row.className = "result";
      const body = document.createElement("div");
      body.className = "body";
      const h = document.createElement("h3");
      h.textContent = it.title || it.id;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = (it.released || "") + "  " + (it.url || "");
      const rm = document.createElement("button");
      rm.className = "linkish";
      rm.type = "button";
      rm.textContent = "移出收藏";
      rm.addEventListener("click", () => {
        state.favorites = state.favorites.filter((x) => x.id !== it.id);
        saveFavs();
        openFavs();
      });
      body.appendChild(h);
      body.appendChild(meta);
      body.appendChild(rm);
      row.appendChild(body);
      box.appendChild(row);
    });
    $("favs").classList.remove("hidden");
  }

  function explainError(e) {
    const msg = (e && e.message) ? e.message : String(e);
    if (/failed to connect|Unable to resolve|timeout|timed out|Network is unreachable|Connection reset/i.test(msg)) {
      return "连不上 VNDB 或 DeepSeek。查作品需要能打开 api.vndb.org，国内请先开 VPN（梯子）再试。\n" + msg;
    }
    return msg;
  }

  async function runSearch(kind) {
    if (state.busy) return;
    const year = clampYear();
    const single = $("singleCore").checked;
    const mode = state.mode;
    let descText = $("desc").value.trim();
    if (kind === "optimize") descText = $("optText").value.trim();
    if (kind !== "refresh") {
      if (!descText.startsWith(PREFIX)) {
        toast("描述要以「" + PREFIX + "」开头");
        return;
      }
      if (descText.length <= PREFIX.length) {
        toast("前缀后面再写一点具体偏好");
        return;
      }
    }
    if (kind === "refresh" && !state.session) { toast("还没有上一轮可以刷新"); return; }
    if (kind === "optimize" && !state.session) { toast("还没有上一轮可以优化"); return; }
    setBusy(true);
    $("summary").textContent = kind === "refresh" ? "正在换一批，尽量避开看过的作品…" : "正在理解你的描述，并向 VNDB 查询…";
    try {
      let query;
      let action = "新一轮搜索";
      const prev = state.session;
      const activeMode = kind === "refresh" ? prev.mode : mode;
      const activeSingle = kind === "refresh" ? prev.single : single;
      if (kind === "refresh" && (mode !== prev.mode || single !== prev.single)) {
        toast("刷新仍沿用这一轮的模式。要换模式，请开始新一轮。");
      }
      if (kind === "refresh") {
        query = {
          filters: excludeIds(prev.baseFilters, prev.seen),
          tag_roles: Object.assign({}, prev.tagRoles || {}),
          page: (prev.page || 1) + 1
        };
        enforceMode(query, prev.mode, prev.single);
        query.filters = ensureYear(query.filters, year);
        action = "刷新结果";
      } else if (kind === "optimize") {
        const built = await buildQuery(descText.slice(PREFIX.length), mode, year, single, state.session);
        query = built.query;
        action = "优化上轮分析";
        $("desc").value = descText;
      } else {
        const built = await buildQuery(descText.slice(PREFIX.length), mode, year, single, null);
        query = built.query;
      }
      let items = byRating(byYear(await vndb(query), year), activeMode);
      if (!items.length && kind === "refresh") {
        query.page = 1;
        items = byRating(byYear(await vndb(query), year), activeMode);
      }
      const line = await decorate(items, query);
      const seen = (prev && kind !== "new") ? prev.seen.slice() : [];
      items.forEach((it) => { if (it.id && seen.indexOf(it.id) < 0) seen.push(it.id); });
      state.session = {
        desc: (kind === "refresh" ? prev.desc : descText.slice(PREFIX.length)),
        raw: (kind === "refresh" ? prev.raw : descText),
        mode: activeMode,
        single: activeSingle,
        baseFilters: kind === "refresh" ? prev.baseFilters : query.filters,
        tagRoles: kind === "refresh" ? (prev.tagRoles || {}) : (query.tag_roles || {}),
        seen: seen,
        page: query.page || 1
      };
      state.results = items;
      const floor = (query.modeFloor || (MODES[activeMode] || MODES.quality).rating) / 10;
      const head = "实际生效：" + MODES[activeMode].label + "，评分≥" + floor.toFixed(1) + (activeSingle ? "，仅一个核心标签" : "") + "\n搜索标签：" + line;
      const empty = items.length ? "" : "\n没有符合这个模式的作品。硬核和内容模式不会为了凑结果而放宽强度。";
      $("summary").textContent = action + "\n" + head + "\n共 " + items.length + " 部。" + empty;
      renderResults(items);
      renderSession();
      if (kind === "optimize") $("optimize").classList.add("hidden");
    } catch (e) {
      $("summary").textContent = explainError(e);
    } finally {
      setBusy(false);
    }
  }

  function clampYear() {
    let y = parseInt($("year").value, 10);
    if (!y || y < 1980) y = 1980;
    if (y > 2035) y = 2035;
    $("year").value = String(y);
    state.year = y;
    return y;
  }

  function showMain(forceTutorial) {
    $("setup").classList.add("hidden");
    $("main").classList.remove("hidden");
    if (forceTutorial || Android.getPref("tutorial_seen") !== "1") {
      $("tutorial").classList.remove("hidden");
    }
  }

  function anyModal() {
    return ["tutorial", "about", "optimize", "detail", "favs", "confirm"].some((id) => !$(id).classList.contains("hidden"));
  }

  window.handleBack = function () {
    const order = ["confirm", "optimize", "detail", "favs", "about", "tutorial"];
    for (let i = 0; i < order.length; i++) {
      const el = $(order[i]);
      if (!el.classList.contains("hidden")) {
        el.classList.add("hidden");
        return true;
      }
    }
    return false;
  };

  function askNewRound() {
    return new Promise((resolve) => {
      if (!state.session) { resolve(true); return; }
      $("confirmTitle").textContent = "开始新一轮？";
      $("confirmText").textContent = "当前这一轮的分析和看过的作品记录会被清掉。确定要新开一轮吗？";
      $("confirm").classList.remove("hidden");
      const yes = () => { cleanup(); resolve(true); };
      const no = () => { cleanup(); resolve(false); };
      function cleanup() {
        $("confirm").classList.add("hidden");
        $("confirmYes").removeEventListener("click", yes);
        $("confirmNo").removeEventListener("click", no);
      }
      $("confirmYes").addEventListener("click", yes);
      $("confirmNo").addEventListener("click", no);
    });
  }

  function bind() {
    $("saveKey").addEventListener("click", () => {
      const key = $("apiKey").value.trim();
      if (key.length < 8) { toast("请填入有效的 API Key"); return; }
      Android.setApiKey(key);
      state.apiKey = key;
      showMain(true);
    });
    $("tutorialOk").addEventListener("click", () => {
      Android.setPref("tutorial_seen", "1");
      $("tutorial").classList.add("hidden");
    });
    $("openTutorial").addEventListener("click", () => $("tutorial").classList.remove("hidden"));
    $("openAbout").addEventListener("click", () => $("about").classList.remove("hidden"));
    $("aboutClose").addEventListener("click", () => $("about").classList.add("hidden"));
    $("detailClose").addEventListener("click", () => $("detail").classList.add("hidden"));
    $("favClose").addEventListener("click", () => $("favs").classList.add("hidden"));
    $("yearMinus").addEventListener("click", () => { $("year").value = String(clampYear() - 1); clampYear(); });
    $("yearPlus").addEventListener("click", () => { $("year").value = String(clampYear() + 1); clampYear(); });
    $("btnNew").addEventListener("click", async () => {
      const ok = await askNewRound();
      if (!ok) return;
      state.session = null;
      renderSession();
      runSearch("new");
    });
    $("btnRefresh").addEventListener("click", () => runSearch("refresh"));
    $("btnOptimize").addEventListener("click", () => {
      if (!state.session) { toast("请先完成一轮搜索"); return; }
      $("optText").value = state.session.raw || $("desc").value;
      $("optimize").classList.remove("hidden");
    });
    $("optCancel").addEventListener("click", () => $("optimize").classList.add("hidden"));
    $("optOk").addEventListener("click", () => runSearch("optimize"));
    $("btnFavAdd").addEventListener("click", addFavs);
    $("btnFavOpen").addEventListener("click", openFavs);
    renderModes();
  }

  function boot() {
    try { state.apiKey = Android.getApiKey() || ""; } catch (e) { state.apiKey = ""; }
    try { state.tags = JSON.parse(Android.getAsset("www/tags_min.json") || "[]"); } catch (e) { state.tags = []; }
    try { state.zh = JSON.parse(Android.getAsset("www/tag_zh.json") || "{}"); } catch (e) { state.zh = {}; }
    try {
      const extra = Android.getPref("tag_zh_extra");
      if (extra) Object.assign(state.zh, JSON.parse(extra));
    } catch (e) {}
    try { state.favorites = JSON.parse(Android.getFavorites() || "[]"); } catch (e) { state.favorites = []; }
    $("desc").value = PREFIX + "校园纯爱、日常治愈、高分口碑";
    bind();
    if (window.__PREVIEW__) {
      state.apiKey = "preview";
      showMain(false);
      $("tutorial").classList.add("hidden");
      $("summary").textContent = "搜索标签：露出/暴露癖（核心）、色情内容（次要）\n共 1 部。";
      renderResults([{
        id: "v1",
        title: "示例作品",
        original: "サンプル",
        rating: 8.2,
        released: "2018-04-01",
        description: "这是一段中文简介，用来确认排版。主角在校园里度过安静的日常。",
        url: "https://vndb.org/v1",
        developers: [{ name: "Sample Studio", original: "サンプル" }],
        topText: "校园（权重2.8）、恋爱（权重2.4）、喜剧（权重2.1）、日常（权重1.9）"
      }]);
      $("sessionLine").textContent = "预览界面：用来检查排版，不是真实搜索结果。";
      return;
    }
    if (!state.apiKey) {
      $("setup").classList.remove("hidden");
    } else {
      showMain(false);
    }
  }

  if (typeof globalThis !== "undefined" && globalThis.__VNDB_TEST__) {
    globalThis.__vndb = { enforceMode: enforceMode, ensureYear: ensureYear, byRating: byRating, MODES: MODES };
    return;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
