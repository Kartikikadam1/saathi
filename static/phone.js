/* Saathi elder phone — browser "phone call" with speech-to-text and text-to-speech (Web Speech API),
   with typing and quick replies as fallbacks. */
(() => {
  const pid = new URLSearchParams(location.search).get("patient") || "P-001";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const S = { lang: "en", session: null, myCall: null, view: "idle", soundOn: false, speaking: false, listening: false,
              rec: null, callStart: null, endedShownFor: null, sending: false, langChosen: false };

  const CHIPS = {
    en: ["Yes, I took them", "No, I forgot", "I took only the BP tablet", "I'm feeling dizzy", "Should I double the dose?", "I'm fine, thank you"],
    hi: ["हाँ, मैंने दवा ले ली", "नहीं, भूल गई", "सिर्फ़ बीपी की गोली ली", "मुझे चक्कर आ रहा है", "क्या मैं दवा दोगुनी कर लूँ?", "मैं ठीक हूँ"],
  };

  async function api(path, body) {
    const res = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  }

  function setView(v) {
    S.view = v;
    ["idle", "ring", "call", "ended"].forEach((x) => $("v-" + x).classList.toggle("active", x === v));
    if (v !== "ring") stopRing();
  }

  // ------------------------------------------------------------- clock
  function tickClock() {
    const now = new Date();
    const t = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    $("clock").textContent = t.replace(/\s?[AP]M$/i, "");
    $("sb-time").textContent = t;
    $("date").textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
    if (S.callStart && S.view === "call") {
      const s = Math.floor((Date.now() - S.callStart) / 1000);
      $("call-timer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }
  }
  setInterval(tickClock, 1000); tickClock();

  // ------------------------------------------------------------- sound: ringtone + TTS
  let actx = null, ringTimer = null;
  $("btn-sound").addEventListener("click", () => {
    S.soundOn = true;
    try { actx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { /* no audio */ }
    if ("speechSynthesis" in window) { const u = new SpeechSynthesisUtterance(" "); u.volume = 0; speechSynthesis.speak(u); }
    $("sound-gate").classList.add("hidden");
  });
  function beep(freq, start, dur) {
    const o = actx.createOscillator(), g = actx.createGain();
    o.frequency.value = freq; o.connect(g); g.connect(actx.destination);
    g.gain.setValueAtTime(0.0001, start); g.gain.exponentialRampToValueAtTime(0.18, start + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, start + dur); o.start(start); o.stop(start + dur + 0.05);
  }
  function startRing() {
    if (ringTimer || !S.soundOn || !actx) return;
    const ring = () => { const t = actx.currentTime; beep(440, t, 0.4); beep(480, t, 0.4); beep(440, t + 0.6, 0.4); beep(480, t + 0.6, 0.4); };
    ring(); ringTimer = setInterval(ring, 2500);
  }
  function stopRing() { clearInterval(ringTimer); ringTimer = null; }

  function pickVoice(lang) {
    const voices = speechSynthesis.getVoices();
    const want = lang === "hi" ? ["hi-IN", "hi"] : ["en-IN", "en-GB", "en-US", "en"];
    for (const w of want) { const v = voices.find((x) => x.lang && x.lang.replace("_", "-").startsWith(w)); if (v) return v; }
    return null;
  }
  function speak(text, lang) {
    return new Promise((resolve) => {
      if (!S.soundOn || !("speechSynthesis" in window) || !text) return resolve();
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = lang === "hi" ? "hi-IN" : "en-IN";
      const v = pickVoice(lang); if (v) u.voice = v;
      u.rate = 0.93;
      S.speaking = true;
      const done = () => { S.speaking = false; resolve(); };
      u.onend = done; u.onerror = done;
      speechSynthesis.speak(u);
      setTimeout(() => { if (S.speaking && !speechSynthesis.speaking) done(); }, 1500); // some browsers never fire onend
    });
  }

  // ------------------------------------------------------------- speech recognition
  function listen() {
    if (!SR) { $("heard").textContent = "Voice input needs Chrome or Edge — please type your answer."; return; }
    if (S.listening || S.speaking || S.sending) return;
    const rec = new SR();
    rec.lang = S.lang === "hi" ? "hi-IN" : "en-IN";
    rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    let finalText = "";
    rec.onstart = () => { S.listening = true; $("btn-mic").classList.add("listening"); $("heard").textContent = S.lang === "hi" ? "सुन रही हूँ…" : "Listening…"; };
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
      }
      $("heard").textContent = finalText || interim;
    };
    rec.onerror = (e) => { $("heard").textContent = e.error === "no-speech" ? "I didn't hear anything. Tap the microphone to try again." : `Microphone: ${e.error}. You can type instead.`; };
    rec.onend = () => {
      S.listening = false; $("btn-mic").classList.remove("listening");
      if (finalText.trim()) sendReply(finalText.trim());
    };
    S.rec = rec;
    try { rec.start(); } catch (e) { S.listening = false; }
  }
  $("btn-mic").addEventListener("click", () => {
    if (S.listening && S.rec) { S.rec.stop(); return; }
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    S.speaking = false;
    listen();
  });

  // ------------------------------------------------------------- call actions
  document.querySelectorAll(".langs button").forEach((b) => b.addEventListener("click", () => {
    S.lang = b.dataset.lang; S.langChosen = true; renderLangButtons(); renderChips();
  }));
  function renderLangButtons() { document.querySelectorAll(".langs button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === S.lang))); }
  function renderChips() {
    $("chips").innerHTML = CHIPS[S.lang].map((c) => `<button type="button">${esc(c)}</button>`).join("");
  }
  $("chips").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) sendReply(b.textContent); });
  $("type-form").addEventListener("submit", (e) => {
    e.preventDefault(); const v = $("type-input").value.trim(); if (v) { $("type-input").value = ""; sendReply(v); }
  });

  $("btn-answer").addEventListener("click", async () => {
    if (!S.session) return;
    try {
      const r = await api(`/api/phone/sessions/${S.session.id}/answer`, { lang: S.lang });
      S.myCall = S.session.id; S.callStart = Date.now(); S.endedShownFor = null;
      openCall(); renderTurns([{ speaker: "agent", text: r.agent.text, translation_en: r.agent.text_en }]);
      await speak(r.agent.text, r.agent.lang);
      if (S.view === "call") listen();
    } catch (err) { $("ring-left").textContent = err.message; }
  });
  $("btn-decline").addEventListener("click", async () => {
    if (!S.session) return;
    await api(`/api/phone/sessions/${S.session.id}/decline`, {}).catch(() => {});
    setView("idle");
  });
  $("btn-hang").addEventListener("click", async () => {
    if (!S.myCall) return;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (S.rec) try { S.rec.abort(); } catch (e) { /* ignore */ }
    await api(`/api/phone/sessions/${S.myCall}/hangup`, {}).catch(() => {});
    showEnded(S.myCall, "You ended the call.");
  });
  $("btn-back").addEventListener("click", () => { S.myCall = null; setView("idle"); });

  async function sendReply(text) {
    if (!S.myCall || S.sending) return;
    S.sending = true;
    $("heard").textContent = `“${text}”`;
    appendBubble({ speaker: "patient", text });
    try {
      const r = await api(`/api/phone/sessions/${S.myCall}/reply`, { text, lang: S.lang });
      if (r.agent.lang && r.agent.lang !== S.lang) { S.lang = r.agent.lang; renderChips(); }
      appendBubble({ speaker: "agent", text: r.agent.text, translation_en: r.agent.text_en });
      S.sending = false;
      await speak(r.agent.text, r.agent.lang);
      if (r.ended) { setTimeout(() => showEnded(S.myCall, "Saathi has noted everything for your family."), 600); }
      else if (S.view === "call") listen();
    } catch (err) {
      S.sending = false;
      $("heard").textContent = err.message;
      if (/not active/i.test(err.message)) showEnded(S.myCall, "This call has ended.");
    }
  }

  function bubbleHtml(t) {
    return `<div class="bubble ${t.speaker}">${esc(t.text)}</div>`;
  }
  function appendBubble(t) {
    const box = $("ph-transcript");
    box.insertAdjacentHTML("beforeend", bubbleHtml(t));
    box.scrollTop = box.scrollHeight;
  }
  function renderTurns(turns) {
    const box = $("ph-transcript");
    box.innerHTML = turns.filter((t) => t.speaker !== "system").map(bubbleHtml).join("");
    box.scrollTop = box.scrollHeight;
  }
  function showEnded(sid, msg) {
    if (S.endedShownFor === sid) return;
    S.endedShownFor = sid; S.callStart = null;
    $("over-sub").textContent = msg;
    $("v-call").classList.add("over");
    setView("call");
  }
  function openCall() { $("v-call").classList.remove("over"); setView("call"); }
  $("btn-done").addEventListener("click", () => { S.myCall = null; $("v-call").classList.remove("over"); setView("idle"); });

  // ------------------------------------------------------------- polling
  async function poll() {
    try {
      const st = await api(`/api/phone/${encodeURIComponent(pid)}/state`);
      if (!S.langChosen && st.patient && S.view !== "call") { S.lang = st.patient.language || "en"; renderLangButtons(); renderChips(); }
      const s = st.session;
      S.session = s;
      if (!s) { if (!$("v-call").classList.contains("over")) setView("idle"); return; }
      if (s.status === "ringing") {
        if (S.view !== "ring") { setView("ring"); renderLangButtons(); }
        $("ring-sub").textContent = `${s.slot === "morning" ? "Morning" : "Evening"} medicine check-in · attempt ${s.attempts} of ${s.max_attempts}`;
        $("ring-left").textContent = st.ring_seconds_left != null ? `Counts as not answered in ${st.ring_seconds_left}s` : "";
        startRing();
      } else if (s.status === "in_progress") {
        if (S.view !== "call" || S.myCall !== s.id) { S.myCall = s.id; S.callStart = S.callStart || Date.now(); S.endedShownFor = null; openCall(); renderTurns(st.turns); }
        else if (!S.sending && !S.speaking && st.turns.length > document.querySelectorAll("#ph-transcript .bubble").length) renderTurns(st.turns);
      } else if (S.view === "call" && S.myCall === s.id && s.status !== "in_progress") {
        if (!S.speaking) showEnded(s.id, "Saathi has noted everything for your family.");
      } else if (S.view === "ring") {
        setView("idle");
      }
      if (S.view === "idle") {
        $("next").textContent = s.status === "retry_wait" ? `Missed a call from Saathi. It will call again at ${new Date(s.next_ring_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`
          : "Saathi will call at medicine time.";
      }
    } catch (e) { /* offline — keep trying */ }
  }

  if (!("speechSynthesis" in window) && !(window.AudioContext || window.webkitAudioContext)) $("sound-gate").classList.add("hidden");
  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => speechSynthesis.getVoices();
  if (!SR) $("heard").textContent = "Type your answer below (voice input works in Chrome and Edge).";
  renderChips();
  poll();
  setInterval(poll, 1200);
})();
