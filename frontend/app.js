(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let lang = localStorage.getItem("kb-lang") || "en";
  let status = {};
  let family = [];
  let checkClip = null; // {blob, name, seconds}
  let famClip = null;
  let lastResult = null;
  let lastLive = null;

  const profile = "bank";
  const t = (k, vars = {}) => {
    const L = I18N[lang] || {};
    let s = (profile === "bank" && (L["bank_" + k] ?? I18N.en["bank_" + k])) || (L[k] ?? I18N.en[k] ?? k);
    for (const [a, b] of Object.entries(vars)) s = s.replaceAll(`{${a}}`, b);
    return s;
  };

  // ---------------------------------------------------------------- i18n
  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
    document.querySelectorAll(".lang button").forEach((b) => b.classList.toggle("on", b.dataset.lang === lang));
    $("famName").placeholder = t("fam_name");
    $("famRel").placeholder = t("fam_relation");
    $("famPhone").placeholder = t("fam_phone");
    $("callerNum").placeholder = $("liveNum").placeholder = "+91 98xxx xxxxx";
    renderBanner();
    renderClaimed();
    renderFamily();
    renderBot();
    renderTxCustomers();
    renderTx();
    if (lastResult) renderResult(lastResult);
    if (lastLive) renderResult(lastLive, $("liveResult"));
    loadHistory();
  }
  document.querySelectorAll(".lang button").forEach((b) =>
    b.addEventListener("click", () => { lang = b.dataset.lang; localStorage.setItem("kb-lang", lang); applyLang(); }));

  // ---------------------------------------------------------------- tabs
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
      document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
      if (b.dataset.tab !== "live") stopInputPreview();
    }));

  // ---------------------------------------------------------------- api
  // Agent PIN: only needed when the server sets AGENT_PIN (a public demo URL). Asked once, kept on this device.
  const getPin = () => localStorage.getItem("kb-pin") || "";
  // The PIN is never put in a URL (it would reach server logs and browser history): HTTP calls send it as a
  // header, and the live socket sends it as a subprotocol ("kb.<hex>"), since browsers cannot set WS headers.
  const wsProtocols = () => {
    const bytes = new TextEncoder().encode(getPin());
    return getPin() ? ["kb." + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")] : undefined;
  };
  let pinAsk = null;
  function askPin() {
    pinAsk = pinAsk || Promise.resolve().then(() => {
      const p = window.prompt("Agent PIN");
      if (p) localStorage.setItem("kb-pin", p.trim());
      return !!p;
    }).finally(() => { pinAsk = null; });
    return pinAsk;
  }

  async function api(path, opts = {}, retried = false) {
    const headers = new Headers(opts.headers || {});
    if (getPin()) headers.set("X-Agent-Pin", getPin());
    const r = await fetch(path, { ...opts, headers });
    const body = await r.json().catch(() => ({}));
    if (r.status === 401 && body?.detail?.code === "invalid_pin" && !retried) {
      localStorage.removeItem("kb-pin");
      if (await askPin()) return api(path, opts, true);
    }
    if (!r.ok) {
      const code = body?.detail?.code || "generic";
      const e = new Error(code);
      e.code = code;
      throw e;
    }
    return body;
  }
  // Like fetch(), but sends the PIN and asks for it once if the server rejects it. Returns the Response.
  async function authFetch(path, retried = false) {
    const r = await fetch(path, { headers: getPin() ? { "X-Agent-Pin": getPin() } : {} });
    if (r.status === 401 && !retried) {
      localStorage.removeItem("kb-pin");
      if (await askPin()) return authFetch(path, true);
    }
    return r;
  }
  // Report and CSV links need the PIN header, which a plain link cannot send: fetch, then open the blob.
  document.addEventListener("click", async (ev) => {
    const a = ev.target.closest && ev.target.closest('a[data-dl], a[href="/api/incidents.csv"]');
    if (!a) return;
    ev.preventDefault();
    const url = a.getAttribute("href");
    const csv = url.endsWith(".csv");
    const w = csv ? null : window.open("", "_blank");  // open synchronously so the popup blocker allows it
    try {
      const r = await authFetch(url);
      if (!r.ok) throw Object.assign(new Error("dl"), { code: r.status === 429 ? "too_many_attempts" : "generic" });
      const u = URL.createObjectURL(await r.blob());
      if (csv) {
        const tmp = document.createElement("a");
        tmp.href = u; tmp.download = "kaunbol-incidents.csv";
        document.body.appendChild(tmp); tmp.click(); tmp.remove();
        setTimeout(() => URL.revokeObjectURL(u), 30000);
      } else {
        w.location = u;
      }
    } catch (e) {
      if (w) w.close();
      window.alert(errText(e));
    }
  });
  const errText = (e) => (I18N.en["err_" + e.code] ? t("err_" + e.code) : t("err_generic"));

  function renderBanner() {
    const b = $("modeBanner");
    const demo = status.mode === "demo";
    const ready = demo || status.mode === "live";
    b.hidden = !ready;
    $("demoBox").hidden = !demo;
    $("liveDemoBox").hidden = !demo;
    if (!ready) return;
    b.className = "banner " + (demo ? "demo" : "live");
    let txt = demo ? t("demo_banner") : t("live_banner");
    const bal = status.credits?.available ?? status.credits?.balance;
    if (!demo && bal != null) txt += ` · credits: ${bal}`;
    b.textContent = txt;
    renderLiveSamples();
  }

  async function loadStatus() {
    try { status = await api("/api/status"); } catch {}
    renderBanner();
  }
  async function loadFamily() {
    try { family = await api("/api/family"); } catch { family = []; }
    renderClaimed();
    renderFamily();
    renderTxCustomers();
  }

  // ------------------------------------------------------------ recorders
  function recorderUI(btn, timeEl, meterEl, onDone, getOpts, onTx) {
    let rec = null;
    btn.addEventListener("click", async () => {
      if (!rec) {
        rec = new KBAudio.Recorder({
          onTick: (s) => (timeEl.textContent = s.toFixed(1) + " s"),
          onLevel: (v) => (meterEl.style.width = Math.min(100, v * 160) + "%"),
          onTranscript: onTx,
        });
        try {
          await rec.start(getOpts());
        } catch {
          rec = null;
          alert(t("err_mic"));
          return;
        }
        btn.classList.add("on");
        btn.querySelector("[data-i18n]").textContent = t("stop");
      } else {
        const r = rec; rec = null;
        btn.classList.remove("on");
        btn.querySelector("[data-i18n]").textContent = t("record");
        meterEl.style.width = "0";
        const out = await r.stop();
        onDone({ blob: out.blob, name: "recording.wav", seconds: out.seconds });
      }
    });
  }

  function showClip(el, clip) {
    el.hidden = false;
    const url = URL.createObjectURL(clip.blob);
    el.innerHTML = `<span>${esc(t("clip_ready"))}${clip.seconds ? " · " + clip.seconds.toFixed(1) + " " + esc(t("seconds")) : ""}</span><audio controls src="${url}"></audio>`;
  }

  // ---------------------------------------------------------------- check
  function renderClaimed() {
    const sel = $("claimed");
    const cur = sel.value;
    sel.innerHTML = `<option value="">${esc(t("unknown_person"))}</option>` +
      family.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}${m.relation ? " (" + esc(m.relation) + ")" : ""}</option>`).join("");
    sel.value = family.some((m) => m.id === cur) ? cur : "";
    $("noFamilyHint").hidden = family.length > 0;
    const ls = $("liveClaimed"), lcur = ls.value;
    ls.innerHTML = sel.innerHTML;
    ls.value = family.some((m) => m.id === lcur) ? lcur : "";
  }

  recorderUI($("recBtn"), $("recTime"), $("meter"),
    (clip) => { checkClip = clip; showClip($("clipReady"), clip); },
    () => ({ lang, transcribe: $("liveTx").checked }),
    (text) => ($("transcript").value = text));

  $("fileIn").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    checkClip = { blob: f, name: f.name, seconds: await KBAudio.durationOf(f) };
    showClip($("clipReady"), checkClip);
  });

  $("checkBtn").addEventListener("click", async () => {
    const err = $("checkErr");
    err.hidden = true;
    if (!checkClip) { err.textContent = t("err_no_audio"); err.hidden = false; return; }
    if (!$("numHidden").checked && $("callerNum").value.replace(/\D/g, "").length < 6) {
      err.textContent = t("err_number_required"); err.hidden = false; $("callerNum").focus(); return;
    }
    if (checkClip.seconds && checkClip.seconds < 4) { err.textContent = t("err_audio_too_short"); err.hidden = false; return; }
    const btn = $("checkBtn");
    btn.disabled = true;
    btn.textContent = t("checking");
    const fd = new FormData();
    fd.append("file", checkClip.blob, checkClip.name);
    fd.append("claimed_member", $("claimed").value);
    fd.append("transcript", $("transcript").value);
    fd.append("demo_scenario", $("scenario").value);
    fd.append("caller_number", $("numHidden").checked ? "" : $("callerNum").value);
    fd.append("number_hidden", $("numHidden").checked ? "true" : "false");
    fd.append("action", $("action").value);
    fd.append("auto_detect", $("autoDetect").checked ? "true" : "false");
    try {
      lastResult = await api("/api/check", { method: "POST", body: fd });
      lastResult._number = $("numHidden").checked ? "" : $("callerNum").value;
      renderResult(lastResult);
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
      loadHistory();
      loadStatus(); // refresh the credit balance in the banner
    } catch (e) {
      err.textContent = errText(e);
      err.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = t("check_btn");
    }
  });

  const LEVEL_ICON = { high: "!", medium: "?", low: "✓" };
  const pill = (cls, txt) => `<span class="pill ${cls}">${esc(txt)}</span>`;

  function renderResult(res, target = $("result")) {
    let ident;
    const v = res.verdict;
    const name = res.claimed?.name || t("someone");
    const has = (r) => v.reasons.includes(r);
    const df = res.deepfake && !res.deepfake.error ? res.deepfake : null;

    // voice
    const vReason = v.reasons.find((r) => r.startsWith("voice_"));
    const vCls = { voice_fake: "bad", voice_spliced: "bad", voice_borderline: "warn", voice_real: "good" }[vReason] || "na";
    let voice = `<div class="sig-h"><span>${esc(t("sig_voice"))}</span>${pill(vCls, df ? df.verdict?.toUpperCase() : "—")}</div>
      <p class="sig-t">${esc(t("r_" + vReason))}</p>`;
    if (df) {
      const sc = Number(df.score || 0);
      const col = sc >= 0.5 ? "var(--high)" : sc >= 0.4 ? "var(--med)" : "var(--low)";
      voice += `<div class="num">${esc(t("score_ai"))}: ${sc.toFixed(2)} / 1.00 · Aletheia</div>
        <div class="bar"><i style="width:${sc * 100}%;background:${col}"></i></div>`;
      const segs = df.segments || [];
      if (segs.length > 1) {
        const total = df.duration_seconds || segs[segs.length - 1].end;
        voice += `<div class="num" style="margin-top:10px">${esc(t("timeline"))}</div><div class="timeline">` +
          segs.map((s) => {
            const fake = s.verdict ? s.verdict === "fake" : s.score >= 0.5;
            const a = 0.25 + Math.min(1, s.score) * 0.75;
            const c = fake ? `rgba(200,65,47,${a})` : `rgba(35,120,91,${0.25 + (1 - s.score) * 0.5})`;
            return `<span title="${s.start}–${s.end}s · ${Number(s.score).toFixed(2)}" style="background:${c}"></span>`;
          }).join("") + `</div><div class="tl-axis"><span>0 s</span><span>${Number(total).toFixed(1)} s</span></div>`;
      }
    }

    // identity
    const iReason = v.reasons.find((r) => r.startsWith("identity_"));
    const iCls = { identity_match: "good", identity_no_match: "bad", identity_check_failed: "warn" }[iReason] || "na";
    ident = `<div class="sig-h"><span>${esc(t("sig_identity"))}</span>${pill(iCls, res.identity ? res.identity.decision.replace("_", " ").toUpperCase() : "—")}</div>
      <p class="sig-t">${esc(t("r_" + iReason, { name }))}</p>`;
    if (res.identity) {
      const s = Number(res.identity.similarity);
      ident += `<div class="num">${esc(t("similarity"))}: ${s.toFixed(2)} (−1 … 1) · Eidos</div>
        <div class="bar"><i style="width:${((s + 1) / 2) * 100}%;background:${iCls === "good" ? "var(--low)" : "var(--high)"}"></i></div>`;
    }
    if (iReason === "identity_match" && (has("voice_fake") || has("voice_spliced"))) {
      ident += `<p class="sig-t"><b>⚠ ${esc(t("both_checks"))}</b></p>`;
    }

    // script
    const sReason = v.reasons.find((r) => r.startsWith("script_"));
    const sCls = { script_high: "bad", script_medium: "warn", script_low: "warn", script_none: "good" }[sReason] || "na";
    let script = `<div class="sig-h"><span>${esc(t("sig_script"))}</span>${pill(sCls, res.script.analysed ? res.script.score + "/100" : "—")}</div>
      <p class="sig-t">${esc(t("r_" + sReason))}</p>`;
    const hits = Object.entries(res.script.hits || {});
    if (hits.length) {
      script += `<div class="chips">` + hits.map(([cat, words]) =>
        `<span class="chip"><b>${esc(t("cat_" + cat))}</b> “${esc(words.join("”, “"))}”</span>`).join("") + `</div>`;
    }

    let how = "";
    if (res.identified_by === "number") how = t("auto_by_number", { name });
    else if (res.identified_by === "transcript") how = t("auto_by_name", { name });
    else if (res.identified_by === "voice") how = t("auto_by_voice", { name });
    if (res.not_family) {
      ident = `<div class="sig-h"><span>${esc(t("sig_identity"))}</span>${pill("warn", t("pill_not_family"))}</div>
        <p class="sig-t">${esc(t("not_family", { name: res.not_family.closest }))}</p>`;
    }
    if (how) ident = `<p class="auto">${esc(how)}</p>` + ident;
    const nReasons = v.reasons.filter((r) => r.startsWith("number_"));
    const NCLS = { number_match: "good", number_mismatch: "bad", number_flagged: "bad", number_unknown: "warn",
      number_hidden: "warn", number_first_time: "warn", number_international: "warn" };
    const numberRow = nReasons.length ? `<li><div class="sig-h"><span>${esc(t("sig_number"))}</span>
      <span>${nReasons.map((r) => pill(NCLS[r], t("pill_" + r))).join(" ")}</span></div>
      ${nReasons.map((r) => `<p class="sig-t">${esc(t("r_" + r, { name }))}</p>`).join("")}
      ${res.number_info?.last_alert ? `<p class="sig-t"><b>${esc(lastAlertText(res.number_info.last_alert))}</b></p>` : ""}</li>` : "";
    const rawNum = res._number || "";
    const context = ["scripted_unverified", "real_but_scripted", "action_high_impact"].filter((r) => v.reasons.includes(r))
      .map((r) => `<p class="context">${esc(t("r_" + r, { name }))}</p>`).join("");
    const lvlTxt = t("lvl_" + v.level);
    const warn = t("warn_text", { name, level: lvlTxt });

    if (target === $("result")) $("liveResult").innerHTML = "";
    else $("result").innerHTML = "";
    target.hidden = false;
    target.innerHTML = `
      <div class="verdict ${v.level}">
        <div class="head"><div class="badge">${LEVEL_ICON[v.level]}</div>
          <h2>${esc(lvlTxt)}${res.simulated ? `<span class="tag">${esc(t("simulated_tag"))}</span>` : ""}</h2></div>
        ${context}
        <p class="note">${esc(t("evidence_note"))}</p>
      </div>
      <div class="card"><ul class="signals"><li>${voice}</li><li>${ident}</li>${numberRow}<li>${script}</li></ul></div>
      <div class="card">
        <h3>${esc(t("next_steps"))}</h3>
        <ol class="steps">${v.next_steps.map((s) => `<li>${esc(t("s_" + s))}</li>`).join("")}</ol>
        ${v.level !== "low" ? `
          <div class="row" style="margin-top:14px">
            <a class="btn wa" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(warn)}">${esc(t("warn_btn"))}</a>
            <button class="btn" id="copyWarn">${esc(t("copy_btn"))}</button>
          </div>` : ""}
      </div>
      ${res.incident_id || rawNum ? `
      <div class="card subtle row">
        ${res.incident_id ? `<a class="btn" target="_blank" rel="noopener" href="/api/incidents/${res.incident_id}/report" data-dl>${esc(t("report_btn"))}</a>` : ""}
        ${rawNum ? `<button class="btn" data-numset="flagged">${esc(t("num_flag"))}</button>
          <button class="btn" data-numset="trusted">${esc(t("num_trust"))}</button>` : ""}
        <span id="numSetMsg" class="hint"></span>
      </div>` : ""}
      ${res.claimed && target === $("result") ? `
      <div class="card ask-card">
        <b>${esc(t("ask_card_title"))}</b>
        <p class="hint">${esc(t("ask_why"))}</p>
        <button class="btn ask-btn" id="askBtn">${esc(t("ask_btn", { name: res.claimed.name }))}</button>
        <div id="askBox" class="ask-box" hidden></div>
      </div>` : ""}
      ${target === $("result") ? `
      <div class="card replay" id="replayBox">
        <b>${esc(t("replay_title"))}</b>
        <p class="hint">${esc(t("replay_intro"))}</p>
        <button class="btn" id="replayStart">${esc(t("replay_start"))}</button>
        <div id="replayArea"></div>
      </div>` : ""}
      ${res.operation_id ? `
      <div class="card subtle" id="fbBox">
        <span>${esc(t("fb_q"))}</span>
        <button class="btn" data-fb="1">${esc(t("fb_yes"))}</button>
        <button class="btn" data-fb="0">${esc(t("fb_no"))}</button>
      </div>` : ""}`;

    $("copyWarn")?.addEventListener("click", () => navigator.clipboard.writeText(warn));
    target.querySelectorAll("[data-numset]").forEach((b) => b.addEventListener("click", async () => {
      try {
        await api("/api/numbers", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ number: rawNum, status: b.dataset.numset, note: "set from result" }) });
        $("numSetMsg").textContent = t("num_saved_" + b.dataset.numset);
        loadNumbers();
      } catch (e) { $("numSetMsg").textContent = errText(e); }
    }));
    $("replayStart")?.addEventListener("click", () => startReplay(res));
    $("askBtn")?.addEventListener("click", () => {
      const m = family.find((f) => f.id === res.claimed.id) || res.claimed;
      askCustomer(m, res._number, $("askBox"), (st) => {
        if (st === "denied" || st === "failed") {
          const v = target.querySelector(".verdict");
          v.className = "verdict high";
          v.querySelector(".badge").textContent = "!";
          v.querySelector("h2").textContent = t("ask_hold_title");
        } else if (st === "confirmed") {
          const v = target.querySelector(".verdict");
          v.className = "verdict low";
          v.querySelector(".badge").textContent = "✓";
          v.querySelector("h2").textContent = t("ask_verified_title");
        }
      });
    });
    document.querySelectorAll("[data-fb]").forEach((b) => b.addEventListener("click", async () => {
      const correct = b.dataset.fb === "1";
      const shown = df?.verdict;
      const label = correct ? shown : shown === "fake" ? "real" : "fake";
      try {
        await api("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ operation_id: res.operation_id, correct, label }) });
        $("fbBox").textContent = res.simulated ? t("fb_demo") : t("fb_thanks");
      } catch (e) { $("fbBox").textContent = errText(e); }
    }));
  }

  async function loadHistory() {
    let h = [];
    try { h = await api("/api/history"); } catch {}
    const col = { high: "var(--high)", medium: "var(--med)", low: "var(--low)" };
    $("history").innerHTML = h.slice(0, 6).map((x) =>
      `<li><span class="lv" style="background:${col[x.level]}"></span>${new Date(x.at * 1000).toLocaleString()} · ${esc(x.claimed || t("unknown_person"))} · ${esc(t("lvl_" + x.level))}${x.simulated ? " · " + esc(t("simulated_tag")) : ""}</li>`).join("");
  }

  // ------------------------------------------------------------- live call
  let live = null; // {ws, src, t0, timer, updates:[], member, ended}

  function setLight(level, head, sub) {
    $("liveLight").className = "light " + level;
    $("liveHeadline").textContent = head;
    $("liveSub").textContent = sub || "";
  }

  function liveError(code) {
    const e = $("liveErr");
    e.textContent = I18N.en["err_" + code] ? t("err_" + code) : t("err_generic");
    e.hidden = false;
  }

  function drawSpark() {
    $("liveSpark").innerHTML = live.updates.map((u) => {
      const h = 6 + u.score * 34;
      const c = u.verdict === "fake" ? "var(--high)" : u.score >= 0.4 ? "var(--med)" : "var(--low)";
      return `<i title="${u.seconds}s · ${u.score.toFixed(2)}" style="height:${h}px;background:${c}"></i>`;
    }).join("");
  }

  function onLiveMsg(m) {
    const name = live.member?.name || t("someone");
    if (m.type === "number_info") {
      showBadge($("liveCallBadge"), m);
    } else if (m.type === "ready") {
      setLight("idle", t("live_listening"), t("live_waiting_speech") + (m.simulated ? " · " + t("simulated_tag") : ""));
      if (live.bot?.state === "running" && !live.botLines.length) playBotLine("welcome");
    } else if (m.type === "bot_state") {
      live.bot = { ...live.bot, ...m };
      if (m.state !== "running") window.speechSynthesis?.cancel();
      renderBot();
    } else if (m.type === "bot_gate" && live.bot) {
      live.bot.can_step = m.can_step;
      renderBot();
    } else if (m.type === "bot_line") {
      playBotLine(m.line);
    } else if (m.type === "deepfake") {
      live.updates.push({ score: Number(m.score), verdict: m.verdict, seconds: m.seconds });
      drawSpark();
      const fake = m.verdict === "fake";
      $("liveVoicePill").className = "pill " + (fake ? "bad" : "good");
      $("liveVoicePill").textContent = (m.verdict || "").toUpperCase() + " " + Number(m.score).toFixed(2);
      $("liveVoiceText").textContent = t(fake ? "r_voice_fake" : "r_voice_real") + " (" +
        (m.early ? t("live_early") : t("live_update", { n: m.sequence })) + " · " + m.seconds + " s)";
    } else if (m.type === "identified") {
      live.member = family.find((f) => f.id === m.member?.id) || m.member;
      updateAskLabel();
      live.identifiedBy = m.by;
      $("liveIdText").textContent = t(m.by === "number" ? "auto_by_number" : m.by === "transcript" ? "auto_by_name"
        : m.by === "voice" ? "auto_by_voice" : "live_id_waiting", { name: m.member?.name || "" })
        + (m.by === "transcript" || m.by === "voice" ? " " + t("live_checking_now") : "");
      $("liveIdPill").className = "pill warn"; $("liveIdPill").textContent = "…";
    } else if (m.type === "not_family") {
      live.member = null;
      $("liveIdPill").className = "pill warn"; $("liveIdPill").textContent = t("pill_not_family");
      $("liveIdText").textContent = t("not_family", { name: m.closest });
    } else if (m.type === "verification_error") {
      $("liveIdPill").className = "pill warn"; $("liveIdPill").textContent = "—";
      $("liveIdText").textContent = t("r_identity_check_failed");
    } else if (m.type === "verification") {
      const ok = m.result?.decision === "match";
      $("liveIdPill").className = "pill " + (ok ? "good" : "bad");
      $("liveIdPill").textContent = (m.result?.decision || "").replace("_", " ").toUpperCase() + " " + Number(m.result?.similarity ?? 0).toFixed(2);
      $("liveIdText").textContent = t(ok ? "r_identity_match" : "r_identity_no_match", { name });
    } else if (m.type === "script") {
      $("liveScriptPill").className = "pill " + ({ high: "bad", medium: "warn", low: "warn", none: "good" }[m.level] || "na");
      $("liveScriptPill").textContent = m.analysed ? m.score + "/100" : "—";
      $("liveChips").innerHTML = Object.entries(m.hits || {}).map(([cat, w]) =>
        `<span class="chip"><b>${esc(t("cat_" + cat))}</b> “${esc(w.join("”, “"))}”</span>`).join("");
    } else if (m.type === "risk") {
      const v = m.verdict;
      if (!m.final) {
        setLight(v.level, t("lvl_" + v.level), t("evidence_note"));
        const high = v.level === "high";
        if (high && $("hangup").hidden) navigator.vibrate?.([300, 150, 300]);
        $("hangup").hidden = !high;
      } else {
        endLiveUI();
        if (live.sourceKind !== "file" && !live.signalDetected) {
          setLight("idle", t("input_no_audio"), t("input_no_audio_help"));
          $("liveResult").hidden = true;
          return;
        }
        setLight(v.level, t("lvl_" + v.level), t("live_final"));
        $("liveLight").classList.add("done");
        $("hangup").hidden = v.level !== "high";
        lastLive = { verdict: v, deepfake: m.deepfake, identity: m.identity, script: m.script,
          claimed: m.claimed || live.member, identified_by: m.identified_by, operation_id: null, simulated: status.mode === "demo",
          incident_id: m.incident_id, number_info: m.number_info, _number: live.number };
        renderResult(lastLive, $("liveResult"));
        loadStatus();
      }
    } else if (m.type === "error") {
      liveError(m.code);
      endLiveUI();
    }
  }

  function renderBot() {
    const visible = profile === "bank" && Boolean(live?.bot);
    $("botLane").hidden = !visible;
    if (!visible) {
      if (live?.bot) window.speechSynthesis?.cancel();
      return;
    }
    const { state, reason } = live.bot;
    $("botState").textContent = t("bot_" + state);
    $("botState").className = "pill " + (state === "running" ? "good" : "bad");
    $("botReason").textContent = state === "running" && !live.bot.can_step && !live.botLines.includes("review")
      ? t("bot_wait_checks") : reason === "started" ? "" : t("bot_reason_" + reason);
    $("botLines").replaceChildren(...live.botLines.map((line) => {
      const item = document.createElement("li");
      item.textContent = t("bot_line_" + line);
      return item;
    }));
    $("botStep").disabled = live.ended || live.stopping || state !== "running" || !live.bot.can_step;
    $("botHandover").disabled = live.ended || live.stopping || state === "handed_over";
    $("botResume").disabled = live.ended || live.stopping || state === "running";
  }

  function playBotLine(line) {
    if (profile !== "bank" || live?.ended || live?.stopping || live?.bot?.state !== "running") return;
    if (!["welcome", "review"].includes(line) || live.botLines.includes(line)) return;
    live.botLines.push(line);
    renderBot();
    if (window.speechSynthesis && window.SpeechSynthesisUtterance) {
      const speech = new SpeechSynthesisUtterance(t("bot_line_" + line));
      speech.lang = { en: "en-IN", hi: "hi-IN", kn: "kn-IN" }[lang] || "en-IN";
      window.speechSynthesis.speak(speech);
    }
  }

  for (const [id, action] of [["botStep", "step"], ["botHandover", "handover"], ["botResume", "resume"]]) {
    $(id).addEventListener("click", () => {
      if (profile === "bank" && !live?.ended && !live?.stopping && live?.ws.readyState === 1) {
        live.ws.send(JSON.stringify({ type: "bot_control", action }));
      }
    });
  }

  function endLiveUI() {
    if (!live) return;
    live.ended = true;
    if (live.bot) window.speechSynthesis?.cancel();
    live.src?.stop();
    live.pcStream?.getTracks().forEach((track) => track.stop());
    clearInterval(live.timer);
    liveCh?.stop?.();
    renderBot();
    $("liveLight").classList.add("done");
    $("liveStop").hidden = true;
    $("liveChallenge").hidden = true;
    $("liveSetup").hidden = false;
  }

  let inputPreview = null;
  let inputLastSound = -Infinity;
  let inputPreference = localStorage.getItem("kb-live-input") || "";

  function inputMessage(id, key) {
    const target = $(id);
    if (target.dataset.i18n === key && target.textContent) return;
    target.dataset.i18n = key;
    target.textContent = t(key);
  }

  async function refreshInputs() {
    const select = $("liveInput");
    let devices = [];
    try { devices = await navigator.mediaDevices.enumerateDevices(); } catch {}
    const inputs = devices.filter((d) => d.kind === "audioinput" && d.label
      && d.deviceId && !["default", "communications"].includes(d.deviceId));
    select.replaceChildren(new Option(t("input_default"), ""),
      ...inputs.map((d) => new Option(d.label, d.deviceId)));
    select.value = inputs.some((d) => d.deviceId === inputPreference) ? inputPreference : "";
  }

  function stopInputPreview() {
    if (!inputPreview) return;
    inputPreview.stop();
    inputPreview = null;
    $("inputTest").disabled = false;
    $("inputTestMeter").hidden = true;
    inputMessage("inputTest", "input_test");
    inputMessage("inputMessage", "input_help");
  }

  async function startInputPreview() {
    const preview = new KBAudio.InputPreview();
    inputPreview = preview;
    inputLastSound = -Infinity;
    $("inputTest").disabled = true;
    $("inputTestMeter").hidden = false;
    $("inputPreviewLevel").style.width = "0%";
    inputMessage("inputMessage", "input_starting");
    try {
      await preview.start($("liveInput").value, ({ peak, rms }) => {
        if (inputPreview !== preview) return;
        const now = performance.now();
        if (rms >= KBAudio.INPUT_SIGNAL_LEVEL) inputLastSound = now;
        $("inputPreviewLevel").style.width = Math.min(100, peak * 500) + "%";
        inputMessage("inputMessage", now - inputLastSound < 1500 ? "input_receiving" : "input_waiting");
      });
      if (inputPreview !== preview) return;
      await refreshInputs();
      if (inputPreview === preview) inputMessage("inputTest", "input_stop_test");
    } catch {
      if (inputPreview !== preview) return;
      stopInputPreview();
      inputMessage("inputMessage", "err_input_unavailable");
    } finally {
      if (!inputPreview || inputPreview === preview) $("inputTest").disabled = false;
    }
  }

  $("inputTest").addEventListener("click", () => {
    if (inputPreview) stopInputPreview();
    else startInputPreview();
  });
  $("liveInput").addEventListener("change", () => {
    inputPreference = $("liveInput").value;
    localStorage.setItem("kb-live-input", inputPreference);
    if (inputPreview) { stopInputPreview(); startInputPreview(); }
  });
  window.addEventListener("pagehide", stopInputPreview);
  navigator.mediaDevices?.addEventListener("devicechange", refreshInputs);

  async function startLive(kind, file) {
    if (live && !live.ended) return;
    $("liveErr").hidden = true;
    $("liveResult").innerHTML = "";
    $("liveResult").hidden = false;
    lastLive = null;
    const memberId = $("liveClaimed").value;
    const member = family.find((m) => m.id === memberId) || null;
    if (!$("liveHidden").checked && $("liveNum").value.replace(/\D/g, "").length < 6) {
      liveError("number_required"); $("liveNum").focus(); return;
    }
    stopInputPreview();
    let pcStream = null;
    if (kind === "pc") {
      $("pcAudioBtn").disabled = true;
      try { pcStream = await KBAudio.openPCAudio(); }
      catch (error) {
        liveError(error.message === "pc_audio_missing" ? "pc_audio_missing" : "pc_audio_unavailable");
        return;
      } finally { $("pcAudioBtn").disabled = false; }
      if (live && !live.ended) { pcStream.getTracks().forEach((track) => track.stop()); return; }
    }
    const deviceId = $("liveInput").value;
    const qs = new URLSearchParams({ member: memberId, scenario: $("liveScenario").value,
      number: $("liveHidden").checked ? "" : $("liveNum").value, hidden: $("liveHidden").checked ? "1" : "0",
      auto: $("liveAuto").checked ? "1" : "0", action: $("liveAction").value,
      bot: kind !== "pc" && profile === "bank" && $("liveBot").checked ? "1" : "0" });
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live?${qs}`, wsProtocols());
    ws.binaryType = "arraybuffer";
    live = { ws, updates: [], member, ended: false, botLines: [], sourceKind: kind, pcStream,
      signalDetected: false, number: $("liveHidden").checked ? "" : $("liveNum").value };
    renderBot();
    const src = new KBAudio.LiveSource({
      onFrame: (buf) => { if (live?.ws === ws && (kind === "file" || live.signalDetected) && ws.readyState === 1) ws.send(buf); },
      onLevel: (v, rms) => {
        if (live?.ws !== ws) return;
        $("liveMeter").style.width = Math.min(100, v * 500) + "%";
        if (kind !== "file" && !live.signalDetected && rms >= KBAudio.INPUT_SIGNAL_LEVEL) {
          live.signalDetected = true;
          inputMessage("liveInputState", "input_receiving");
        }
      },
      onTranscript: (text) => { $("liveTranscript").value = text; sendTx(); },
      onEnded: () => { if (live?.ws === ws) stopLive(); },
    });
    live.src = src;
    $("liveInputState").hidden = kind === "file";
    $("liveMeter").style.width = "0%";
    inputMessage("liveInputState", "input_waiting");
    ["liveVoicePill", "liveIdPill", "liveScriptPill"].forEach((id) => { $(id).className = "pill na"; $(id).textContent = "—"; });
    $("liveVoiceText").textContent = "";
    $("liveCallBadge").hidden = true;
    $("liveIdText").textContent = member ? t("live_id_waiting", { name: member.name }) : t("live_id_none");
    $("liveChips").innerHTML = ""; $("liveSpark").innerHTML = ""; $("liveTranscript").value = "";
    $("liveTimer").textContent = "0:00";
    $("hangup").hidden = true; $("liveStop").hidden = false;
    $("liveReplay").hidden = true; liveCh?.stop?.(); liveCh = null; $("liveChallenge").hidden = false;
    $("liveAskBox").hidden = true; clearInterval(askPoll); $("liveAsk").hidden = false; updateAskLabel();
    $("liveSetup").hidden = true; $("liveCall").hidden = false;
    setLight("idle", t("live_connecting"), "");
    ws.onmessage = (e) => { if (live?.ws === ws) onLiveMsg(JSON.parse(e.data)); };
    ws.onclose = () => { if (live?.ws === ws && !live.ended) endLiveUI(); };
    const opened = await new Promise((res) => {
      ws.onopen = () => res(true);
      ws.onerror = () => res(false);
      ws.addEventListener("close", () => res(false), { once: true });
    });
    if (live?.ws !== ws || live.ended || live.stopping) return;
    if (!opened) { liveError("stream_unavailable"); endLiveUI(); return; }
    try {
      if (kind === "mic") await src.startMic({ lang, transcribe: !deviceId && KBAudio.speechSupported, deviceId });
      else if (kind === "pc") await src.startStream(pcStream);
      else await src.startFile(file);
    } catch {
      liveError(kind === "mic" ? "input_unavailable" : kind === "pc" ? "pc_audio_unavailable" : "corrupt_audio");
      stopLive();
      return;
    }
    if (live?.ws !== ws || live.ended || live.stopping) return;
    live.t0 = Date.now();
    live.timer = setInterval(() => {
      const s = Math.floor((Date.now() - live.t0) / 1000);
      $("liveTimer").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }, 500);
  }

  let txTimer = null;
  function sendTx() {
    clearTimeout(txTimer);
    txTimer = setTimeout(() => {
      if (live?.ws.readyState === 1) live.ws.send(JSON.stringify({ type: "transcript", text: $("liveTranscript").value }));
      checkLiveReplay();
    }, 400);
  }

  function stopLive() {
    if (!live || live.ended || live.stopping) return;
    live.stopping = true;
    if (live.bot) window.speechSynthesis?.cancel();
    renderBot();
    live.src?.stop();
    clearInterval(live.timer);
    $("liveStop").hidden = true;
    if (live.ws.readyState === 1) live.ws.send(JSON.stringify({ type: "stop" }));
    else {
      live.ws.close();
      endLiveUI();
    }
  }


  // ------------------------------------------------------- replay check
  // A recording cannot say a phrase it has never heard.
  const replayMsg = (r, extra = {}) => t("replay_" + r, extra);

  function countdown(el, secs, onEnd) {
    const end = Date.now() + secs * 1000;
    const tick = () => {
      const left = Math.max(0, Math.ceil((end - Date.now()) / 1000));
      if (el.isConnected) el.textContent = t("replay_time_left", { s: left });
      if (left === 0) { clearInterval(iv); onEnd?.(); }
    };
    const iv = setInterval(tick, 500);
    tick();
    return () => clearInterval(iv);
  }

  async function startReplay(res) {
    const area = $("replayArea");
    $("replayStart").hidden = true;
    let ch;
    try {
      ch = await api("/api/challenge", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lang }) });
    } catch (e) { area.textContent = errText(e); return; }
    area.innerHTML = `
      <p class="hint">${esc(t("replay_say"))}</p>
      <div class="phrase">${esc(ch.phrase)}</div>
      <p class="num" id="replayTimer"></p>
      <div class="recbox"><button id="replayRec" class="rec"><span class="dot"></span><span>${esc(t("record"))}</span></button>
        <div class="recinfo"><div class="meter"><i id="replayMeter"></i></div><span id="replayTime" class="time">0.0 s</span></div></div>
      <input id="replayTx" placeholder="${esc(t("replay_type"))}" style="margin-top:10px" />
      <button class="primary" id="replayCheck">${esc(t("replay_check"))}</button>
      <p id="replayOut" class="context" hidden></p>`;
    const stopTimer = countdown($("replayTimer"), ch.expires_in);
    let clip = null, rec = null;
    $("replayRec").addEventListener("click", async () => {
      const btn = $("replayRec");
      if (!rec) {
        rec = new KBAudio.Recorder({
          onTick: (s) => ($("replayTime").textContent = s.toFixed(1) + " s"),
          onLevel: (v) => ($("replayMeter").style.width = Math.min(100, v * 160) + "%"),
          onTranscript: (text) => ($("replayTx").value = text),
        });
        try { await rec.start({ lang, transcribe: KBAudio.speechSupported }); } catch { rec = null; alert(t("err_mic")); return; }
        btn.classList.add("on"); btn.lastElementChild.textContent = t("stop");
      } else {
        const r = rec; rec = null;
        btn.classList.remove("on"); btn.lastElementChild.textContent = t("record");
        clip = await r.stop();
      }
    });
    $("replayCheck").addEventListener("click", async () => {
      const out = $("replayOut");
      const fd = new FormData();
      fd.append("transcript", $("replayTx").value);
      fd.append("claimed_member", res.claimed?.id || "");
      fd.append("demo_scenario", $("scenario").value);
      fd.append("final", "true");
      if (res.incident_id) fd.append("incident_id", res.incident_id);
      if (clip) fd.append("file", clip.blob, "answer.wav");
      $("replayCheck").disabled = true;
      try {
        const r = await api(`/api/challenge/${ch.id}/answer`, { method: "POST", body: fd });
        stopTimer();
        const heard = (r.phrase.heard || []).length;
        out.textContent = replayMsg(r.result, { name: res.claimed?.name || t("someone") }) +
          (r.phrase.words ? " " + t("replay_heard", { n: heard, of: r.phrase.words.length }) : "") +
          (r.simulated ? " · " + t("simulated_tag") : "");
        out.className = "context " + (r.result === "passed" ? "ok" : "err");
        loadStatus();
      } catch (e) { out.textContent = errText(e); out.className = "context err"; }
      out.hidden = false;
      $("replayCheck").disabled = false;
    });
  }

  // live call: the stream already checks the voice; we only need the words.
  let liveCh = null;
  async function startLiveReplay() {
    const box = $("liveReplay");
    const call = live;
    liveCh?.stop?.();
    try {
      const challenge = await api("/api/challenge", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang, guard_id: call?.bot?.id || "" }) });
      if (call !== live || call.ended) return;
      liveCh = challenge;
    } catch (e) { box.hidden = false; box.textContent = errText(e); return; }
    liveCh.txStart = $("liveTranscript").value.length;
    liveCh.done = false;
    box.hidden = false;
    box.className = "replay-box";
    box.innerHTML = `<p class="hint">${esc(t("replay_say"))}</p><div class="phrase">${esc(liveCh.phrase)}</div>
      <p class="num" id="liveReplayTimer"></p><p id="liveReplayOut" class="context">${esc(t("replay_waiting"))}</p>
      <button class="btn" id="liveReplayFinish">${esc(t("bot_check_phrase"))}</button>`;
    $("liveReplayFinish").onclick = () => checkLiveReplay(true);
    liveCh.stop = countdown($("liveReplayTimer"), liveCh.expires_in, () => {
      if (liveCh && !liveCh.done) checkLiveReplay(true);
    });
  }
  function showLiveReplay(result) {
    const out = $("liveReplayOut");
    if (!out) return;
    out.textContent = replayMsg(result, { name: live?.member?.name || t("someone") });
    out.className = "context " + (result === "passed" ? "ok" : result === "waiting" ? "" : "err");
    $("liveReplay").className = "replay-box " + (result === "passed" ? "good" : result === "waiting" ? "" : "bad");
  }
  async function checkLiveReplay(final = false) {
    if (!liveCh || liveCh.done) return;
    const challenge = liveCh;
    const said = $("liveTranscript").value.slice(challenge.txStart);
    if (!said.trim() && !final) return;
    try {
      const body = new FormData();
      body.append("transcript", said);
      body.append("final", String(final));
      const r = await api(`/api/challenge/${challenge.id}/answer`, { method: "POST", body });
      if (challenge !== liveCh || challenge.done) return;
      if (r.result !== "waiting") {
        challenge.done = true;
        challenge.stop?.();
        $("liveReplayFinish").disabled = true;
      }
      showLiveReplay(r.result === "used" ? "passed" : r.result);
    } catch {}
  }
  $("liveChallenge").addEventListener("click", startLiveReplay);


  // ------------------------------------------------- demo sample library
  // One tap: load a stored clip with a known story and run it. Demo mode only.
  const SAMPLES = [
    { id: "clone_as_a", clip: "scam_tts.wav", scenario: "cloned", number: "+919000000001", tx: { en: "Hello, this is Person A. I need to change my registered mobile number and send fifty thousand rupees right now, it is urgent.", hi: "नमस्ते, मैं Person A बोल रहा हूँ। मुझे अभी के अभी रजिस्टर्ड नंबर बदलना है और पचास हज़ार रुपये भेजने हैं, बहुत जल्दी है।", kn: "ನಮಸ್ಕಾರ, ನಾನು Person A. ನನ್ನ ನೋಂದಾಯಿತ ನಂಬರ್ ಈಗಲೇ ಬದಲಾಯಿಸಬೇಕು, ಐವತ್ತು ಸಾವಿರ ರೂಪಾಯಿ ಕಳುಹಿಸಬೇಕು, ತುಂಬಾ ತುರ್ತು." } },
    { id: "a_real", clip: "clip27.wav", scenario: "genuine", number: "+919000000001", tx: { en: "Hi, reached the hostel safely, will call you tonight after dinner", hi: "नमस्ते, मैं हॉस्टल ठीक से पहुँच गया, रात को खाने के बाद फ़ोन करूँगा।", kn: "ನಮಸ್ಕಾರ, ನಾನು ಹಾಸ್ಟೆಲ್ ಸುರಕ್ಷಿತವಾಗಿ ತಲುಪಿದೆ, ರಾತ್ರಿ ಊಟದ ನಂತರ ಕರೆ ಮಾಡುತ್ತೇನೆ." } },
    { id: "b_money", clip: "clip30.wav", scenario: "genuine", number: "+919000000002", tx: { en: "Hey it's me, please send 20000 on GPay quickly, I'll explain later", hi: "मैं ही हूँ, प्लीज़ जल्दी से GPay पर बीस हज़ार रुपये भेज दो, बाद में बताऊँगा।", kn: "ನಾನೇ, ದಯವಿಟ್ಟು ಬೇಗ GPay ನಲ್ಲಿ ಇಪ್ಪತ್ತು ಸಾವಿರ ರೂಪಾಯಿ ಕಳುಹಿಸು, ನಂತರ ಹೇಳುತ್ತೇನೆ." } },
    { id: "ai_scam", clip: "scam_tts.wav", scenario: "cloned", number: "+44 7700 900123", tx: { en: "Papa I had an accident, police stopped me, send fifty thousand rupees on UPI right now, do not tell mummy, this is my new number", hi: "पापा, एक्सीडेंट हो गया, पुलिस ने पकड़ लिया, अभी के अभी पचास हज़ार रुपये UPI पर भेजो, मम्मी को मत बताना, यह मेरा नया नंबर है।", kn: "ಅಪ್ಪ, ಅಪಘಾತ ಆಗಿದೆ, ಪೊಲೀಸ್ ಹಿಡಿದಿದ್ದಾರೆ, ಈಗಲೇ ಐವತ್ತು ಸಾವಿರ ರೂಪಾಯಿ UPI ಮೂಲಕ ಕಳುಹಿಸು, ಅಮ್ಮನಿಗೆ ಹೇಳಬೇಡ, ಇದು ನನ್ನ ಹೊಸ ನಂಬರ್." } },
    { id: "b_as_a", clip: "clip30.wav", scenario: "wrong_person", claim: "Person A", tx: { en: "It's me, my phone is broken, this is my new number, send money quickly", hi: "मैं ही हूँ, मेरा फ़ोन खराब हो गया, यह मेरा नया नंबर है, जल्दी पैसे भेजो।", kn: "ನಾನೇ, ನನ್ನ ಫೋನ್ ಹಾಳಾಗಿದೆ, ಇದು ನನ್ನ ಹೊಸ ನಂಬರ್, ಬೇಗ ಹಣ ಕಳುಹಿಸು." }, number: "+91 9000000000" },
    { id: "spliced", clip: "clip27.wav", scenario: "spliced", tx: { en: "" } },
  ];
  async function sampleFile(name) {
    const r = await authFetch("/api/demo-clip/" + encodeURIComponent(name));
    if (!r.ok) throw Object.assign(new Error("clip_missing"), { code: "clip_missing" });
    return new File([await r.blob()], name, { type: "audio/wav" });
  }
  function renderLiveSamples() {
    // Live mode: same stored clips, but checked for real (uses credits). The "spliced" sample only
    // exists as a demo scenario, so it is not offered live.
    const available = new Set(status.sample_clips || []);
    const list = SAMPLES.filter((sp) => available.has(sp.clip) && (status.mode === "demo" || sp.id !== "spliced"));
    $("liveSamples").hidden = !list.length;
    const buttons = list.map((sp) => `<button class="btn" data-sample="${sp.id}">${esc(t("sample_" + sp.id))}</button>`).join("");
    $("liveSamples").innerHTML = `<span class="hint" style="width:100%">${esc(t("samples_live"))}</span>` + buttons;
    $("liveSamples").querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", () => runLiveSample(b.dataset.sample)));
  }
  async function runLiveSample(id) {
    const sp = SAMPLES.find((x) => x.id === id);
    const file = await sampleFile(sp.clip).catch(() => null);
    if (!file || !file.size) { alert(t("err_corrupt_audio")); return; }
    const claimId = sp.claim ? (family.find((m) => m.name === sp.claim)?.id || "") : "";
    $("liveClaimed").value = claimId;
    $("liveNum").value = sp.number || ""; $("liveHidden").checked = !sp.number; $("liveNum").disabled = !sp.number; $("liveNum").dispatchEvent(new Event("input"));
    $("liveScenario").value = sp.scenario === "spliced" ? "genuine" : sp.scenario;
    $("liveAuto").checked = true;
    await startLive("file", file);
    const tx = sp.tx[lang] ?? sp.tx.en;
    if (tx) setTimeout(() => { $("liveTranscript").value = tx; sendTx(); }, 2500);
  }


  // ------------------------------------------------------- number memory
  function lastAlertText(a) {
    const when = new Date(a.at * 1000).toLocaleString();
    return a.claimed ? t("num_last_alert", { when, who: a.claimed }) : t("num_last_alert_noclaim", { when });
  }
  function badgeText(info) {
    const parts = [];
    let cls = "";
    if (info.status === "flagged") {
      cls = "bad";
      parts.push(t("nb_flagged", { n: info.alerts || 1 }));
      if (info.last_alert) parts.push(lastAlertText(info.last_alert));
    } else if (info.status === "known") { cls = "good"; parts.push(t("nb_known", { name: info.owner })); }
    else if (info.status === "trusted") { cls = "good"; parts.push(t("nb_trusted")); }
    else if (info.status === "seen") { parts.push(t("nb_seen", { n: info.calls })); }
    else if (info.status === "first_time") { cls = "warn"; parts.push(t("nb_first_time")); }
    else if (info.status === "hidden") { cls = "warn"; parts.push(t("nb_hidden")); }
    if (info.international) { cls = cls === "bad" ? "bad" : "warn"; parts.push(t("nb_international")); }
    return { cls, text: parts.join(" ") };
  }
  function showBadge(el, info) {
    if (!info || !info.status) { el.hidden = true; return; }
    const b = badgeText(info);
    el.className = "numbadge " + b.cls;
    el.textContent = b.text;
    el.hidden = !b.text;
  }
  function wireLookup(input, hiddenBox, badge) {
    let timer = null;
    const go = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const hidden = hiddenBox.checked;
        input.disabled = hidden;
        if (!hidden && input.value.replace(/\D/g, "").length < 6) { badge.hidden = true; return; }
        try { showBadge(badge, await api(`/api/numbers/lookup?${new URLSearchParams({ n: input.value, hidden })}`)); } catch {}
      }, 350);
    };
    input.addEventListener("input", go);
    hiddenBox.addEventListener("change", go);
  }
  wireLookup($("callerNum"), $("numHidden"), $("numBadge"));
  wireLookup($("liveNum"), $("liveHidden"), $("liveNumBadge"));

  const STATUS_CLS = { flagged: "bad", trusted: "good", seen: "na" };
  async function loadNumbers() {
    let rows = [], log = [];
    try { [rows, log] = await Promise.all([api("/api/numbers"), api("/api/history")]); } catch {}
    $("numList").innerHTML = rows.length ? rows.map((r) => `
      <li><div class="who">${esc(r.display || r.number)}<small>${esc(r.owner ? t("nb_known", { name: r.owner }) : "")}
        ${esc(t("num_stats", { calls: r.calls, alerts: r.alerts }))}${r.note ? " · " + esc(r.note) : ""}</small></div>
        <span class="pill ${r.owner ? "good" : STATUS_CLS[r.status] || "na"}">${esc(t("num_status_" + (r.owner && r.status !== "flagged" ? "known" : r.status)))}</span>
        ${r.status !== "flagged" ? `<button class="btn" data-n="${esc(r.display || r.number)}" data-s="flagged">${esc(t("num_flag"))}</button>` : ""}
        ${r.status !== "trusted" ? `<button class="btn" data-n="${esc(r.display || r.number)}" data-s="trusted">${esc(t("num_trust"))}</button>` : ""}
        ${r.status !== "seen" ? `<button class="btn" data-n="${esc(r.display || r.number)}" data-s="seen">${esc(t("num_clear"))}</button>` : ""}
      </li>`).join("") : `<li class="muted">${esc(t("nums_empty"))}</li>`;
    $("numList").querySelectorAll("[data-n]").forEach((b) => b.addEventListener("click", () => setNum(b.dataset.n, b.dataset.s)));
    const col = { high: "var(--high)", medium: "var(--med)", low: "var(--low)" };
    $("logList").innerHTML = log.map((x) => `<li><span class="lv" style="background:${col[x.level]}"></span>
      <span>${new Date(x.at * 1000).toLocaleString()} · ${esc(x.kind === "live" ? t("tab_live") : t("tab_check"))} · ${esc(x.number || t("nb_hidden_short"))} ·
      ${esc(x.claimed || t("unknown_person"))} · <b>${esc(t("lvl_" + x.level))}</b>${x.simulated ? " · " + esc(t("simulated_tag")) : ""}
      ${x.bot_events?.some((e) => e.state === "paused") ? " · " + esc(t("bot_pause_logged")) : ""}</span>
      <a href="/api/incidents/${x.id}/report" data-dl target="_blank" rel="noopener">${esc(t("report_short"))}</a></li>`).join("") || `<li class="muted">—</li>`;
  }
  async function setNum(n, st) {
    try {
      await api("/api/numbers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ number: n, status: st, note: "" }) });
    } catch (e) { alert(errText(e)); }
    loadNumbers();
  }
  $("numFlag").addEventListener("click", () => { if ($("numAdd").value) setNum($("numAdd").value, "flagged"); });
  $("numTrust").addEventListener("click", () => { if ($("numAdd").value) setNum($("numAdd").value, "trusted"); });
  document.querySelector('[data-tab="numbers"]').addEventListener("click", loadNumbers);


  // ------------------------------------------------------- incoming calls
  // The phone system posts the caller's number to /api/calls/incoming; we pick it up
  // here and fill everything in, so nobody types or ticks anything.
  let lastCallId = 0, currentCall = null;

  function fillNumber(inputId, hiddenId, badgeId, call) {
    $(inputId).value = call.hidden ? "" : call.number;
    $(hiddenId).checked = call.hidden;
    $(inputId).disabled = call.hidden;
    showBadge($(badgeId), call.info);
  }
  function showIncoming(call) {
    currentCall = call;
    const who = call.member ? ` (${call.member.name})` : "";
    $("incTitle").textContent = call.hidden ? t("inc_hidden") : t("inc_from", { num: call.number }) + who;
    const b = badgeText(call.info || {});
    $("incInfo").className = "numbadge " + b.cls;
    $("incInfo").textContent = b.text;
    $("incoming").className = "incoming " + b.cls;
    $("incoming").hidden = false;
    // fill both tabs straight away
    fillNumber("liveNum", "liveHidden", "liveNumBadge", call);
    fillNumber("callerNum", "numHidden", "numBadge", call);
    if (call.member) { $("liveClaimed").value = ""; }
    navigator.vibrate?.([200, 100, 200]);
  }
  async function pollCalls() {
    try {
      const calls = await api(`/api/calls/pending?after=${lastCallId}`);
      if (calls.length) { lastCallId = calls[calls.length - 1].id; showIncoming(calls[calls.length - 1]); }
    } catch {}
  }
  async function primeCalls() {
    try { const c = await api("/api/calls/pending?after=0"); if (c.length) lastCallId = c[c.length - 1].id; } catch {}
    setInterval(pollCalls, 2000);
  }
  const gotoTab = (name) => document.querySelector(`.tabs [data-tab="${name}"]`).click();
  $("incLive").addEventListener("click", () => { $("incoming").hidden = true; gotoTab("live"); });
  $("incCheck").addEventListener("click", () => { $("incoming").hidden = true; gotoTab("check"); });
  $("incDismiss").addEventListener("click", () => { $("incoming").hidden = true; });

  // simulate exactly what a phone system would send
  function renderSim() {
    renderOutbound();
    const opts = family.flatMap((m) => (m.phones || []).map((p) => [p, `${m.name} · ${p}`]));
    opts.push(["+91 90000 12345", t("sim_unknown")], ["+44 7700 900123", t("sim_intl")], ["anonymous", t("sim_hidden")]);
    const cur = $("simFrom").value;
    $("simFrom").innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    if (cur) $("simFrom").value = cur;
  }
  $("simBtn").addEventListener("click", async () => {
    try {
      await api("/api/calls/incoming", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from: $("simFrom").value, source: "simulator" }) });
      pollCalls();
    } catch (e) { alert(errText(e)); }
  });

  // a dialer / automation can open the app with ?from=+91... and get the same flow
  const fromParam = new URLSearchParams(location.search).get("from");
  if (fromParam !== null) {
    api("/api/calls/incoming", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from: fromParam, source: "link" }) }).then(showIncoming).catch(() => {});
    history.replaceState(null, "", location.pathname);
  }


  // ------------------------------------------------ ask the real customer
  // Not "does this voice sound like them?" but "is the real person actually on this call?"
  // The claimed customer's registered phone gets a prompt; their answer shows here live.
  let askPoll = null;
  async function askCustomer(member, number, box, onResult, guardId = "") {
    clearInterval(askPoll);
    box.hidden = false;
    box.className = "ask-box";
    if (!member) { box.textContent = t("ask_need_member"); return; }
    let r;
    try {
      r = await api("/api/confirm", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ member_id: member.id, caller_number: number || "", lang, guard_id: guardId }) });
    } catch (e) { box.textContent = errText(e); return; }
    box.innerHTML = `<div class="ask-wait"><span class="spin"></span><span>${esc(t("ask_sent", { name: member.name, to: r.sent_to }))}</span></div>
      <p class="hint">${esc(t("ask_why"))}</p>
      <a class="btn" target="kb-phone" href="${r.device_url}${status.mode === "demo" ? "?clip=clip27.wav" : ""}">${esc(t("ask_open"))}</a>`;
    const show = (st, res) => {
      clearInterval(askPoll);
      const sim = res?.similarity != null ? Number(res.similarity).toFixed(2) : "—";
      box.className = "ask-box " + (st === "confirmed" ? "good" : st === "expired" ? "warn" : "bad");
      box.innerHTML = `<b>${esc(t("ask_" + st, { name: member.name, sim }))}</b>${res?.simulated ? ` <span class="tag">${esc(t("simulated_tag"))}</span>` : ""}`;
      onResult?.(st);
    };
    askPoll = setInterval(async () => {
      try {
        const s = await api(`/api/confirm/status/${r.id}`);
        if (s.status !== "pending") show(s.status, s.result);
      } catch {}
    }, 1200);
  }

  function updateAskLabel() {
    const m = live?.member;
    $("liveAsk").textContent = m ? t("ask_btn", { name: m.name }) : t("ask_btn_generic");
  }
  $("liveAsk").addEventListener("click", () => {
    const m = live?.member ? (family.find((f) => f.id === live.member.id) || live.member) : null;
    askCustomer(m, live?.number, $("liveAskBox"), (st) => {
      if (st === "denied" || st === "failed") {
        setLight("high", t("ask_hold_title"), t("ask_" + st, { name: m.name, sim: "" }));
        $("hangup").hidden = false;
        navigator.vibrate?.([300, 150, 300]);
      } else if (st === "confirmed" && !$("hangup").hidden) {
        $("liveSub").textContent = t("ask_confirmed_note");
      }
    }, live?.bot?.id);
  });


  // --------------------------------------------- two-way trust (outbound)
  // The real agent declares "I'm calling this customer now"; the customer's bank app
  // can then check the call is genuine and verify the agent's voice with Eidos.
  let obPoll = null;
  function renderOutbound() {
    const agents = family.filter((m) => m.agent);
    $("obAgent").innerHTML = agents.length ? agents.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("")
      : `<option value="">${esc(t("ob_no_agents"))}</option>`;
    $("obCustomer").innerHTML = family.filter((m) => !m.agent).map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
  }
  $("obStart").addEventListener("click", async () => {
    clearInterval(obPoll);
    const box = $("obBox");
    box.hidden = false; box.className = "ask-box";
    if (!$("obAgent").value) { box.textContent = t("ob_no_agents"); return; }
    let s;
    try {
      s = await api("/api/outbound", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: $("obAgent").value, customer_id: $("obCustomer").value, lang }) });
    } catch (e) { box.textContent = errText(e); return; }
    const agentClip = status.mode === "demo" ? "&clip=clip30.wav" : "";
    box.innerHTML = `<div class="ask-wait"><span class="spin"></span><span>${esc(t("ob_active", { agent: s.agent_name, customer: s.customer_name }))}</span></div>
      <p class="hint">${esc(t("ob_explain"))}</p>
      <div class="row"><a class="btn" target="kb-phone" href="${s.customer_url}&lang=${lang}${agentClip}">${esc(t("ob_open_customer"))}</a>
      <button class="btn" id="obEnd">${esc(t("ob_end"))}</button></div>`;
    $("obEnd").onclick = async () => { await api(`/api/outbound/${s.id}/end`, { method: "POST" }).catch(() => {}); clearInterval(obPoll); box.className = "ask-box"; box.textContent = t("ob_ended"); };
    obPoll = setInterval(async () => {
      try {
        const st = await api(`/api/outbound/${s.id}`);
        if (st.result) {
          clearInterval(obPoll);
          const ok = st.result.verified;
          box.className = "ask-box " + (ok ? "good" : "bad");
          box.innerHTML = `<b>${esc(t(ok ? "ob_verified" : "ob_failed", { customer: st.customer_name }))}</b>${st.result.simulated ? ` <span class="tag">${esc(t("simulated_tag"))}</span>` : ""}
            <div class="row" style="margin-top:8px"><button class="btn" id="obEnd2">${esc(t("ob_end"))}</button></div>`;
          $("obEnd2").onclick = async () => { await api(`/api/outbound/${s.id}/end`, { method: "POST" }).catch(() => {}); box.className = "ask-box"; box.textContent = t("ob_ended"); };
        } else if (st.status === "ended") { clearInterval(obPoll); }
      } catch {}
    }, 1500);
  });

  // ------------------------------------------------------- accuracy page
  async function loadAccuracy() {
    let d;
    try { d = await api("/api/benchmark"); } catch { return; }
    $("accRun").textContent = d.mode === "demo" ? t("acc_run_demo") : t("acc_run");
    const plan = d.readiness;
    $("accRun").disabled = !plan?.total || plan.ready !== plan.total;
    const readiness = `<p class="hint">${esc(t("acc_readiness", { ready: plan?.ready ?? 0, total: plan?.total ?? 0 }))}</p>`;
    const r = d.results;
    if (!r) { $("accBox").innerHTML = readiness; return; }
    const mark = (ok) => ok == null ? "—" : ok ? "✅" : "❌";
    const metrics = r.metrics?.overall;
    const missing = (metrics?.voice_missing || 0) + (metrics?.identity_missing || 0);
    $("accBox").innerHTML = `
      ${readiness}
      <p class="acc-sum">${esc(t("acc_summary", { c: r.correct, n: r.total }))}${r.simulated ? ` <span class="tag">${esc(t("simulated_tag"))}</span>` : ""}</p>
      ${missing ? `<p class="hint">${esc(t("acc_missing", { n: missing }))}</p>` : ""}
      <div class="acc-wrap"><table class="acc">
        <tr><th>${esc(t("acc_case"))}</th><th>${esc(t("acc_condition"))}</th><th>${esc(t("acc_voice"))}</th><th>${esc(t("acc_identity"))}</th></tr>
        ${r.rows.map((x) => `<tr>
          <td>${esc(x.note || x.clip)}</td>
          <td>${esc(x.condition || "clean")}</td>
          <td>${mark(x.voice_ok)} ${esc(x.voice || x.error || "—")}${x.score != null ? ` <small>${Number(x.score).toFixed(3)}</small>` : ""}</td>
          <td>${x.decision ? `${mark(x.identity_ok)} ${esc(x.decision.replace("_", " "))} <small>${Number(x.similarity).toFixed(2)}</small>` : "—"}</td>
        </tr>`).join("")}
      </table></div>
      <p class="hint">${esc(t("acc_when", { when: new Date(r.at * 1000).toLocaleString() }))}</p>`;
  }
  $("accRun").addEventListener("click", async () => {
    if (status.mode !== "demo" && !confirm(t("acc_confirm"))) return;
    $("accRun").disabled = true; $("accBox").innerHTML = `<div class="ask-wait"><span class="spin"></span>${esc(t("acc_running"))}</div>`;
    try { await api("/api/benchmark/run", { method: "POST" }); } catch (e) { alert(errText(e)); }
    $("accRun").disabled = false;
    loadAccuracy(); loadStatus();
  });
  document.querySelector('[data-tab="numbers"]').addEventListener("click", loadAccuracy);

  $("liveMicBtn").addEventListener("click", () => startLive("mic"));
  $("pcAudioBtn").addEventListener("click", () => startLive("pc"));
  if (!navigator.mediaDevices?.getDisplayMedia) {
    $("pcAudioBtn").hidden = true;
    $("pcAudioHint").hidden = true;
  }
  $("liveFile").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) startLive("file", f); });
  $("liveStop").addEventListener("click", stopLive);
  $("liveTranscript").addEventListener("input", sendTx);

  // ---------------------------------------------------------------- family
  function renderFamily() {
    renderSim();
    const ul = $("famList");
    if (!family.length) { ul.innerHTML = `<li class="muted">${esc(t("fam_empty"))}</li>`; return; }
    ul.innerHTML = family.map((m) => `
      <li><div class="av">${esc(m.name[0]?.toUpperCase() || "?")}</div>
        <div class="who">${esc(m.name)}<small>${esc(m.relation || "")} · ${esc(t("fam_samples", { n: m.samples }))}${(m.phones || []).length ? " · 📞 " + esc(m.phones.join(", ")) : ""}</small></div>
        <button class="btn" data-phone="${esc(m.id)}">📞</button>
        ${!m.agent ? `<button class="btn" data-email="${esc(m.id)}">${esc(t(m.email ? "customer_email_edit" : "customer_email_add"))}</button>` : ""}
        ${m.samples < 5 ? `<button class="btn" data-add="${esc(m.id)}">${esc(t("fam_add_sample"))}</button>` : ""}
        <button class="btn bank-only" data-staff="${esc(m.id)}">${esc(m.agent ? t("staff_on") : t("staff_off"))}</button>
        <button class="btn" data-del="${esc(m.id)}">${esc(t("fam_remove"))}</button></li>`).join("");
    ul.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => {
      const m = family.find((x) => x.id === b.dataset.add);
      $("famName").value = m.name; $("famRel").value = m.relation; $("famTarget").value = m.id;
      $("famName").scrollIntoView({ behavior: "smooth" });
    }));
    ul.querySelectorAll("[data-staff]").forEach((b) => b.addEventListener("click", async () => {
      const m = family.find((x) => x.id === b.dataset.staff);
      try {
        await api(`/api/family/${m.id}/agent`, { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent: !m.agent }) });
      } catch (e) { alert(errText(e)); }
      loadFamily();
    }));
    ul.querySelectorAll("[data-phone]").forEach((b) => b.addEventListener("click", async () => {
      const m = family.find((x) => x.id === b.dataset.phone);
      const v = prompt(t("fam_phone_prompt", { name: m.name }), (m.phones || []).join(", "));
      if (v === null) return;
      try {
        await api(`/api/family/${m.id}/phones`, { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phones: v.split(",").map((x) => x.trim()).filter(Boolean) }) });
      } catch (e) { alert(errText(e)); }
      loadFamily();
    }));
    ul.querySelectorAll("[data-email]").forEach((b) => b.addEventListener("click", async () => {
      const member = family.find((m) => m.id === b.dataset.email);
      const email = prompt(t("customer_email_prompt"), member.email || "");
      if (email === null) return;
      try {
        await api(`/api/family/${member.id}/email`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }),
        });
        await loadFamily();
      } catch (e) { alert(errText(e)); }
    }));
    ul.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      const m = family.find((x) => x.id === b.dataset.del);
      if (!confirm(t("fam_confirm_remove", { name: m.name }))) return;
      try { await api("/api/family/" + m.id, { method: "DELETE" }); } catch (e) { alert(errText(e)); }
      loadFamily(); loadStatus();
    }));
  }

  recorderUI($("famRecBtn"), $("famTime"), $("famMeter"),
    (clip) => { famClip = clip; showClip($("famClip"), clip); },
    () => ({ lang, transcribe: false }), null);
  $("famFile").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    famClip = { blob: f, name: f.name, seconds: await KBAudio.durationOf(f) };
    showClip($("famClip"), famClip);
  });
  $("famName").addEventListener("input", () => ($("famTarget").value = ""));

  $("famSave").addEventListener("click", async () => {
    const msg = $("famMsg");
    const showMsg = (txt, ok) => { msg.textContent = txt; msg.className = "msg " + (ok ? "ok" : "err"); msg.hidden = false; };
    const name = $("famName").value.trim();
    if (!name) { $("famName").focus(); return; }
    if (!famClip) return showMsg(t("err_no_audio"), false);
    if (famClip.seconds && famClip.seconds < 15) return showMsg(t("fam_need_15"), false);
    const fd = new FormData();
    fd.append("file", famClip.blob, famClip.name);
    fd.append("name", name);
    fd.append("relation", $("famRel").value.trim());
    fd.append("phone", $("famPhone").value.trim());
    fd.append("member_id", $("famTarget").value);
    const btn = $("famSave");
    btn.disabled = true;
    try {
      await api("/api/family/enroll", { method: "POST", body: fd });
      showMsg(t("fam_saved", { name }), true);
      famClip = null; $("famClip").hidden = true;
      $("famName").value = $("famRel").value = $("famTarget").value = $("famPhone").value = "";
      loadFamily(); loadStatus();
    } catch (e) { showMsg(errText(e), false); }
    finally { btn.disabled = false; }
  });

  // ------------------------------------------------------------- transaction review
  let pendingTx = null, txPoll = null, txBusy = false, qrBlobUrl = "", qrTransaction = "";
  $("txLang").value = ["en", "hi", "kn"].includes(lang) ? lang : "en";

  function renderTxCustomers() {
    const select = $("txCustomer");
    const chosen = pendingTx?.member_id || select.value;
    select.replaceChildren(new Option(t("tx_select"), ""),
      ...family.filter((member) => !member.agent).map((member) => new Option(member.name, member.id)));
    select.value = chosen;
    select.disabled = Boolean(pendingTx);
  }

  function renderTx() {
    const tx = pendingTx;
    $("txCard").hidden = !tx;
    $("txReset").hidden = !tx;
    $("txSubmit").disabled = txBusy;
    $("txSubmit").textContent = t(tx ? "tx_update" : "tx_hold");
    if (!tx) { hideTxQr(); return; }
    const amount = new Intl.NumberFormat({ en: "en-IN", hi: "hi-IN", kn: "kn-IN" }[lang],
      { style: "currency", currency: "INR" }).format(tx.amount_minor / 100);
    $("txExact").textContent = t("tx_summary", { amount, merchant: tx.merchant });
    $("txState").textContent = t("tx_" + tx.status);
    $("txState").className = "pill " + ({ held: "warn", approved: "good", denied: "bad" }[tx.status]);
    const detail = tx.revision > 1 && tx.verification === "pending" ? "tx_fresh" : "tx_result_" + tx.verification;
    $("txDetails").textContent = t(detail) + (tx.simulated_checks ? " · " + t("simulated_tag") : "");
    $("txEvidence").hidden = !tx.result;
    if (tx.result) {
      const signal = (value) => value ? t("tx_signal_" + value) : "—";
      $("txEvidence").textContent = t("tx_evidence", {
        voice: signal(tx.result.voice), match: signal(tx.result.decision), phrase: signal(tx.result.phrase),
      });
    }
    $("txLinkRow").hidden = !tx.device_url;
    if (!tx.device_url || qrTransaction !== tx.id + ":" + tx.revision) hideTxQr();
    $("txSms").disabled = !tx.sms_available || txBusy || ["sending", "accepted", "sms_unknown"].includes(tx.sms);
    $("txSmsStatus").hidden = !tx.device_url || (!tx.sms && tx.sms_available);
    $("txSmsStatus").textContent = t("tx_delivery_" + (tx.sms || "sms_unconfigured"));
    for (const id of ["txOpen", "txCopy", "txShowQr", "txSms"]) $(id).hidden = tx.otp_required;
    $("txEmail").hidden = !tx.otp_required;
    $("txEmail").disabled = !tx.email_available || txBusy || Boolean(tx.email_delivery);
    $("txOtp").hidden = !tx.otp_required;
    $("txOtp").disabled = !tx.otp_available || txBusy || Boolean(tx.otp);
    $("txOtpStatus").hidden = !tx.otp_required || !tx.device_url;
    $("txOtpStatus").textContent = t("tx_otp_" + (tx.otp || (tx.otp_available ? "ready" : "unavailable")));
    if (tx.otp_required) {
      $("txSmsStatus").hidden = !tx.device_url;
      $("txSmsStatus").textContent = t("tx_email_" + (tx.otp === "verified" ? "otp_verified" :
        tx.email_delivery || (tx.email_available ? "ready" : "unavailable")));
    }
    $("txReport").hidden = tx.device_reported || tx.status !== "held";
    $("txReport").disabled = txBusy;
    if (tx.device_url) $("txOpen").href = tx.device_url;
    else $("txOpen").removeAttribute("href");
  }

  function showTxError(message) {
    $("txError").textContent = message || "";
    $("txError").hidden = !message;
  }

  function pollTx() {
    clearInterval(txPoll);
    txPoll = setInterval(async () => {
      const before = pendingTx;
      if (!before || txBusy) return;
      try {
        const next = await api("/api/transactions/" + before.id);
        if (pendingTx !== before || txBusy) return;
        pendingTx = next;
        renderTx();
        if (!["pending", "checking"].includes(next.verification)) clearInterval(txPoll);
      } catch (e) {
        if (pendingTx === before) showTxError(errText(e));
      }
    }, 1200);
  }

  $("txForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (txBusy) return;
    const raw = $("txAmount").value.trim();
    if (!/^\d{1,7}(\.\d{1,2})?$/.test(raw)) { showTxError(t("tx_invalid_amount")); return; }
    const [rupees, fraction = ""] = raw.split(".");
    const amount = Number(rupees) * 100 + Number(fraction.padEnd(2, "0"));
    const merchant = $("txMerchant").value.trim();
    if (amount < 1 || amount > 100_000_000) { showTxError(t("tx_invalid_amount")); return; }
    if (!merchant) { showTxError(t("tx_merchant_required")); return; }
    const saved = pendingTx;
    txBusy = true;
    showTxError("");
    renderTx();
    try {
      const body = {
        amount_minor: amount, merchant, lang: $("txLang").value,
        ...(saved ? { revision: saved.revision } : { member_id: $("txCustomer").value }),
      };
      pendingTx = await api(saved ? "/api/transactions/" + saved.id : "/api/transactions", {
        method: saved ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      renderTxCustomers();
      pollTx();
    } catch (e) { showTxError(errText(e)); }
    finally { txBusy = false; renderTx(); }
  });

  $("txReset").addEventListener("click", () => {
    if (txBusy) return;
    clearInterval(txPoll);
    pendingTx = null;
    $("txForm").reset();
    $("txLang").value = lang;
    showTxError("");
    renderTxCustomers();
    renderTx();
  });

  $("txCopy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("txOpen").href);
      $("txCopy").textContent = t("tx_copied");
      setTimeout(() => { $("txCopy").textContent = t("tx_copy"); }, 1400);
    } catch { showTxError(t("tx_copy_error")); }
  });

  function hideTxQr() {
    if (qrBlobUrl) URL.revokeObjectURL(qrBlobUrl);
    qrBlobUrl = qrTransaction = "";
    $("txQrImage").removeAttribute("src");
    $("txQr").hidden = true;
    $("txShowQr").setAttribute("aria-expanded", "false");
  }

  $("txShowQr").addEventListener("click", async () => {
    if (!$("txQr").hidden) { hideTxQr(); return; }
    const tx = pendingTx;
    if (!tx?.device_url) return;
    $("txShowQr").disabled = true;
    try {
      const response = await authFetch(`/api/transactions/${tx.id}/qr?revision=${tx.revision}`);
      if (!response.ok) throw new Error();
      const image = await response.blob();
      if (pendingTx?.device_url !== tx.device_url) return;
      qrBlobUrl = URL.createObjectURL(image);
      qrTransaction = tx.id + ":" + tx.revision;
      $("txQrImage").src = qrBlobUrl;
      $("txQrImage").alt = t("tx_qr");
      $("txQrHelp").textContent = t(tx.device_url.startsWith("/") ? "tx_qr_local" : "tx_qr_help");
      $("txQr").hidden = false;
      $("txShowQr").setAttribute("aria-expanded", "true");
    } catch { showTxError(t("err_generic")); }
    finally { $("txShowQr").disabled = false; }
  });

  async function txAction(path) {
    if (!pendingTx || txBusy) return;
    txBusy = true;
    showTxError("");
    renderTx();
    try {
      pendingTx = await api("/api/transactions/" + pendingTx.id + path, { method: "POST" });
      pollTx();
    } catch (e) { showTxError(errText(e)); }
    finally { txBusy = false; renderTx(); }
  }

  $("txSms").addEventListener("click", () => txAction("/sms?revision=" + pendingTx?.revision));
  $("txEmail").addEventListener("click", () => txAction("/email?revision=" + pendingTx?.revision));
  $("txOtp").addEventListener("click", () => txAction("/otp?revision=" + pendingTx?.revision));
  $("txReport").addEventListener("click", () => {
    if (window.confirm(t("tx_report_confirm"))) txAction("/report-device");
  });

  function openPayment(action) {
    document.querySelector('[data-tab="transactions"]').click();
    const selected = live?.member?.id || $("liveClaimed").value;
    if (!pendingTx && [...$("txCustomer").options].some((option) => option.value === selected)) {
      $("txCustomer").value = selected;
    }
    if (!action) { $("txAmount").focus(); return; }
    if (!pendingTx || !selected || pendingTx.member_id !== selected || !pendingTx.device_url) {
      showTxError(t("tx_delivery_review_first"));
      $("txAmount").focus();
      return;
    }
    const button = $(action === "otp" ? "txOtp" : "txEmail");
    if (button.hidden || button.disabled) {
      $("txCard").scrollIntoView({ block: "nearest" });
      return;
    }
    button.click();
  }

  $("liveHold").addEventListener("click", () => openPayment());
  $("liveSendOtp").addEventListener("click", () => openPayment("otp"));
  $("liveSendEmail").addEventListener("click", () => openPayment("email"));

  $("txLive").addEventListener("click", () => {
    if (!live || live.ended) {
      $("liveClaimed").value = pendingTx?.member_id || "";
      $("liveAction").value = "payment";
    }
    document.querySelector('[data-tab="live"]').click();
  });

  if (!KBAudio.speechSupported) $("liveTx").closest("label").hidden = true;

  // ------------------------------------------------- PWA: install + "Share to this app"

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  // A voice note shared from WhatsApp lands here via the service worker (see sw.js).
  async function loadSharedClip() {
    const q = new URLSearchParams(location.search);
    if (!q.has("shared")) return;
    history.replaceState(null, "", "/");
    if (q.get("shared") !== "1" || !window.caches) return;
    const cache = await caches.open("kb-share");
    const res = await cache.match("/shared-clip");
    if (!res) return;
    const blob = await res.blob();
    await cache.delete("/shared-clip");
    checkClip = { blob, name: decodeURIComponent(res.headers.get("X-Name") || "shared-voice-note"), seconds: await KBAudio.durationOf(blob) };
    showClip($("clipReady"), checkClip);
    document.querySelector('.tabs button[data-tab="check"]')?.click();
  }

  applyLang();
  refreshInputs();
  loadStatus();
  loadFamily();
  primeCalls();
  loadSharedClip().catch(() => {});
})();
