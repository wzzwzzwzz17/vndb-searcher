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

  function httpGet(url, headers) {
    return new Promise((resolve, reject) => {
      const id = "g" + (seq++);
      pending[id] = (raw) => {
        try {
          const obj = JSON.parse(raw);
          if (!obj.ok) reject(new Error(obj.error || ("HTTP " + obj.status)));
          else resolve(obj);
        } catch (e) { reject(e); }
      };
      Android.httpGetAsync(id, url, JSON.stringify(headers || {}));
    });
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
    if ((node[0] === "tag" || node[0] === "dtag") && node.length === 4 && /^g\d+$/.test(String(node[1]))) {
      let level = Number(node[3]);
      if (!isFinite(level)) level = 0;
      level = Math.min(3, Math.max(0, level));
      node = node[2] === "!=" ? [node[0], "!=", node[1]] : [node[0], "=", [node[1], 0, level]];
    }
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
      sort: query.sort || "rating",
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
      meta.textContent = (it.original ? it.original + " · " : "") + (it.scoreLine || ("VNDB " + rating)) + " · " + (it.released || "日期未知");
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
      body.addEventListener("click", () => openDetail(it));
      row.appendChild(cb);
      row.appendChild(body);
      row._item = it;
      row._cb = cb;
      box.appendChild(row);
    });
  }

  let detailSeq = 0;

  function openExternal(url) {
    if (!url || !window.Android || !Android.openUrl) return;
    Android.openUrl(url);
  }

  function renderSitePages(profile) {
    const box = $("detailSites");
    const pages = $("detailPages");
    box.innerHTML = "";
    pages.innerHTML = "";
    [["鲲 Galgame", profile.kungal], ["TouchGal", profile.touchgal], ["真红小站", profile.shinnku]].forEach(([label, row]) => {
      const block = document.createElement("section");
      const heading = document.createElement("h3");
      heading.textContent = label;
      block.appendChild(heading);
      if (!row || !row.ok) {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = (row && row.reason) || "没有资料";
        block.appendChild(empty);
      } else {
        if (row.tags && row.tags.length) {
          const tags = document.createElement("p");
          tags.className = "hint";
          tags.textContent = row.tags.join("、");
          block.appendChild(tags);
        }
        if (row.intro) {
          const intro = document.createElement("p");
          intro.textContent = row.intro;
          block.appendChild(intro);
        }
      }
      box.appendChild(block);
      const list = (row && row.pages) || (row && row.page ? [{ name: (row && row.name) || label, page: row.page }] : []);
      list.forEach((item) => {
        if (!item.page) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn";
        button.textContent = label + " · " + (item.name || "参考页");
        button.addEventListener("click", () => openExternal(item.page));
        pages.appendChild(button);
      });
    });
    if (!pages.children.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "这三个站暂时没有对上参考页。";
      pages.appendChild(empty);
    }
  }

  async function openDetail(it) {
    const seq = ++detailSeq;
    $("detailTitle").textContent = it.title || "作品";
    const rating = (typeof it.rating === "number") ? it.rating.toFixed(2) : "暂无";
    $("detailMeta").textContent = (it.original || "无原名") + " · " + (it.scoreLine || ("VNDB " + rating)) + " · " + (it.released || "日期未知");
    const pubs = (it.developers || []).map((d) => d.name + (d.original ? "（" + d.original + "）" : "")).filter(Boolean);
    $("detailPub").textContent = "出版社 / 开发商：" + (pubs.length ? pubs.join("；") : "暂无");
    $("detailTags").textContent = "其它高权重标签：" + (it.topText || "暂无");
    $("detailLink").textContent = "";
    $("detailDesc").textContent = it.description || "（无简介）";
    $("detailSites").innerHTML = "";
    $("detailPages").innerHTML = "";
    $("detailLoading").textContent = "正在读取鲲、TouchGal 和真红小站的参考页…";
    $("detailLoading").classList.remove("hidden");
    if (it.url) {
      const vndb = document.createElement("button");
      vndb.type = "button";
      vndb.className = "btn";
      vndb.textContent = "VNDB 作品页";
      vndb.addEventListener("click", () => openExternal(it.url));
      $("detailLink").appendChild(vndb);
    }
    $("detail").classList.remove("hidden");
    const queries = [];
    [it.original, it.title].forEach((text) => {
      const cleaned = String(text || "").trim();
      if (cleaned && queries.indexOf(cleaned) < 0) queries.push(cleaned);
    });
    const namePage = $("pageName");
    const mustFind = namePage && !namePage.classList.contains("hidden")
      ? !!($("nameMust") && $("nameMust").checked)
      : !!($("mustFind") && $("mustFind").checked);
    try {
      const profile = {
        kungal: await kunProfile(queries),
        touchgal: await touchProfile(queries),
        shinnku: await shinnkuPages(queries, mustFind)
      };
      if (seq !== detailSeq) return;
      renderSitePages(profile);
      $("detailLoading").classList.add("hidden");
    } catch (err) {
      if (seq !== detailSeq) return;
      $("detailLoading").textContent = err.message || "参考页加载失败";
    }
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

  function asTen(v) {
    const n = Number(v);
    if (!isFinite(n) || n <= 0) return null;
    if (n > 10 && n <= 100) return Math.round(n) / 10;
    if (n > 10) return null;
    return Math.round(n * 100) / 100;
  }

  function normName(s) {
    return String(s || "").toLowerCase().replace(/[\s*＊·・]+/g, "");
  }

  function findScore(node, queries) {
    const skip = { download: 1, downloads: 1, magnet: 1, resource: 1, resources: 1, pan: 1, url: 1, link: 1, links: 1 };
    let hit = null;
    function walk(obj) {
      if (hit || !obj) return;
      if (Array.isArray(obj)) { obj.forEach(walk); return; }
      if (typeof obj !== "object") return;
      const names = [];
      let score = null;
      let votes = null;
      Object.keys(obj).forEach((k) => {
        if (skip[k.toLowerCase()]) return;
        const val = obj[k];
        const lk = k.toLowerCase();
        if (typeof val === "string" && /name|title|original/.test(lk) && val.trim()) names.push(val.trim());
        if (score == null && /^(rating|score|avg|average|avg_rating)$/.test(lk)) score = asTen(val);
        if (votes == null && /count|votes/.test(lk) && typeof val === "number") votes = val;
        if (val && typeof val === "object") {
          if (typeof val["zh-cn"] === "string") names.push(val["zh-cn"]);
          if (typeof val["ja-jp"] === "string") names.push(val["ja-jp"]);
        }
      });
      const blob = normName(names.join(" "));
      if (score != null && names.length && queries.some((q) => q && (blob.indexOf(q) >= 0 || q.indexOf(blob) >= 0))) {
        hit = { ok: true, score: score, votes: votes, name: names[0] };
        return;
      }
      Object.keys(obj).forEach((k) => { if (!skip[k.toLowerCase()]) walk(obj[k]); });
    }
    walk(node);
    return hit;
  }

  function workNames(work) {
    const names = [];
    if (!work || typeof work !== "object") return names;
    ["display_name", "name", "latin"].forEach((key) => {
      if (work[key]) names.push(String(work[key]));
    });
    const localized = work.localized || {};
    Object.keys(localized).forEach((key) => {
      const value = localized[key] && localized[key].value;
      if (value) names.push(String(value));
    });
    return names.map((name) => name.trim()).filter(Boolean);
  }

  function pickWork(items, queries) {
    const norms = queries.map(normName).filter(Boolean);
    let best = null;
    let bestRank = 0;
    (items || []).forEach((work) => {
      const blobs = workNames(work).map(normName);
      let rank = 0;
      norms.forEach((query) => {
        blobs.forEach((blob) => {
          if (!blob) return;
          if (query === blob) rank = Math.max(rank, 3);
          else if (query.indexOf(blob) >= 0 || blob.indexOf(query) >= 0) rank = Math.max(rank, 2);
        });
      });
      if (rank > bestRank) { best = work; bestRank = rank; }
    });
    return best;
  }

  function introText(intros) {
    if (!Array.isArray(intros)) return "";
    const byLocale = {};
    intros.forEach((row) => {
      if (row && row.value) byLocale[String(row.locale || "")] = String(row.value).trim();
    });
    const order = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "ja", "en"];
    for (let i = 0; i < order.length; i++) {
      if (byLocale[order[i]]) return byLocale[order[i]];
    }
    const values = Object.keys(byLocale).map((key) => byLocale[key]).filter(Boolean);
    return values[0] || "";
  }

  function tagNames(tags) {
    const names = [];
    if (!Array.isArray(tags)) return names;
    tags.forEach((tag) => {
      if (typeof tag === "string" && tag.trim()) names.push(tag.trim());
      else if (tag && typeof tag === "object") {
        const label = tag.display_name || tag.name || tag.zh || "";
        if (String(label).trim()) names.push(String(label).trim());
      }
    });
    return names.filter((name, index) => names.indexOf(name) === index).slice(0, 30);
  }

  async function lookupKunScore(keyword) {
    try {
      const res = await httpGet(
        "https://www.kungal.com/api/v1/search/works?q=" + encodeURIComponent(keyword) + "&page=1&limit=8&include_nsfw=true",
        { Accept: "application/json" }
      );
      const data = JSON.parse(res.body || "{}");
      const work = pickWork(data.items || [], [keyword]);
      const score = work ? asTen(work.rating_score) : null;
      if (!work || score == null) return { ok: false, reason: "没有对应评分" };
      return { ok: true, score: score, votes: work.rating_count, name: (workNames(work)[0] || "") };
    } catch (e) {
      return { ok: false, reason: "站点暂时连不上" };
    }
  }

  async function kunProfile(queries) {
    if (!queries.length) return { ok: false, reason: "没有作品名" };
    try {
      const res = await httpGet(
        "https://www.kungal.com/api/v1/search/works?q=" + encodeURIComponent(queries[0]) + "&page=1&limit=8&include_nsfw=true",
        { Accept: "application/json" }
      );
      const data = JSON.parse(res.body || "{}");
      const work = pickWork(data.items || [], queries);
      if (!work || !work.id) return { ok: false, reason: "鲲没有匹配到这部作品" };
      let source = work;
      try {
        const detail = await httpGet("https://www.kungal.com/api/v1/works/" + encodeURIComponent(work.id), { Accept: "application/json" });
        const parsed = JSON.parse(detail.body || "{}");
        if (parsed && typeof parsed === "object" && parsed.id) source = parsed;
      } catch (e) {}
      return {
        ok: true,
        name: workNames(source)[0] || "",
        score: asTen(source.rating_score),
        intro: introText(source.intros),
        tags: tagNames(source.tags),
        page: "https://www.kungal.com/galgame/" + work.id
      };
    } catch (e) {
      return { ok: false, reason: "鲲站点暂时连不上" };
    }
  }

  async function touchProfile(queries) {
    const token = (Android.getPref("touchgal_token") || "").trim();
    if (!token) return { ok: false, reason: "未配置 TouchGal Token" };
    const keyword = queries[0] || "";
    if (keyword.length < 3) return { ok: false, reason: "关键词太短，TouchGal 不接受" };
    try {
      const res = await httpGet(
        "https://developer.touchgal.com/api/v1/games/search?keyword=" + encodeURIComponent(keyword) + "&page=1&limit=8&allowNsfw=true",
        { Accept: "application/json", Authorization: "Bearer " + token }
      );
      const data = JSON.parse(res.body || "{}");
      const items = (data.data && data.data.items) || data.items || [];
      const game = pickWork(items, queries);
      if (!game) return { ok: false, reason: "TouchGal 没有匹配到这部作品" };
      const uniqueId = game.uniqueId || game.id || "";
      let detail = game;
      if (uniqueId) {
        try {
          const fetched = await httpGet("https://developer.touchgal.com/api/v1/games/" + encodeURIComponent(uniqueId), {
            Accept: "application/json",
            Authorization: "Bearer " + token
          });
          const parsed = JSON.parse(fetched.body || "{}");
          detail = (parsed.data && typeof parsed.data === "object") ? parsed.data : parsed;
        } catch (e) {}
      }
      let intro = "";
      ["introduction", "intro", "description", "summary", "content"].some((key) => {
        const value = detail && detail[key];
        if (typeof value === "string" && value.trim()) { intro = value.trim(); return true; }
        return false;
      });
      return {
        ok: true,
        name: (detail && detail.name) || game.name || "",
        score: asTen(detail && detail.rating),
        intro: intro,
        tags: tagNames(detail && detail.tags),
        page: uniqueId ? ("https://www.touchgal.ink/" + uniqueId) : ""
      };
    } catch (e) {
      const msg = String(e && e.message || e);
      if (/401|403/.test(msg)) return { ok: false, reason: "TouchGal Token 无效或被拦截" };
      return { ok: false, reason: "TouchGal 暂时连不上" };
    }
  }

  function hasKana(text) {
    return /[\u3040-\u30ff\u4e00-\u9fff]/.test(text || "");
  }

  function addTerm(terms, text) {
    const cleaned = String(text || "").replace(/\s+/g, " ").trim();
    if (normName(cleaned).length < 2 || terms.indexOf(cleaned) >= 0) return;
    terms.push(cleaned);
  }

  function shinnkuTerms(queries, mustFind) {
    const japanese = queries.filter(hasKana);
    const others = queries.filter((text) => japanese.indexOf(text) < 0);
    const terms = [];
    japanese.concat(others).forEach((text) => addTerm(terms, text));
    const source = japanese[0] || queries[0] || "";
    const chars = Array.from(source);
    const count = chars.length;
    if (count >= 4) {
      const half = Math.max(2, Math.floor(count / 2));
      addTerm(terms, chars.slice(0, half).join(""));
      const middle = Math.max(0, Math.floor((count - half) / 2));
      addTerm(terms, chars.slice(middle, middle + half).join(""));
      addTerm(terms, chars.slice(count - half).join(""));
      const quarter = Math.max(2, Math.floor(count / 4));
      const rounds = mustFind ? 8 : 4;
      for (let i = 0; i < rounds; i++) {
        if (count <= quarter) { addTerm(terms, source); break; }
        const start = Math.floor(Math.random() * (count - quarter + 1));
        addTerm(terms, chars.slice(start, start + quarter).join(""));
      }
    }
    return terms.slice(0, mustFind ? 12 : 8);
  }

  function decodePart(text) {
    try { return decodeURIComponent(text); } catch (e) { return text; }
  }

  function shinnkuEntry(url, fragment) {
    const frag = normName(fragment);
    if (!frag || String(url).toLowerCase().indexOf("magnet:") === 0) return "";
    const bare = String(url).split("?")[0];
    const pieces = bare.split("/").filter(Boolean);
    let prefix = "https://www.shinnku.com";
    let segments = pieces;
    if (bare.indexOf("http") === 0) {
      prefix = bare.split("://")[0] + "://" + pieces[0];
      segments = pieces.slice(1);
    }
    let decoded = segments.map(decodePart);
    if (decoded.length && /\.[A-Za-z0-9]{1,5}$/.test(decoded[decoded.length - 1])) {
      decoded = decoded.slice(0, -1);
      segments = segments.slice(0, -1);
    }
    for (let index = decoded.length - 1; index >= 0; index--) {
      if (normName(decoded[index]).indexOf(frag) >= 0) {
        return prefix + "/" + segments.slice(0, index + 1).join("/") + "/";
      }
    }
    return "";
  }

  async function searchShinnkuOnce(keyword) {
    try {
      const res = await httpGet("https://www.shinnku.com/search?q=" + encodeURIComponent(keyword), {
        Accept: "text/html",
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
      });
      const html = res.body || "";
      const re = /hover:underline" href="([^"]+)">\s*([^<]+?)\s*<\/a>/gi;
      const searchPage = "https://www.shinnku.com/search?q=" + encodeURIComponent(keyword);
      const found = [];
      const seen = {};
      let match;
      while ((match = re.exec(html)) && found.length < 5) {
        const href = match[1].trim();
        const name = match[2].replace(/\s+/g, " ").trim();
        if (href.toLowerCase().indexOf("magnet:") === 0) continue;
        const url = href.indexOf("http") === 0 ? href : ("https://www.shinnku.com" + (href.charAt(0) === "/" ? href : "/" + href));
        if (normName(name + " " + decodePart(url)).indexOf(normName(keyword)) < 0) continue;
        const folder = shinnkuEntry(url, keyword);
        const page = folder || searchPage;
        const label = name || keyword;
        const key = page + "|" + label;
        if (seen[key]) continue;
        seen[key] = 1;
        found.push({ name: label, page: page });
      }
      return found;
    } catch (e) {
      return [];
    }
  }

  async function shinnkuPages(queries, mustFind) {
    const terms = shinnkuTerms(queries, mustFind);
    if (!terms.length) return { ok: false, reason: "没有可用于真红小站的名字", pages: [] };
    const pages = [];
    const seen = {};
    let tried = 0;
    for (let i = 0; i < terms.length && pages.length < 3; i++) {
      tried += 1;
      const hits = await searchShinnkuOnce(terms[i]);
      hits.forEach((item) => {
        const marker = normName(item.name);
        if (!marker || seen[marker] || pages.length >= 3) return;
        seen[marker] = 1;
        pages.push(item);
      });
    }
    if (!pages.length) {
      return { ok: false, reason: "真红小站搜了 " + tried + " 次日文或英文片段，仍没有对上的目录", pages: [] };
    }
    return { ok: true, name: pages[0].name, page: pages[0].page, pages: pages, intro: "", tags: [] };
  }

  async function lookupSite(kind, keyword) {
    const headers = { Accept: "application/json", "User-Agent": "VNDB-Searcher/1.0 (Android; author: yulibaitiao)" };
    const token = Android.getPref("touchgal_token") || "";
    if (token) headers.Authorization = "Bearer " + token;
    const urls = ["https://www.touchgal.io/api/galgame"];
    const keys = ["q", "keywords"];
    for (let u = 0; u < urls.length; u++) {
      for (let i = 0; i < keys.length; i++) {
        const params = keys[i] + "=" + encodeURIComponent(keyword) + "&limit=5&page=1";
        const joiner = urls[u].indexOf("?") >= 0 ? "&" : "?";
        try {
          const res = await httpGet(urls[u] + joiner + params, headers);
          const data = JSON.parse(res.body || "{}");
          const hit = findScore(data, [normName(keyword)]);
          if (hit) return hit;
        } catch (e) {
          const msg = String(e && e.message || e);
          if (/401|403|登录|moment|challenge/i.test(msg)) return { ok: false, reason: "需要登录或被站点拦截" };
        }
      }
    }
    return { ok: false, reason: "没有对应评分" };
  }

  async function attachCommunityScores(items) {
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      const keyword = it.original || it.title || "";
      const q = normName(keyword);
      let kun = { ok: false, reason: "无标题" };
      let touch = { ok: false, reason: "无标题" };
      if (q) {
        kun = await lookupKunScore(keyword);
        touch = await lookupSite("touchgal", keyword);
      }
      it.community = { kungal: kun, touchgal: touch };
      const nums = [];
      if (typeof it.rating === "number") nums.push(it.rating);
      if (kun.ok) nums.push(kun.score);
      if (touch.ok) nums.push(touch.score);
      it.composite = nums.length ? Math.round((nums.reduce((a, b) => a + b, 0) / nums.length) * 100) / 100 : null;
      const fmt = (label, row) => row && row.ok ? (label + " " + row.score.toFixed(2)) : (label + " —");
      const vndb = typeof it.rating === "number" ? it.rating.toFixed(2) : "—";
      const comp = typeof it.composite === "number" ? it.composite.toFixed(2) : "—";
      it.scoreLine = "VNDB " + vndb + " | " + fmt("鲲", kun) + " | " + fmt("TouchGal", touch) + " | 综合 " + comp;
    }
    items.sort((a, b) => (b.composite || -1) - (a.composite || -1));
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
      try { await attachCommunityScores(items); } catch (e) {}
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


  function showPage(id) {
    ["pageRecommend", "pageName", "pageTag"].forEach((page) => {
      const el = $(page);
      if (el) el.classList.toggle("hidden", page !== id);
    });
    document.querySelectorAll(".dock button").forEach((button) => {
      button.classList.toggle("on", button.getAttribute("data-page") === id);
    });
  }

  function bindExtra() {
    document.querySelectorAll(".dock button").forEach((button) => {
      button.addEventListener("click", () => showPage(button.getAttribute("data-page")));
    });
    if ($("nameGo")) $("nameGo").addEventListener("click", nameSearch);
    if ($("openTree")) $("openTree").addEventListener("click", toggleTree);
    if ($("treeFilter")) $("treeFilter").addEventListener("input", () => drawTree(filterTree($("treeFilter").value)));
    if ($("tagNlGo")) $("tagNlGo").addEventListener("click", searchTagsByWords);
    if ($("tagSearch")) $("tagSearch").addEventListener("click", () => tagWeightedSearch(false));
    if ($("tagRefresh")) $("tagRefresh").addEventListener("click", () => tagWeightedSearch(true));
    renderPicked();
  }

  const tagState = { roots: [], flat: [], picked: [], seen: [] };

  function tagLabel(id, en) { return state.zh[id] || en || id; }

  function ensureTagTree() {
    if (tagState.roots.length) return;
    const raw = JSON.parse(Android.getAsset("www/tags_tree.json") || "[]");
    const nodes = {};
    raw.forEach((row) => {
      const id = "g" + row.id;
      nodes[id] = { id: id, en: row.name || "", zh: tagLabel(id, row.name || ""), searchable: !!row.searchable, parents: (row.parents || []).map((pid) => "g" + pid), children: [] };
    });
    const roots = [];
    Object.keys(nodes).forEach((id) => {
      const node = nodes[id];
      const parents = node.parents.filter((pid) => nodes[pid]);
      node.parents = parents;
      node.also = parents.slice(1).map((pid) => nodes[pid].zh);
      if (!parents.length) roots.push(node);
      else nodes[parents[0]].children.push(node);
    });
    const byZh = (a, b) => a.zh.localeCompare(b.zh, "zh");
    function sortNode(node) { node.children.sort(byZh); node.children.forEach(sortNode); }
    roots.sort(byZh);
    roots.forEach(sortNode);
    tagState.roots = roots;
    tagState.flat = Object.keys(nodes).map((id) => nodes[id]);
  }

  function renderTreeNode(node) {
    const wrap = document.createElement("div");
    wrap.className = "node";
    const row = document.createElement("div");
    row.className = "row";
    let childBox = null;
    if (node.children && node.children.length) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "btn";
      toggle.textContent = "展开";
      childBox = document.createElement("div");
      childBox.className = "hidden";
      toggle.addEventListener("click", () => {
        const hiddenNow = childBox.classList.toggle("hidden");
        toggle.textContent = hiddenNow ? "展开" : "收起";
        if (!childBox.childElementCount) node.children.forEach((child) => childBox.appendChild(renderTreeNode(child)));
      });
      row.appendChild(toggle);
    }
    const label = document.createElement("span");
    const extra = (node.also || []).length ? "（也属于：" + node.also.join("、") + "）" : "";
    label.textContent = node.zh + (node.en && node.zh !== node.en ? " / " + node.en : "") + extra;
    row.appendChild(label);
    if (node.searchable) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "btn";
      add.textContent = "加入";
      add.addEventListener("click", () => addPicked(node));
      row.appendChild(add);
    }
    wrap.appendChild(row);
    if (childBox) wrap.appendChild(childBox);
    return wrap;
  }

  function drawTree(nodes) {
    const box = $("tagTree");
    box.innerHTML = "";
    nodes.forEach((node) => box.appendChild(renderTreeNode(node)));
  }

  function filterTree(word) {
    const q = (word || "").trim().toLowerCase();
    if (!q) return tagState.roots;
    return tagState.flat.filter((node) => (node.zh + " " + node.en + " " + node.id).toLowerCase().indexOf(q) >= 0).map((node) => Object.assign({}, node, { children: [] }));
  }

  function toggleTree() {
    $("tagTree").classList.toggle("hidden");
    if ($("tagTree").classList.contains("hidden")) return;
    ensureTagTree();
    drawTree(filterTree($("treeFilter").value));
  }

  function addPicked(tag) {
    if (tagState.picked.some((item) => item.id === tag.id)) return;
    tagState.picked.push({ id: tag.id, zh: tag.zh || tagLabel(tag.id, tag.en), level: 1 });
    tagState.seen = [];
    renderPicked();
  }

  function renderPicked() {
    const box = $("pickedTags");
    if (!box) return;
    box.innerHTML = "";
    if (!tagState.picked.length) { box.textContent = "还没有标签。从目录或搜索结果里添加。"; return; }
    tagState.picked.forEach((tag, index) => {
      const row = document.createElement("div");
      row.className = "row";
      const name = document.createElement("strong");
      name.textContent = tag.zh;
      const weight = document.createElement("input");
      weight.type = "number";
      weight.min = "0.6";
      weight.max = "2.8";
      weight.step = "0.1";
      weight.value = String(tag.level);
      weight.style.width = "84px";
      weight.addEventListener("change", () => {
        let value = Number(weight.value);
        if (value < 0.6) value = 0.6;
        if (value > 2.8) value = 2.8;
        tag.level = value;
        weight.value = String(value);
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn";
      remove.textContent = "移除";
      remove.addEventListener("click", () => { tagState.picked.splice(index, 1); tagState.seen = []; renderPicked(); });
      row.append(name, weight, remove);
      box.appendChild(row);
    });
  }

  async function searchTagsByWords() {
    const text = $("tagNl").value.trim();
    if (!text) return;
    const box = $("tagNlHits");
    box.innerHTML = "";
    findCandidates(text).slice(0, 12).forEach((hit) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = tagLabel(hit.id, hit.name);
      button.addEventListener("click", () => addPicked({ id: hit.id, zh: tagLabel(hit.id, hit.name), en: hit.name }));
      box.appendChild(button);
    });
  }

  function renderPlainCards(box, items) {
    box.innerHTML = "";
    items.forEach((it) => {
      const card = document.createElement("article");
      card.className = "result";
      const body = document.createElement("div");
      body.className = "body";
      const h = document.createElement("h3");
      h.textContent = it.title || "（无标题）";
      const meta = document.createElement("div");
      meta.className = "meta";
      const rating = typeof it.rating === "number" ? it.rating.toFixed(2) : "—";
      meta.textContent = (it.original || "") + " · 评分 " + rating + (it.scoreLine ? " · " + it.scoreLine : "");
      const desc = document.createElement("div");
      desc.className = "desc";
      desc.textContent = it.description || "";
      body.append(h, meta, desc);
      card.appendChild(body);
      card.addEventListener("click", () => openDetail(it));
      box.appendChild(card);
    });
  }

  async function tagWeightedSearch(refresh) {
    if (!tagState.picked.length) { toast("先加入标签"); return; }
    $("tagStatus").textContent = refresh ? "正在换一批…" : "正在按权重搜索…";
    const preds = tagState.picked.map((tag) => ["tag", "=", [tag.id, 0, Math.min(2.8, Math.max(0.6, Number(tag.level) || 0.6))]]);
    (refresh ? tagState.seen : []).slice(-25).forEach((id) => preds.push(["id", "!=", id]));
    const filters = preds.length === 1 ? preds[0] : ["and"].concat(preds);
    try {
      const items = await vndb({ filters: filters, sort: "rating", page: 1 });
      if (!refresh) tagState.seen = [];
      items.forEach((it) => { if (it.id && tagState.seen.indexOf(it.id) < 0) tagState.seen.push(it.id); });
      await decorate(items, { filters: filters, tag_roles: {} });
      $("tagStatus").textContent = "本次 " + items.length + " 部，按评分从高到低。累计看过 " + tagState.seen.length + " 部。";
      renderPlainCards($("tagResults"), items);
    } catch (err) {
      $("tagStatus").textContent = err.message || String(err);
    }
  }

  async function nameSearch() {
    const name = $("nameQuery").value.trim();
    if (!name) { toast("请输入作品名"); return; }
    const started = Date.now();
    $("nameGo").disabled = true;
    $("nameStatus").textContent = "正在查必应并分析名称…";
    try {
      let snippets = [];
      let bingOk = true;
      try {
        const page = await httpGet("https://www.bing.com/search?q=" + encodeURIComponent(name + " visual novel vndb") + "&setlang=zh-Hans", { "Accept-Language": "zh-CN,zh;q=0.9" });
        const html = page.body || "";
        const re = /<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi;
        let match;
        while ((match = re.exec(html)) && snippets.length < 6) {
          if (match[1].indexOf("bing.com") >= 0) continue;
          snippets.push({ title: match[2].replace(/<[^>]+>/g, ""), url: match[1] });
        }
      } catch (e) { bingOk = false; }
      const parsed = extractJson(await chat([
        { role: "system", content: "根据大致作品名和必应摘要，判断最可能的视觉小说。只输出 JSON：{\"zh\":\"\",\"en\":\"\",\"ja\":\"\",\"vndb_id\":\"\"}。没有依据就不要编造 vndb_id。" },
        { role: "user", content: JSON.stringify({ name: name, bing: snippets }) }
      ], 400));
      const names = [parsed.ja, parsed.en, parsed.zh, name].filter((item, index, arr) => item && arr.indexOf(item) === index);
      const items = [];
      const seen = {};
      if (/^v\d+$/.test(parsed.vndb_id || "")) {
        (await vndb({ filters: ["id", "=", parsed.vndb_id], sort: "searchrank" })).forEach((it) => { if (!seen[it.id]) { seen[it.id] = 1; items.push(it); } });
      }
      for (let i = 0; i < names.length && items.length < 10; i++) {
        const hits = await vndb({ filters: ["search", "=", names[i]], sort: "searchrank" });
        hits.forEach((it) => { if (it.id && !seen[it.id] && items.length < 10) { seen[it.id] = 1; items.push(it); } });
      }
      if (items.length) await attachCommunityScores(items);
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      $("nameStatus").textContent = "耗时 " + seconds + " 秒。" + (bingOk ? " 已参考必应。" : " 必应没有返回。") + " 判断：" + names.join(" / ");
      renderPlainCards($("nameResults"), items);
      if (!items.length) $("nameStatus").textContent += " 没有在 VNDB 对上。";
    } catch (err) {
      $("nameStatus").textContent = err.message || String(err);
    } finally {
      $("nameGo").disabled = false;
    }
  }

  function bind() {
    $("saveKey").addEventListener("click", () => {
      const key = $("apiKey").value.trim();
      if (key.length < 8) { toast("请填入有效的 API Key"); return; }
      Android.setApiKey(key);
      Android.setPref("touchgal_token", $("touchToken").value.trim());
      state.apiKey = key;
      showMain(true);
    });
    $("tutorialOk").addEventListener("click", () => {
      Android.setPref("tutorial_seen", "1");
      $("tutorial").classList.add("hidden");
    });
    $("openTutorial").addEventListener("click", () => $("tutorial").classList.remove("hidden"));
    $("openAbout").addEventListener("click", () => {
      $("touchToken2").value = Android.getPref("touchgal_token") || "";
      $("about").classList.remove("hidden");
    });
    $("aboutClose").addEventListener("click", () => {
      Android.setPref("touchgal_token", $("touchToken2").value.trim());
      $("about").classList.add("hidden");
    });
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
    bindExtra();
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
