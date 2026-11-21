(function () {
  "use strict";

  function detectWorkspaceId() {
    const m = (window.location.pathname || "").match(/^\/w\/([a-zA-Z0-9_-]{1,40})(?:\/|$)/);
    if (m && m[1]) return m[1];
    try {
      const p = new URLSearchParams(window.location.search);
      const qWs = (p.get("ws") || p.get("workspace") || "").trim();
      if (qWs) return qWs;
    } catch (_e) {}
    return "default";
  }

  const WORKSPACE_ID = detectWorkspaceId();
  const WS_KEY_PREFIX = WORKSPACE_ID === "default" ? "" : `${WORKSPACE_ID}_`;

  const STORAGE_KEYS = {
    unitId: `parking_mobile_${WS_KEY_PREFIX}unit_id`,
    vehicleType: `parking_mobile_${WS_KEY_PREFIX}vehicle_type`,
    repeatCount: `parking_mobile_${WS_KEY_PREFIX}repeat_count`,
    continuous: `parking_mobile_${WS_KEY_PREFIX}continuous`,
    showBackdrop: `parking_mobile_${WS_KEY_PREFIX}show_backdrop`,
    voiceUri: `parking_mobile_${WS_KEY_PREFIX}voice_uri`,
    token: `parking_mobile_${WS_KEY_PREFIX}token`,
  };

  const POLL_INTERVAL_MS = 4000;
  const CONTINUOUS_REPEAT_MS = 16000;

  const state = {
    workspaceId: WORKSPACE_ID,
    token: "",
    preferredVoiceLang: "",
    overview: null,
    unitId: "",
    vehicleType: "sedan", // "sedan" | "ev"
    tagFilter: "",
    repeatCount: 2, // 1 | 2
    continuousBroadcast: true,
    sessionMutedByQuery: false,
    embedMode: false,
    voiceUri: "", // "" = browser built-in instant default voice
    showBackdrop: true,
    backdropPreferenceSaved: false,
    backdropDataUrl: null,
    backdropLoading: false,
    zoom: 1.0,
    panX: 0,
    panY: 0,
    worldW: 145,
    worldH: 100,
    latestVoiceText: "",
    latestRepeatText: "",
    lastAnnouncedSig: "",
    isSpeaking: false,
    currentPass: 0,
    totalPasses: 0,
    speechSessionId: 0,
    passTimer: null,
    onlineFallbackTimer: null,
    watchdogTimer: null,
    speechPollTimer: null,
    continuousTimer: null,
    toastTimer: null,
    userInteracted: false,
    speechBlockedByAutoplay: false,
    wakeLock: null,
    pollTimer: null,
    fetching: false,
    fetchAbortCtrl: null,
  };

  const els = {
    liveDot: document.getElementById("liveDot"),
    communityName: document.getElementById("communityName"),
    totalFreePill: document.getElementById("totalFreePill"),
    totalFreeNum: document.getElementById("totalFreeNum"),
    unitSelect: document.getElementById("mobileUnitSelect"),
    btnEvModeToggle: document.getElementById("btnEvModeToggle"),
    evModeLabel: document.getElementById("evModeLabel"),
    btnSpeakAction: document.getElementById("btnSpeakAction"),
    repeatModeGroup: document.getElementById("repeatModeGroup"),
    voiceSelect: document.getElementById("mobileVoiceSelect"),
    btnContinuousToggle: document.getElementById("btnContinuousToggle"),
    continuousLabel: document.getElementById("continuousLabel"),
    tagFilterBar: document.getElementById("mobileTagFilterBar"),
    guidanceCard: document.getElementById("guidanceCard"),
    recBadgeTag: document.getElementById("recBadgeTag"),
    recZoneName: document.getElementById("recZoneName"),
    recFreePill: document.getElementById("recFreePill"),
    recWalkPill: document.getElementById("recWalkPill"),
    recBackupText: document.getElementById("recBackupText"),
    voiceStatusBadge: document.getElementById("voiceStatusBadge"),
    mapStage: document.getElementById("mobileMapStage"),
    mapSvg: document.getElementById("mobileMapSvg"),
    btnZoomIn: document.getElementById("btnZoomIn"),
    btnZoomOut: document.getElementById("btnZoomOut"),
    btnZoomReset: document.getElementById("btnZoomReset"),
    btnBackdropToggle: document.getElementById("btnBackdropToggle"),
    mapSpotToast: document.getElementById("mapSpotToast"),
    spotSheet: document.getElementById("mobileSpotSheet"),
    mobSheetZone: document.getElementById("mobSheetZone"),
    mobSheetTitle: document.getElementById("mobSheetTitle"),
    mobSheetStatus: document.getElementById("mobSheetStatus"),
    mobSheetTags: document.getElementById("mobSheetTags"),
    mobSheetDetails: document.getElementById("mobSheetDetails"),
    btnCloseMobSpotSheet: document.getElementById("btnCloseMobSpotSheet"),
    mobLegendEv: document.getElementById("mobLegendEv"),
    mobLegendRent: document.getElementById("mobLegendRent"),
    authCard: document.getElementById("mobileAuthCard"),
    authTitle: document.getElementById("mobileAuthTitle"),
    tokenInput: document.getElementById("mobileTokenInput"),
    btnSaveToken: document.getElementById("btnSaveMobileToken"),
  };

  function mobileAuthHeaders() {
    const h = {};
    if (state.workspaceId && state.workspaceId !== "default") {
      h["X-Parking-Workspace"] = state.workspaceId;
    }
    if (state.token) {
      h["X-Parking-Token"] = state.token;
    }
    return h;
  }

  function appendWorkspaceQuery(baseUrl) {
    const u = new URL(baseUrl, window.location.origin);
    if (state.workspaceId && state.workspaceId !== "default") {
      u.searchParams.set("ws", state.workspaceId);
    }
    if (state.token) {
      u.searchParams.set("token", state.token);
    }
    return u.pathname + u.search;
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function loadPersistedSettings() {
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const qTok = (urlParams.get("token") || urlParams.get("k") || "").trim();
      if (qTok) {
        state.token = qTok;
        localStorage.setItem(STORAGE_KEYS.token, qTok);
      } else {
        state.token = (localStorage.getItem(STORAGE_KEYS.token) || "").trim();
      }
      if (urlParams.get("embed") === "1") {
        state.embedMode = true;
        document.body.classList.add("is-embed");
      }

      const queryUnit = (urlParams.get("unit") || "").trim();
      const savedUnit = (localStorage.getItem(STORAGE_KEYS.unitId) || "").trim();
      state.unitId = queryUnit || savedUnit || "";

      const qVeh = (urlParams.get("vehicle_type") || urlParams.get("vehicle") || "").trim();
      const savedVeh = (localStorage.getItem(STORAGE_KEYS.vehicleType) || "").trim();
      state.vehicleType = (qVeh || savedVeh) === "ev" ? "ev" : "sedan";
      const qTag = (urlParams.get("tag") || urlParams.get("tag_filter") || "").trim();
      if (qTag) state.tagFilter = qTag;

      const savedRepeat = localStorage.getItem(STORAGE_KEYS.repeatCount);
      const savedCont = localStorage.getItem(STORAGE_KEYS.continuous);
      if (savedRepeat === "1" || savedRepeat === "2") {
        state.repeatCount = Number(savedRepeat);
      } else {
        state.repeatCount = 2;
      }

      if (savedRepeat === "0") {
        // Migrate legacy muted state cleanly into Auto-Broadcast OFF while keeping 2-pass manual playback ready
        state.continuousBroadcast = false;
        localStorage.setItem(STORAGE_KEYS.repeatCount, "2");
        localStorage.setItem(STORAGE_KEYS.continuous, "0");
      } else if (savedCont === "0" || savedCont === "1") {
        state.continuousBroadcast = savedCont === "1";
      } else {
        state.continuousBroadcast = true;
      }

      state.voiceUri = (localStorage.getItem(STORAGE_KEYS.voiceUri) || "").trim();
      const rawBackdropPref = localStorage.getItem(STORAGE_KEYS.showBackdrop);
      if (urlParams.get("backdrop") === "1") {
        state.showBackdrop = true;
        state.backdropPreferenceSaved = true;
      } else if (rawBackdropPref === "0" || rawBackdropPref === "1") {
        state.showBackdrop = rawBackdropPref === "1";
        state.backdropPreferenceSaved = true;
      } else {
        state.showBackdrop = true;
        state.backdropPreferenceSaved = false;
      }

      // If opened by Siri / Apple Shortcut (`?mute=1`) or embedded (`?embed=1`),
      // mute auto-broadcast in this session without overwriting saved settings.
      if (
        state.embedMode ||
        urlParams.get("mute") === "1" ||
        urlParams.get("autoplay") === "0"
      ) {
        state.sessionMutedByQuery = true;
      }
    } catch (_err) {
      // Ignore storage access errors in private WebViews
    }
  }

  function persistSettings() {
    try {
      if (state.unitId) {
        localStorage.setItem(STORAGE_KEYS.unitId, state.unitId);
      }
      localStorage.setItem(STORAGE_KEYS.vehicleType, state.vehicleType || "sedan");
      localStorage.setItem(STORAGE_KEYS.repeatCount, String(state.repeatCount));
      localStorage.setItem(
        STORAGE_KEYS.continuous,
        state.continuousBroadcast ? "1" : "0"
      );
      localStorage.setItem(STORAGE_KEYS.voiceUri, state.voiceUri || "");
      localStorage.setItem(
        STORAGE_KEYS.showBackdrop,
        state.showBackdrop ? "1" : "0"
      );
    } catch (_err) {
      // Ignore storage errors
    }
  }

  function syncUrlUnitParam(unitId) {
    if (!unitId || !window.history || !window.history.replaceState) return;
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.get("unit") !== unitId) {
        url.searchParams.set("unit", unitId);
        window.history.replaceState(null, "", url.toString());
      }
    } catch (_err) {
      // Ignore URL errors
    }
  }

  async function requestWakeLockIfNeeded() {
    if (!("wakeLock" in navigator)) return;
    if (state.continuousBroadcast && state.repeatCount > 0) {
      if (state.wakeLock) return;
      try {
        state.wakeLock = await navigator.wakeLock.request("screen");
        state.wakeLock.addEventListener("release", () => {
          state.wakeLock = null;
        });
      } catch (_err) {
        // WakeLock unsupported or denied
      }
    } else if (state.wakeLock) {
      try {
        await state.wakeLock.release();
      } catch (_err) {
        // Ignore
      }
      state.wakeLock = null;
    }
  }

  const SPEAKER_SVG = `<svg class="m-icon-svg" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 6.2h2.3L8.2 3.4v9.2L4.8 9.8H2.5a.7.7 0 0 1-.7-.7V6.9c0-.4.3-.7.7-.7Z"/><path d="M10.8 5.5a3.6 3.6 0 0 1 0 5"/><path d="M12.8 3.7a6.2 6.2 0 0 1 0 8.6"/></svg>`;
  const EQ_BARS_HTML = `<span class="m-eq-bars" aria-hidden="true"><i></i><i></i><i></i></span>`;

  function syncBroadcastControlUi() {
    const segBtns = els.repeatModeGroup.querySelectorAll(".m-seg-btn");
    segBtns.forEach((btn) => {
      const val = Number(btn.dataset.repeat);
      const active = val === state.repeatCount;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-checked", active ? "true" : "false");
    });

    const contActive = Boolean(state.continuousBroadcast);
    els.btnContinuousToggle.classList.toggle("is-on", contActive);
    els.btnContinuousToggle.setAttribute(
      "aria-pressed",
      contActive ? "true" : "false"
    );
    els.continuousLabel.textContent = "自动播报";

    els.btnSpeakAction.classList.toggle("is-speaking", state.isSpeaking);
    els.btnSpeakAction.classList.toggle(
      "needs-unlock",
      !state.isSpeaking && state.speechBlockedByAutoplay && contActive
    );

    if (state.isSpeaking) {
      const stopTxt =
        state.totalPasses > 1
          ? `停止 ${state.currentPass}/${state.totalPasses}`
          : "停止";
      els.btnSpeakAction.innerHTML = `${EQ_BARS_HTML}<span>${stopTxt}</span>`;
      els.voiceStatusBadge.textContent =
        state.totalPasses > 1
          ? `正在播报 (${state.currentPass}/${state.totalPasses})`
          : "语音播报中…";
    } else if (state.speechBlockedByAutoplay && contActive) {
      els.btnSpeakAction.innerHTML = `${SPEAKER_SVG}<span>轻触播报</span>`;
      els.voiceStatusBadge.textContent = "轻触解锁语音";
    } else {
      els.btnSpeakAction.innerHTML = `${SPEAKER_SVG}<span>播报</span>`;
      els.voiceStatusBadge.textContent = contActive
        ? `自动播报 · ${state.repeatCount}遍`
        : `按需播报 · ${state.repeatCount}遍`;
    }
  }

  // ---------------------------------------------------------------------------
  // Multi-Pass (1x / 2x) + Continuous Web Speech Engine (Instant Local Default + Optional Voices)
  // ---------------------------------------------------------------------------

  function classifyChineseVoiceDialect(v) {
    const lang = String(v.lang || "").toLowerCase();
    const name = String(v.name || "");
    if (
      lang.includes("hk") ||
      lang.includes("mo") ||
      lang.includes("yue") ||
      /sin[-_ ]?ji|善怡|hiumaan|hiugaai|wanlung|晓曼|曉曼|晓佳|曉佳|云龙|雲龍|粤|粵|cantonese/i.test(name)
    ) {
      return { group: "cantonese", tag: "粤语" };
    }
    if (/liaoning|晓北|东北/i.test(`${lang} ${name}`)) {
      return { group: "dialect", tag: "东北话" };
    }
    if (/shaanxi|晓妮|陕西/i.test(`${lang} ${name}`)) {
      return { group: "dialect", tag: "陕西话" };
    }
    if (lang.includes("tw") || /mei[-_ ]?jia|美佳|hsiaochen|hsiaoyu|yunjhe|曉臻|曉雨|雲哲/i.test(name)) {
      return { group: "dialect", tag: "台湾腔" };
    }
    return { group: "mandarin", tag: "普通话" };
  }

  function isCloudOnlineVoice(v) {
    if (!v) return false;
    return /online|cloud|natural/i.test(`${v.name || ""} ${v.voiceURI || ""}`);
  }

  function normalizeVoiceBcp47Lang(v, fallbackLang) {
    if (v) {
      const info = classifyChineseVoiceDialect(v);
      const raw = String(v.lang || "").trim().replace(/_/g, "-");
      if (info.group === "cantonese") {
        return /^(zh-HK|zh-MO|yue)/i.test(raw) ? raw : "zh-HK";
      }
      if (info.tag === "台湾腔") {
        return /^zh-TW/i.test(raw) ? raw : "zh-TW";
      }
      if (raw) return raw;
    }
    return String(fallbackLang || "zh-CN").trim().replace(/_/g, "-") || "zh-CN";
  }

  function formatVoiceOptionLabel(v) {
    const info = classifyChineseVoiceDialect(v);
    const rawName = String(v.name || v.voiceURI || "中文语音")
      .replace(/^Microsoft\s+/i, "")
      .replace(/\s*Online\s*\(Natural\)\s*-\s*Chinese.*$/i, "")
      .replace(/\s*-\s*Chinese.*$/i, "")
      .trim();
    const isOnline = isCloudOnlineVoice(v);
    const speedTag = isOnline ? "自然" : "极速";
    return `${rawName} · ${info.tag} (${speedTag})`;
  }

  function populateVoiceSelectOptions() {
    if (!els.voiceSelect || !("speechSynthesis" in window)) return;
    const allVoices = window.speechSynthesis.getVoices() || [];
    const zhVoices = allVoices.filter((v) =>
      /^zh|chinese|cmn|yue|cantonese|sin[-_ ]?ji|善怡|tingting|婷婷|mei[-_ ]?jia|美佳/i.test(
        `${v.lang || ""} ${v.name || ""}`
      )
    );
    zhVoices.sort((a, b) => {
      const aCloud = isCloudOnlineVoice(a) ? 1 : 0;
      const bCloud = isCloudOnlineVoice(b) ? 1 : 0;
      if (aCloud !== bCloud) return aCloud - bCloud;
      const aLocal = a.localService !== false ? 0 : 1;
      const bLocal = b.localService !== false ? 0 : 1;
      if (aLocal !== bLocal) return aLocal - bLocal;
      return String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
    });

    const prevVal = state.voiceUri || "";
    els.voiceSelect.innerHTML = "";
    const defOpt = document.createElement("option");
    defOpt.value = "";
    defOpt.textContent = "默认音色";
    els.voiceSelect.appendChild(defOpt);

    const groups = {
      mandarin: { label: "普通话 · 国语", items: [] },
      cantonese: { label: "粤语 · 广东话 (善怡等)", items: [] },
      dialect: { label: "台湾腔与特色方言", items: [] },
    };
    const seen = new Set();
    const seenLabels = new Set();
    zhVoices.forEach((v) => {
      const uri = String(v.voiceURI || v.name || "").trim();
      if (!uri || seen.has(uri)) return;
      const lbl = formatVoiceOptionLabel(v);
      if (seenLabels.has(lbl) && uri !== prevVal) return;
      seen.add(uri);
      seenLabels.add(lbl);
      const info = classifyChineseVoiceDialect(v);
      groups[info.group].items.push({ uri, label: lbl });
    });

    ["mandarin", "cantonese", "dialect"].forEach((gk) => {
      const grp = groups[gk];
      if (!grp.items.length) return;
      const og = document.createElement("optgroup");
      og.label = grp.label;
      grp.items.forEach((it) => {
        const opt = document.createElement("option");
        opt.value = it.uri;
        opt.textContent = it.label;
        og.appendChild(opt);
      });
      els.voiceSelect.appendChild(og);
    });

    if (prevVal && seen.has(prevVal)) {
      els.voiceSelect.value = prevVal;
    } else {
      els.voiceSelect.value = "";
    }
  }

  function resolveSelectedVoice(preferLocalFallbackForLang) {
    if (!("speechSynthesis" in window)) return null;
    const voices = window.speechSynthesis.getVoices() || [];
    if (preferLocalFallbackForLang) {
      const target = String(preferLocalFallbackForLang).toLowerCase();
      return (
        voices.find(
          (v) =>
            !isCloudOnlineVoice(v) &&
            normalizeVoiceBcp47Lang(v, "").toLowerCase().startsWith(target.slice(0, 5))
        ) || null
      );
    }
    if (state.voiceUri) {
      const matches = voices.filter(
        (v) => v.voiceURI === state.voiceUri || v.name === state.voiceUri
      );
      if (matches.length) {
        // Prefer localService === true when macOS registers duplicate entries (e.g. two Sinji / 善怡)
        return matches.find((v) => v.localService !== false) || matches[0];
      }
      return null;
    }
    const prefLang = String(state.preferredVoiceLang || "")
      .trim()
      .replace(/_/g, "-")
      .toLowerCase();
    if (prefLang) {
      const localPref = voices.find(
        (v) =>
          !isCloudOnlineVoice(v) &&
          normalizeVoiceBcp47Lang(v, "").toLowerCase().startsWith(prefLang)
      );
      if (localPref) return localPref;
    }
    const localZh = voices.find(
      (v) => !isCloudOnlineVoice(v) && /^zh[-_]?CN/i.test(String(v.lang || ""))
    );
    return localZh || null;
  }

  const activeMobileUtterances = new Set();

  function clearSpeechTimers() {
    if (state.passTimer) {
      clearTimeout(state.passTimer);
      state.passTimer = null;
    }
    if (state.onlineFallbackTimer) {
      clearTimeout(state.onlineFallbackTimer);
      state.onlineFallbackTimer = null;
    }
    if (state.watchdogTimer) {
      clearTimeout(state.watchdogTimer);
      state.watchdogTimer = null;
    }
    if (state.speechPollTimer) {
      clearInterval(state.speechPollTimer);
      state.speechPollTimer = null;
    }
  }

  function clearContinuousTimer() {
    if (state.continuousTimer) {
      clearTimeout(state.continuousTimer);
      state.continuousTimer = null;
    }
  }

  function stopBroadcast(cancelContinuous = false) {
    state.speechSessionId += 1;
    clearSpeechTimers();
    if (cancelContinuous) {
      clearContinuousTimer();
    }
    state.isSpeaking = false;
    state.currentPass = 0;
    state.totalPasses = 0;
    if ("speechSynthesis" in window) {
      try {
        if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
          window.speechSynthesis.cancel();
        }
      } catch (_err) {
        // Ignore
      }
    }
    syncBroadcastControlUi();
  }

  function scheduleNextContinuousCycle() {
    clearContinuousTimer();
    if (!state.continuousBroadcast || state.repeatCount <= 0) return;
    if (document.hidden) return;
    state.continuousTimer = setTimeout(() => {
      state.continuousTimer = null;
      if (!state.continuousBroadcast || state.repeatCount <= 0 || document.hidden) {
        return;
      }
      if (state.latestVoiceText && !state.isSpeaking) {
        speakBroadcast(state.latestVoiceText, state.repeatCount, "continuous");
      }
    }, CONTINUOUS_REPEAT_MS);
  }

  function speakBroadcast(text, passes, _reason) {
    const cleanText = String(text || "").trim();
    const targetPasses = Math.max(1, Math.min(2, Number(passes) || 1));
    if (!cleanText) return;

    if (!("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") {
      els.voiceStatusBadge.textContent = "当前浏览器不支持语音合成";
      return;
    }

    const hadActiveSpeech = Boolean(
      window.speechSynthesis.speaking || window.speechSynthesis.pending
    );
    stopBroadcast(true);
    const sessionId = state.speechSessionId;
    state.isSpeaking = true;
    const fullSpeechText =
      targetPasses >= 2 && state.latestRepeatText
        ? `${cleanText} 再为您重复播报一遍：${state.latestRepeatText}`
        : cleanText;
    state.totalPasses = 1;
    state.currentPass = 1;
    syncBroadcastControlUi();

    const runSinglePass = (_passNo, forceBuiltInDefault = false) => {
      if (sessionId !== state.speechSessionId) return;
      state.currentPass = 1;
      syncBroadcastControlUi();

      let finished = false;
      let startedSpeaking = false;

      const completePass = (blocked = false) => {
        if (finished || sessionId !== state.speechSessionId) return;
        finished = true;
        clearSpeechTimers();

        if (blocked) {
          state.isSpeaking = false;
          state.currentPass = 0;
          state.totalPasses = 0;
          state.speechBlockedByAutoplay = true;
          syncBroadcastControlUi();
          return;
        }

        state.speechBlockedByAutoplay = false;
        state.isSpeaking = false;
        state.currentPass = 0;
        state.totalPasses = 0;
        syncBroadcastControlUi();
        scheduleNextContinuousCycle();
      };

      try {
        const passText = fullSpeechText;
        const utter = new SpeechSynthesisUtterance(passText);
        activeMobileUtterances.add(utter);
        const startIssuedAt = Date.now();
        utter.rate = 1.04;
        utter.pitch = 1.0;
        const primaryPick = resolveSelectedVoice(null);
        const targetLang = normalizeVoiceBcp47Lang(primaryPick, state.preferredVoiceLang || "zh-CN");
        const chosenVoice = forceBuiltInDefault
          ? resolveSelectedVoice(targetLang)
          : primaryPick;
        if (chosenVoice) {
          utter.voice = chosenVoice;
          utter.lang = normalizeVoiceBcp47Lang(chosenVoice, targetLang);
        } else {
          utter.lang = targetLang;
        }
        const isOnlineVoice = isCloudOnlineVoice(chosenVoice);

        utter.onstart = () => {
          startedSpeaking = true;
          if (state.onlineFallbackTimer) {
            clearTimeout(state.onlineFallbackTimer);
            state.onlineFallbackTimer = null;
          }
          state.speechBlockedByAutoplay = false;
          syncBroadcastControlUi();
        };
        utter.onend = () => {
          activeMobileUtterances.delete(utter);
          completePass(false);
        };
        utter.onerror = (ev) => {
          activeMobileUtterances.delete(utter);
          const errCode = String((ev && ev.error) || "").toLowerCase();
          if (errCode.includes("interrupted") || errCode.includes("canceled")) {
            return;
          }
          if (
            errCode.includes("not-allowed") ||
            (!state.userInteracted && !startedSpeaking)
          ) {
            completePass(true);
          } else if (isOnlineVoice && !startedSpeaking && !forceBuiltInDefault) {
            finished = true;
            clearSpeechTimers();
            runSinglePass(passNo, true);
          } else {
            completePass(false);
          }
        };

        if (window.speechSynthesis.paused) {
          window.speechSynthesis.resume();
        }
        window.speechSynthesis.speak(utter);

        // State poller guarantees button auto-resets even if WebKit drops `onend`
        state.speechPollTimer = setInterval(() => {
          if (finished || sessionId !== state.speechSessionId) {
            clearInterval(state.speechPollTimer);
            state.speechPollTimer = null;
            return;
          }
          const busy = Boolean(window.speechSynthesis.speaking || window.speechSynthesis.pending);
          if (window.speechSynthesis.speaking && !startedSpeaking) {
            startedSpeaking = true;
          }
          if (startedSpeaking && !busy && Date.now() - startIssuedAt > 350) {
            activeMobileUtterances.delete(utter);
            completePass(false);
          }
        }, 240);

        // Only true cloud neural voices (e.g. Microsoft Online) use network timeout fallback
        if (isOnlineVoice && state.userInteracted && !forceBuiltInDefault) {
          state.onlineFallbackTimer = setTimeout(() => {
            if (
              !finished &&
              sessionId === state.speechSessionId &&
              !startedSpeaking &&
              !window.speechSynthesis.speaking
            ) {
              activeMobileUtterances.delete(utter);
              finished = true;
              clearSpeechTimers();
              try {
                window.speechSynthesis.cancel();
              } catch (_e) {}
              runSinglePass(passNo, true);
            }
          }, 3200);
        }

        // Detect iOS Safari / mobile WebView silent pre-gesture block quickly
        if (!state.userInteracted) {
          setTimeout(() => {
            if (
              !finished &&
              sessionId === state.speechSessionId &&
              !startedSpeaking &&
              !window.speechSynthesis.speaking
            ) {
              activeMobileUtterances.delete(utter);
              try {
                window.speechSynthesis.cancel();
              } catch (_e) {
                // Ignore
              }
              completePass(true);
            }
          }, 900);
        }

        // Watchdog in case mobile WebView drops onend callback
        const maxDurationMs = Math.max(6000, passText.length * 360);
        state.watchdogTimer = setTimeout(() => {
          activeMobileUtterances.delete(utter);
          completePass(false);
        }, maxDurationMs);
      } catch (_err) {
        completePass(!state.userInteracted);
      }
    };

    // Always invoke synchronously inside user-gesture stack so iOS Safari never blocks speech!
    runSinglePass(1, false);
  }

  function buildRecommendationSignature(rec, unitId) {
    if (!rec) return "";
    const p = rec.primary || {};
    const b = rec.backup || {};
    return [
      unitId || "",
      rec.has_recommendation ? "REC" : "FULL",
      p.zone_id || "NONE",
      p.group_code || "",
      p.effective_free ?? 0,
      b.zone_id || "",
      b.group_code || "",
      b.effective_free ?? 0,
    ].join("|");
  }

  // ---------------------------------------------------------------------------
  // Site Map SVG Rendering (1:1 Parity with Main Console Floor-Plan)
  // ---------------------------------------------------------------------------

  function getSiteMapMetrics(smap) {
    const aspect = Math.max(
      0.45,
      Math.min(2.6, Number((smap && smap.aspect_ratio) || 1.45))
    );
    const worldW = aspect >= 1 ? +(100 * aspect).toFixed(3) : 100;
    const worldH = aspect >= 1 ? 100 : +(100 / aspect).toFixed(3);
    return {
      worldW,
      worldH,
      px: (v) => +((Number(v || 0) / 100) * worldW).toFixed(3),
      py: (v) => +((Number(v || 0) / 100) * worldH).toFixed(3),
    };
  }

  function applySvgViewBox() {
    const z = Math.max(1, Math.min(4.5, Number(state.zoom || 1)));
    const vw = state.worldW / z;
    const vh = state.worldH / z;
    const maxX = Math.max(0, state.worldW - vw);
    const maxY = Math.max(0, state.worldH - vh);
    state.panX = Math.max(0, Math.min(maxX, state.panX));
    state.panY = Math.max(0, Math.min(maxY, state.panY));
    els.mapSvg.setAttribute(
      "viewBox",
      `${state.panX.toFixed(2)} ${state.panY.toFixed(2)} ${vw.toFixed(
        2
      )} ${vh.toFixed(2)}`
    );
    if (els.btnZoomReset) {
      els.btnZoomReset.hidden = z <= 1.02;
    }
  }

  function zoomAroundRatio(newZoom, anchorRatioX = 0.5, anchorRatioY = 0.5) {
    const oldZ = Math.max(1, Math.min(4.5, Number(state.zoom || 1)));
    const targetZ = Math.max(1, Math.min(4.5, Number(newZoom || 1)));
    const oldVw = state.worldW / oldZ;
    const oldVh = state.worldH / oldZ;
    const focusWorldX = state.panX + oldVw * anchorRatioX;
    const focusWorldY = state.panY + oldVh * anchorRatioY;

    const newVw = state.worldW / targetZ;
    const newVh = state.worldH / targetZ;
    state.zoom = targetZ;
    if (targetZ <= 1.001) {
      state.zoom = 1.0;
      state.panX = 0;
      state.panY = 0;
    } else {
      state.panX = focusWorldX - newVw * anchorRatioX;
      state.panY = focusWorldY - newVh * anchorRatioY;
    }
    applySvgViewBox();
    renderFloorPlanSvg();
  }

  function formatCompactUnitBadgeLabel(u, allUnits) {
    const uid = String((u && u.unit_id) || "").trim();
    const m = uid.match(/^([A-Za-z0-9]+)[-_#](\d+)$/);
    if (!m) return `${uid}栋`;
    const bldg = m[1];
    const unitNo = Number(m[2]);
    const siblingCount = (allUnits || []).filter((x) => {
      const xm = String((x && x.unit_id) || "").match(
        /^([A-Za-z0-9]+)[-_#](\d+)$/
      );
      return xm && xm[1].toUpperCase() === bldg.toUpperCase();
    }).length;
    if (siblingCount >= 2 || unitNo >= 2) {
      return `${bldg}栋${unitNo}单元`;
    }
    return `${bldg}栋`;
  }

  function shortenSpotLabel(label, fallbackIdx) {
    const s = String(label || "").trim();
    const m = s.match(/(\d{1,3})$/);
    if (m) return m[1].padStart(2, "0");
    if (s.length <= 4) return s;
    return s.slice(-3) || String(fallbackIdx + 1).padStart(2, "0");
  }

  function renderFloorPlanSvg() {
    const ov = state.overview;
    if (!ov) return;
    const smap = ov.site_map || {};
    const { worldW, worldH, px, py } = getSiteMapMetrics(smap);
    state.worldW = worldW;
    state.worldH = worldH;
    applySvgViewBox();

    const invScale = Math.pow(Math.max(1, state.zoom || 1), -0.52);
    const topo = smap.topology || {};
    const roads =
      topo.roads && topo.roads.length
        ? topo.roads
        : [
            {
              road_id: "R_MAIN",
              points: [
                [8, 83],
                [88, 83],
                [88, 22],
                [12, 22],
              ],
              width: 5.2,
            },
          ];
    const walkways = topo.walkways || [];
    const gates =
      topo.gates && topo.gates.length
        ? topo.gates
        : [
            { gate_id: "south", name: "南门入口", x: 47, y: 96.5, w: 8.5, h: 2.8 },
            { gate_id: "east", name: "东门", x: 95.5, y: 52, w: 6.5, h: 2.8 },
          ];
    const greens = topo.greens || [];
    const unitsToDraw = Array.isArray(ov.units) ? ov.units : [];

    const rec = ov.recommendation || null;
    const recPrimary = rec && rec.has_recommendation ? rec.primary : null;
    const recZoneId = recPrimary ? recPrimary.zone_id : "";
    const recGroupCode = recPrimary ? recPrimary.group_code || "" : "";

    const spotsToDraw = [];
    (ov.zones || []).forEach((z) => {
      (z.spots || []).forEach((sp) => {
        if (sp.map_placed === false || sp.map_x == null || sp.map_y == null) {
          return;
        }
        spotsToDraw.push({
          ...sp,
          zone_id: sp.zone_id || z.zone_id,
          zone_name: sp.zone_name || z.name,
        });
      });
    });

    let html = `
      <rect x="0.25" y="0.25" width="${(worldW - 0.5).toFixed(
        2
      )}" height="${(worldH - 0.5).toFixed(
        2
      )}" rx="2.6" fill="#F8FAFC" stroke="#E2E8F0" stroke-width="0.4"/>
    `;

    if (state.showBackdrop && state.backdropDataUrl) {
      const bgAlpha = Number(
        (smap.meta && smap.meta.backdrop_alpha) || 0.58
      ).toFixed(2);
      html += `
        <image href="${state.backdropDataUrl}" x="0.25" y="0.25" width="${(
          worldW - 0.5
        ).toFixed(2)}" height="${(worldH - 0.5).toFixed(
          2
        )}" preserveAspectRatio="none" opacity="${bgAlpha}"/>
      `;
    } else {
      for (const gr of greens) {
        html += `
          <rect x="${px(gr.x)}" y="${py(gr.y)}" width="${px(gr.w)}" height="${py(
            gr.h
          )}" rx="2.2"
                fill="rgba(52, 199, 89, 0.09)" stroke="rgba(52, 199, 89, 0.20)" stroke-width="0.3"/>
        `;
      }
    }

    const renderChevronAlongSegments = (
      ptsArr,
      color,
      scaleMul = 1.0,
      twoWay = false
    ) => {
      let chevHtml = "";
      const stepWorld = 10.5;
      let nextAt = 4.5;
      let accum = 0;
      for (let i = 0; i < ptsArr.length - 1; i++) {
        const [x1, y1] = ptsArr[i];
        const [x2, y2] = ptsArr[i + 1];
        const dx = x2 - x1;
        const dy = y2 - y1;
        const segLen = Math.hypot(dx, dy);
        if (segLen < 0.05) continue;
        const ang = (Math.atan2(dy, dx) * 180) / Math.PI;
        while (nextAt <= accum + segLen) {
          const t = Math.max(0.08, Math.min(0.92, (nextAt - accum) / segLen));
          const cx = x1 + dx * t;
          const cy = y1 + dy * t;
          const s = 0.72 * scaleMul;
          chevHtml += `<g transform="translate(${cx.toFixed(2)} ${cy.toFixed(
            2
          )}) rotate(${ang.toFixed(1)})">
            <polygon points="${(1.25 * s).toFixed(2)},0 ${(-0.85 * s).toFixed(
              2
            )},${(-0.85 * s).toFixed(2)} ${(-0.38 * s).toFixed(2)},0 ${(
              -0.85 * s
            ).toFixed(2)},${(0.85 * s).toFixed(2)}" fill="${color}"/>
          </g>`;
          if (twoWay && segLen > 7.5) {
            const cx2 = x1 + dx * Math.min(0.86, t + 0.16);
            const cy2 = y1 + dy * Math.min(0.86, t + 0.16);
            chevHtml += `<g transform="translate(${cx2.toFixed(
              2
            )} ${cy2.toFixed(2)}) rotate(${(ang + 180).toFixed(1)})">
              <polygon points="${(1.05 * s).toFixed(2)},0 ${(
                -0.75 * s
              ).toFixed(2)},${(-0.75 * s).toFixed(2)} ${(-0.32 * s).toFixed(
                2
              )},0 ${(-0.75 * s).toFixed(2)},${(0.75 * s).toFixed(
                2
              )}" fill="${color}" opacity="0.82"/>
            </g>`;
          }
          nextAt += stepWorld;
        }
        accum += segLen;
      }
      return chevHtml;
    };

    // 1. Unified 2F Vehicular Roads Layer
    roads.forEach((r) => {
      const ptsArr = (r.points || []).map(([rx, ry]) => [px(rx), py(ry)]);
      if (ptsArr.length < 2) return;
      const ptsStr = ptsArr.map(([x, y]) => `${x},${y}`).join(" ");
      const rw = Math.max(1.8, Number(r.width || 4.4));
      const isTwoWay = r.one_way === false || r.direction === "twoway";
      const roadFillStroke = state.showBackdrop
        ? "rgba(148, 163, 184, 0.38)"
        : "#E2E8F0";
      const centerDashStroke = state.showBackdrop
        ? "rgba(2, 132, 199, 0.62)"
        : "rgba(14, 165, 233, 0.45)";
      html += `
        <g pointer-events="none">
          <polyline points="${ptsStr}" fill="none" stroke="${roadFillStroke}"
                    stroke-width="${rw}" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="${ptsStr}" fill="none" stroke="${centerDashStroke}"
                    stroke-width="0.40" stroke-dasharray="1.6,1.3"/>
          ${renderChevronAlongSegments(
            ptsArr,
            "rgba(2, 132, 199, 0.80)",
            0.92,
            isTwoWay
          )}
        </g>
      `;
    });

    // 2. 2F Courtyard Pedestrian Walkways Layer
    walkways.forEach((wk) => {
      const ptsArr = (wk.points || []).map(([wx, wy]) => [px(wx), py(wy)]);
      if (ptsArr.length < 2) return;
      const ptsStr = ptsArr.map(([x, y]) => `${x},${y}`).join(" ");
      html += `
        <g pointer-events="none">
          <polyline points="${ptsStr}" fill="none" stroke="rgba(16, 185, 129, 0.16)"
                    stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="${ptsStr}" fill="none" stroke="#10B981"
                    stroke-width="0.46" stroke-dasharray="1.05,0.85" stroke-linecap="round" stroke-linejoin="round"/>
        </g>
      `;
    });

    // 3. Active Driver Recommended Guidance Route Highlight
    const recRoute = rec && rec.recommended_route;
    if (recRoute && Array.isArray(recRoute.points) && recRoute.points.length >= 2) {
      const recPts = recRoute.points.map(([rx, ry]) => [px(rx), py(ry)]);
      const recStr = recPts
        .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`)
        .join(" ");
      const [sx0, sy0] = recPts[0];
      const gateLabel = recRoute.entry_gate_name
        ? `🚗 ${recRoute.entry_gate_name}行车引导`
        : "🚗 进场行车引导";
      const gPillW = Math.max(7.2, gateLabel.length * 1.12 + 1.6) * invScale;
      const gPillH = 1.92 * invScale;
      const gLblY = Math.max(gPillH * 0.7, sy0 - 1.55 * invScale);
      html += `
        <g pointer-events="none">
          <polyline points="${recStr}" fill="none" stroke="rgba(0, 113, 227, 0.22)" stroke-width="${(
            1.85 * invScale
          ).toFixed(2)}" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="${recStr}" fill="none" stroke="#0071E3" stroke-width="${(
            0.68 * invScale
          ).toFixed(2)}" stroke-dasharray="${(1.35 * invScale).toFixed(2)},${(
            0.75 * invScale
          ).toFixed(
            2
          )}" class="active-guidance-route" stroke-linecap="round" stroke-linejoin="round"/>
          ${renderChevronAlongSegments(recPts, "#1D4ED8", 1.05 * invScale, false)}
          <circle cx="${sx0.toFixed(2)}" cy="${sy0.toFixed(2)}" r="${(
            0.78 * invScale
          ).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
            0.28 * invScale
          ).toFixed(2)}"/>
          <rect x="${(sx0 - gPillW / 2).toFixed(2)}" y="${(
            gLblY -
            gPillH / 2
          ).toFixed(2)}" width="${gPillW.toFixed(2)}" height="${gPillH.toFixed(
            2
          )}" rx="${(gPillH / 2).toFixed(
            2
          )}" fill="rgba(0, 113, 227, 0.92)" stroke="#FFFFFF" stroke-width="${(
            0.22 * invScale
          ).toFixed(2)}"/>
          <text x="${sx0.toFixed(2)}" y="${(gLblY + 0.36 * invScale).toFixed(
            2
          )}" font-size="${(0.98 * invScale).toFixed(
            2
          )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeHtml(
            gateLabel
          )}</text>
        </g>
      `;
    }

    // 4. Individual Parking Stalls Layer (1:1 Top-Down Vehicle Silhouettes & Status Colors)
    spotsToDraw.forEach((sp, idx) => {
      const sx = px(sp.map_x ?? 50);
      const sy = py(sp.map_y ?? 50);
      const w = Math.max(
        0.85,
        Number(sp.map_w || (sp.spot_type === "elastic_strip" ? 7.8 : 1.75))
      );
      const h = Math.max(
        0.85,
        Number(sp.map_h || (sp.spot_type === "elastic_strip" ? 2.05 : 3.25))
      );
      const angle = Number(sp.map_angle || 0);

      const effSt = sp.effective_status || sp.status || "FREE";
      const obsType = String(sp.obstacle_type || "none").toLowerCase();
      let fill = "#24B83B";
      if (effSt === "FREE" && obsType === "movable") fill = "#0D9488";
      else if (effSt === "FREE" && obsType === "pedestrian") fill = "#0284C7";
      else if (effSt === "OCCUPIED") fill = "#F03226";
      else if (effSt === "BLOCKED") fill = "#F58A00";
      else if (effSt === "UNKNOWN") fill = "#64748B";

      const isUnassigned = !sp.camera_id || sp.camera_id === "UNASSIGNED";
      if (isUnassigned) {
        fill = "#475569";
      }

      const isRecZone =
        (recGroupCode
          ? sp.group_code === recGroupCode
          : Boolean(recZoneId && sp.zone_id === recZoneId)) &&
        effSt === "FREE" &&
        !isUnassigned;

      const statusCn = isUnassigned
        ? "待接入摄像头"
        : effSt === "FREE" && obsType === "movable"
        ? "可移轻物(可停)"
        : effSt === "FREE" && obsType === "pedestrian"
        ? "临停行人(让行可停)"
        : effSt === "FREE"
        ? "空闲可停"
        : effSt === "OCCUPIED"
        ? "已停车辆"
        : effSt === "BLOCKED"
        ? "占道禁停"
        : "待识别/离线";

      const spTags = Array.isArray(sp.tags) ? sp.tags : [];
      const spMeta = sp.tag_meta || {};
      const isEvSpot = Boolean(sp.is_ev || spTags.includes("ev_charging"));
      const isRentSpot = Boolean(sp.is_rentable || spTags.includes("rentable"));
      const isPrivSpot = Boolean(sp.is_private_reserved || spTags.includes("private"));
      const isAccSpot = spTags.includes("accessible");
      const isWideSpot = spTags.includes("large_friendly");
      const activeTag = state.tagFilter || "";
      const tagMatched =
        !activeTag ||
        spTags.includes(activeTag) ||
        (activeTag === "ev_charging" && isEvSpot);

      const pendTxt = sp.pending_status ? " · 双帧防抖确认中" : "";
      const hintTxt = sp.action_hint ? ` · ${sp.action_hint}` : "";
      const evTxt = isEvSpot ? ` · ⚡新能源充电位` : "";
      const rentTxt = isRentSpot
        ? ` · 🤝对外招租${spMeta.rental_price ? `(${spMeta.rental_price})` : ""}`
        : isPrivSpot
        ? " · 🔒业主专属位"
        : "";
      const spotToast = `${sp.group_name || sp.zone_name || sp.zone_id} · ${
        sp.label
      }号位：${statusCn}${evTxt}${rentTxt}${pendTxt}${hintTxt}`;

      const outerStroke = isUnassigned
        ? "#F59E0B"
        : isEvSpot
        ? "#059669"
        : "rgba(15, 23, 42, 0.36)";
      const outerStrokeW = isUnassigned ? 0.26 : isEvSpot ? 0.28 : 0.2;
      const outerDash = isUnassigned ? 'stroke-dasharray="0.65,0.4"' : "";
      const dimAttr = !tagMatched ? 'opacity="0.24"' : "";

      html += `<g transform="translate(${sx.toFixed(2)} ${sy.toFixed(
        2
      )}) rotate(${angle.toFixed(1)})" ${dimAttr} data-spot-id="${escapeHtml(
        sp.spot_id || ""
      )}" data-spot-toast="${escapeHtml(spotToast)}">`;

      if (isRecZone) {
        html += `<rect x="${(-w / 2 - 0.45).toFixed(2)}" y="${(
          -h / 2 -
          0.45
        ).toFixed(2)}" width="${(w + 0.9).toFixed(2)}" height="${(
          h + 0.9
        ).toFixed(
          2
        )}" rx="0.65" fill="rgba(40, 205, 65, 0.28)" stroke="#28CD41" stroke-width="0.30"/>`;
      }

      html += `
        <rect x="${(-w / 2).toFixed(2)}" y="${(-h / 2).toFixed(
          2
        )}" width="${w.toFixed(2)}" height="${h.toFixed(2)}" rx="0.36"
              fill="${fill}" stroke="${outerStroke}" stroke-width="${outerStrokeW}" ${outerDash}/>
        <rect x="${(-w / 2 + 0.14).toFixed(2)}" y="${(
          -h / 2 +
          0.14
        ).toFixed(2)}" width="${Math.max(0.2, w - 0.28).toFixed(
          2
        )}" height="${Math.max(0.2, h - 0.28).toFixed(2)}" rx="0.26"
              fill="none" stroke="rgba(255,255,255,0.72)" stroke-width="0.14"/>
      `;

      if (sp.spot_type === "elastic_strip") {
        const cap = Math.max(2, Number(sp.capacity || 3));
        for (let k = 1; k < cap; k++) {
          const divX = -w / 2 + (w * k) / cap;
          html += `<line x1="${divX.toFixed(2)}" y1="${(-h * 0.38).toFixed(
            2
          )}" x2="${divX.toFixed(2)}" y2="${(h * 0.38).toFixed(
            2
          )}" stroke="rgba(255,255,255,0.62)" stroke-width="0.18" stroke-dasharray="0.45,0.35"/>`;
        }
      } else if (w > h * 1.22) {
        const cw = w * 0.76;
        const ch = h * 0.68;
        html += `
          <rect x="${(-cw / 2).toFixed(2)}" y="${(-ch / 2).toFixed(
            2
          )}" width="${cw.toFixed(2)}" height="${ch.toFixed(2)}" rx="${(
            ch * 0.28
          ).toFixed(
            2
          )}" fill="rgba(255,255,255,0.16)" stroke="rgba(255,255,255,0.58)" stroke-width="0.13"/>
          <polygon points="${(cw * 0.1).toFixed(2)},${(-ch * 0.36).toFixed(
            2
          )} ${(cw * 0.32).toFixed(2)},${(-ch * 0.28).toFixed(2)} ${(
            cw * 0.32
          ).toFixed(2)},${(ch * 0.28).toFixed(2)} ${(cw * 0.1).toFixed(2)},${(
            ch * 0.36
          ).toFixed(2)}" fill="rgba(255,255,255,0.34)"/>
          <line x1="${(w / 2 - 0.34).toFixed(2)}" y1="${(-h * 0.26).toFixed(
            2
          )}" x2="${(w / 2 - 0.34).toFixed(2)}" y2="${(h * 0.26).toFixed(
            2
          )}" stroke="rgba(255,255,255,0.88)" stroke-width="0.20" stroke-linecap="round"/>
        `;
      } else {
        const cw = w * 0.7;
        const ch = h * 0.76;
        html += `
          <rect x="${(-cw / 2).toFixed(2)}" y="${(-ch / 2).toFixed(
            2
          )}" width="${cw.toFixed(2)}" height="${ch.toFixed(2)}" rx="${(
            cw * 0.28
          ).toFixed(
            2
          )}" fill="rgba(255,255,255,0.16)" stroke="rgba(255,255,255,0.58)" stroke-width="0.13"/>
          <polygon points="${(-cw * 0.36).toFixed(2)},${(-ch * 0.1).toFixed(
            2
          )} ${(cw * 0.36).toFixed(2)},${(-ch * 0.1).toFixed(2)} ${(
            cw * 0.28
          ).toFixed(2)},${(-ch * 0.32).toFixed(2)} ${(-cw * 0.28).toFixed(
            2
          )},${(-ch * 0.32).toFixed(2)}" fill="rgba(255,255,255,0.34)"/>
          <line x1="${(-w * 0.28).toFixed(2)}" y1="${(-h / 2 + 0.36).toFixed(
            2
          )}" x2="${(w * 0.28).toFixed(2)}" y2="${(-h / 2 + 0.36).toFixed(
            2
          )}" stroke="rgba(255,255,255,0.88)" stroke-width="0.20" stroke-linecap="round"/>
        `;
      }

      if (sp.pending_status) {
        html += `<rect x="${(-w / 2 - 0.22).toFixed(2)}" y="${(
          -h / 2 -
          0.22
        ).toFixed(2)}" width="${(w + 0.44).toFixed(2)}" height="${(
          h + 0.44
        ).toFixed(
          2
        )}" rx="0.45" fill="none" stroke="#5856D6" stroke-width="0.34" stroke-dasharray="0.65,0.42"/>`;
      }

      const minDim = Math.min(w, h);
      if (sp.spot_type === "elastic_strip") {
        const lblTxt = `${sp.label || "顺停"} 余${sp.free_units ?? 0}/${
          sp.capacity || 1
        }`;
        html += `<text x="0" y="0.42" font-size="1.15" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeHtml(
          lblTxt
        )}</text>`;
      } else if (minDim >= 1.1) {
        const shortLbl = shortenSpotLabel(sp.label, idx);
        const fontSz = Math.min(1.18, Math.max(0.82, minDim * 0.56)).toFixed(2);
        html += `<g transform="rotate(${(-angle).toFixed(1)})">
          <text x="0" y="${(Number(fontSz) * 0.35).toFixed(
            2
          )}" font-size="${fontSz}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeHtml(
            shortLbl
          )}</text>
        </g>`;
      }

      const badgeR = Math.max(0.56, Math.min(0.82, minDim * 0.38));
      if (isEvSpot) {
        const bx = +(w * 0.38).toFixed(2);
        const by = +(-h * 0.38).toFixed(2);
        const bs = (badgeR / 0.68).toFixed(2);
        html += `<g transform="translate(${bx} ${by}) rotate(${(-angle).toFixed(
          1
        )}) scale(${bs})" pointer-events="none">
          <circle cx="0" cy="0" r="0.68" fill="#064E3B" stroke="#FACC15" stroke-width="0.16"/>
          <polygon points="0.08,-0.44 -0.32,0.05 -0.02,0.05 -0.10,0.44 0.32,-0.05 0.02,-0.05" fill="#FACC15"/>
        </g>`;
      }
      const secBadge = isRentSpot
        ? { txt: "租", bg: "#0071E3" }
        : isPrivSpot
        ? { txt: "专", bg: "#4F46E5" }
        : isAccSpot
        ? { txt: "♿", bg: "#7C3AED" }
        : isWideSpot
        ? { txt: "宽", bg: "#0284C7" }
        : null;
      if (secBadge) {
        const sbx = +(-w * 0.38).toFixed(2);
        const sby = +(-h * 0.38).toFixed(2);
        html += `<g transform="translate(${sbx} ${sby}) rotate(${(-angle).toFixed(
          1
        )})" pointer-events="none">
          <circle cx="0" cy="0" r="${badgeR.toFixed(2)}" fill="${secBadge.bg}" stroke="#FFFFFF" stroke-width="0.15"/>
          <text x="0" y="${(badgeR * 0.38).toFixed(2)}" font-size="${(
          badgeR * 1.08
        ).toFixed(2)}" fill="#FFFFFF" font-weight="800" text-anchor="middle">${
          secBadge.txt
        }</text>
        </g>`;
      }

      html += `</g>`;
    });

    // 5. Walking Path from Selected Unit Door 🚪 to Recommended/Nearest Stall Strip
    let walkObj =
      rec && rec.door_walk_path && rec.door_walk_path.unit_id === state.unitId
        ? rec.door_walk_path
        : null;
    if (!walkObj && state.unitId && Array.isArray(ov.spot_groups)) {
      const candidates = ov.spot_groups.filter(
        (g) =>
          g.map_placed !== false &&
          g.selected_door_walk &&
          Array.isArray(g.selected_door_walk.path_points) &&
          g.selected_door_walk.path_points.length >= 2
      );
      const pref = candidates
        .slice()
        .sort((a, b) => {
          const aFree = Number(a.effective_free || 0) > 0 ? 0 : 1;
          const bFree = Number(b.effective_free || 0) > 0 ? 0 : 1;
          if (aFree !== bFree) return aFree - bFree;
          return (
            Number(
              (a.selected_door_walk && a.selected_door_walk.walk_meters) ?? 9999
            ) -
            Number(
              (b.selected_door_walk && b.selected_door_walk.walk_meters) ?? 9999
            )
          );
        })[0];
      if (pref) {
        walkObj = {
          unit_id: state.unitId,
          path_points: pref.selected_door_walk.path_points,
          walk_meters: pref.selected_door_walk.walk_meters,
        };
      }
    }

    if (
      walkObj &&
      Array.isArray(walkObj.path_points) &&
      walkObj.path_points.length >= 2
    ) {
      const wPts = walkObj.path_points.map(([wx, wy]) => [px(wx), py(wy)]);
      const wStr = wPts
        .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`)
        .join(" ");
      let totalArc = 0;
      for (let i = 0; i < wPts.length - 1; i++) {
        totalArc += Math.hypot(
          wPts[i + 1][0] - wPts[i][0],
          wPts[i + 1][1] - wPts[i][1]
        );
      }
      let halfArc = totalArc * 0.5;
      let mxLbl = (wPts[0][0] + wPts[wPts.length - 1][0]) / 2;
      let myLbl = (wPts[0][1] + wPts[wPts.length - 1][1]) / 2;
      for (let i = 0; i < wPts.length - 1; i++) {
        const seg = Math.hypot(
          wPts[i + 1][0] - wPts[i][0],
          wPts[i + 1][1] - wPts[i][1]
        );
        if (halfArc <= seg && seg > 0.001) {
          const r = halfArc / seg;
          mxLbl = wPts[i][0] + (wPts[i + 1][0] - wPts[i][0]) * r;
          myLbl = wPts[i][1] + (wPts[i + 1][1] - wPts[i][1]) * r;
          break;
        }
        halfArc -= seg;
      }
      myLbl -= 1.25 * invScale;
      const walkText = `🚶 距单元门 ${walkObj.walk_meters}m`;
      const pillW = Math.max(7.6, walkText.length * 1.15 + 1.8) * invScale;
      const pillH = 2.0 * invScale;
      html += `
        <g pointer-events="none">
          <polyline points="${wStr}" fill="none" stroke="rgba(13, 148, 136, 0.22)" stroke-width="${(
            1.45 * invScale
          ).toFixed(2)}" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="${wStr}" fill="none" stroke="#0D9488" stroke-width="${(
            0.52 * invScale
          ).toFixed(2)}" stroke-dasharray="${(0.95 * invScale).toFixed(2)},${(
            0.65 * invScale
          ).toFixed(2)}" class="active-walk-route" stroke-linecap="round" stroke-linejoin="round"/>
          ${renderChevronAlongSegments(wPts, "#0F766E", 0.85 * invScale, false)}
          <rect x="${(mxLbl - pillW / 2).toFixed(2)}" y="${(
            myLbl -
            pillH / 2
          ).toFixed(2)}" width="${pillW.toFixed(2)}" height="${pillH.toFixed(
            2
          )}" rx="${(pillH / 2).toFixed(
            2
          )}" fill="rgba(15, 118, 110, 0.94)" stroke="#FFFFFF" stroke-width="${(
            0.24 * invScale
          ).toFixed(2)}"/>
          <text x="${mxLbl.toFixed(2)}" y="${(myLbl + 0.38 * invScale).toFixed(
            2
          )}" font-size="${(1.06 * invScale).toFixed(
            2
          )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeHtml(
            walkText
          )}</text>
        </g>
      `;
    }

    // 6. Named Parking Strip Summary Chips & In-Transit Soft-Lock Pills (with anti-overlap nudge)
    const occupiedChipBoxes = unitsToDraw.map((u) => {
      const ux = px(u.map_x);
      const uy = py(u.map_y);
      const compactLabel = formatCompactUnitBadgeLabel(u, unitsToDraw);
      const shortText = `${u.unit_id === state.unitId ? "★ " : ""}${compactLabel}`;
      const uw = Math.max(5.6, shortText.length * 1.28 + 1.8) * invScale;
      const uh = 2.55 * invScale;
      return { cx: ux, cy: uy, w: uw + 0.6, h: uh + 0.6 };
    });
    const boxesOverlap = (a, b) =>
      Math.abs(a.cx - b.cx) * 2 < a.w + b.w &&
      Math.abs(a.cy - b.cy) * 2 < a.h + b.h;

    (ov.spot_groups || []).forEach((grp) => {
      if (grp.map_placed === false || !grp.spot_count) return;
      const gSpots = spotsToDraw.filter(
        (s) => (s.group_code || "") === grp.group_code
      );
      if (!gSpots.length) return;
      const gPts = gSpots.map((s) => [px(s.map_x), py(s.map_y)]);
      const baseCx = gPts.reduce((a, p) => a + p[0], 0) / gPts.length;
      const topY = Math.min(...gPts.map((p) => p[1])) - 2.45 * invScale;
      const botY = Math.max(...gPts.map((p) => p[1])) + 2.55 * invScale;
      const dispName = grp.group_name || grp.group_code;
      const shortDisp =
        dispName.length > 8 ? `${dispName.slice(0, 7)}…` : dispName;
      const isRecGrp = recGroupCode && grp.group_code === recGroupCode;
      const statusTxt = grp.is_bound ? `余${grp.effective_free}` : "待接入";
      const chipTxt = `${isRecGrp ? "★ " : ""}${shortDisp} · ${statusTxt}`;
      const bw = Math.max(7.2, chipTxt.length * 1.05 + 1.6) * invScale;
      const bh = 1.92 * invScale;

      const candidates = [
        [baseCx, topY],
        [baseCx, topY - 2.15 * invScale],
        [baseCx, botY],
        [baseCx - 3.6 * invScale, topY],
        [baseCx + 3.6 * invScale, topY],
        [baseCx, botY + 2.15 * invScale],
      ];
      let gcx = baseCx;
      let minGY = topY;
      for (const [candX, candY] of candidates) {
        const clampedX = Math.max(
          bw / 2 + 0.6,
          Math.min(worldW - bw / 2 - 0.6, candX)
        );
        const clampedY = Math.max(
          bh / 2 + 0.6,
          Math.min(worldH - bh / 2 - 0.6, candY)
        );
        const box = { cx: clampedX, cy: clampedY, w: bw + 0.4, h: bh + 0.35 };
        if (!occupiedChipBoxes.some((ob) => boxesOverlap(box, ob))) {
          gcx = clampedX;
          minGY = clampedY;
          break;
        }
      }
      occupiedChipBoxes.push({ cx: gcx, cy: minGY, w: bw + 0.4, h: bh + 0.35 });
      const bgFill = isRecGrp
        ? "#0071E3"
        : grp.is_bound
        ? "rgba(15, 23, 42, 0.84)"
        : "rgba(100, 116, 139, 0.86)";
      const walkM =
        grp.selected_door_walk && grp.selected_door_walk.walk_meters != null
          ? ` · 距单元门约 ${grp.selected_door_walk.walk_meters}m`
          : "";
      const grpToast = `${dispName}：净余 ${grp.effective_free}/${grp.total_capacity} 位${walkM}`;

      html += `
        <g data-spot-toast="${escapeHtml(grpToast)}">
          <rect x="${(gcx - bw / 2).toFixed(2)}" y="${(minGY - bh / 2).toFixed(
            2
          )}" width="${bw.toFixed(2)}" height="${bh.toFixed(2)}" rx="${(
            bh / 2
          ).toFixed(
            2
          )}" fill="${bgFill}" stroke="rgba(255,255,255,0.88)" stroke-width="${(
            0.24 * invScale
          ).toFixed(2)}"/>
          <text x="${gcx.toFixed(2)}" y="${(minGY + 0.36 * invScale).toFixed(
            2
          )}" font-size="${(1.02 * invScale).toFixed(
            2
          )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeHtml(
            chipTxt
          )}</text>
        </g>
      `;

      if (Number(grp.soft_locked || 0) > 0) {
        const lockY = minGY - 2.2 * invScale;
        const pillW = 6.2 * invScale;
        const pillH = 1.9 * invScale;
        html += `
          <g pointer-events="none">
            <rect x="${(gcx - pillW / 2).toFixed(2)}" y="${(
              lockY -
              pillH / 2
            ).toFixed(2)}" width="${pillW.toFixed(2)}" height="${pillH.toFixed(
              2
            )}" rx="${(pillH / 2).toFixed(
              2
            )}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
              0.24 * invScale
            ).toFixed(2)}"/>
            <text x="${gcx.toFixed(2)}" y="${(lockY + 0.36 * invScale).toFixed(
              2
            )}" font-size="${(1.04 * invScale).toFixed(
              2
            )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">在途×${
              grp.soft_locked
            }</text>
          </g>
        `;
      }
    });

    // 7. Community Entrance Gate Pills
    for (const g of gates) {
      const gx = px(Number(g.x ?? 50));
      const gy = py(Number(g.y ?? 96));
      const labelStr = g.name || "出入口";
      const gw =
        Math.max(5.8, Math.min(11.5, labelStr.length * 1.65 + 2.2)) * invScale;
      const gh = 2.45 * invScale;
      html += `
        <g pointer-events="none">
          <rect x="${(gx - gw / 2).toFixed(2)}" y="${(gy - gh / 2).toFixed(
            2
          )}" width="${gw.toFixed(2)}" height="${gh.toFixed(2)}" rx="${(
            gh / 2
          ).toFixed(2)}"
                fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
                  0.3 * invScale
                ).toFixed(2)}"/>
          <text x="${gx.toFixed(2)}" y="${(gy + 0.46 * invScale).toFixed(
            2
          )}" font-size="${(1.32 * invScale).toFixed(
            2
          )}" fill="#FFFFFF" font-weight="600" text-anchor="middle">${escapeHtml(
            labelStr
          )}</text>
        </g>
      `;
    }

    // 8. Building Unit Badges & Entrance Door Pins (Tap to switch destination)
    for (const u of unitsToDraw) {
      const ux = px(u.map_x);
      const uy = py(u.map_y);
      const dx = px(u.door_x != null ? u.door_x : u.map_x);
      const dy = py(u.door_y != null ? u.door_y : u.map_y);
      const isHome = u.unit_id === state.unitId;
      const compactLabel = formatCompactUnitBadgeLabel(u, unitsToDraw);
      const shortText = `${isHome ? "★ " : ""}${compactLabel}`;
      const uw = Math.max(5.6, shortText.length * 1.28 + 1.8) * invScale;
      const uh = 2.55 * invScale;
      const doorDistPx = Math.hypot(dx - ux, dy - uy);

      if (doorDistPx > 0.35) {
        html += `
          <line x1="${ux.toFixed(2)}" y1="${uy.toFixed(2)}" x2="${dx.toFixed(
            2
          )}" y2="${dy.toFixed(2)}" stroke="${
            isHome ? "#0D9488" : "rgba(13, 148, 136, 0.62)"
          }" stroke-width="${((isHome ? 0.36 : 0.24) * invScale).toFixed(
            2
          )}" stroke-dasharray="${(0.65 * invScale).toFixed(2)},${(
            0.45 * invScale
          ).toFixed(2)}" pointer-events="none"/>
        `;
      }

      const doorR = (isHome ? 1.12 : 0.88) * invScale;
      html += `
        <g class="clickable-unit" data-unit-id="${escapeHtml(u.unit_id)}">
          <circle cx="${dx.toFixed(2)}" cy="${dy.toFixed(2)}" r="${doorR.toFixed(
            2
          )}" fill="${
            isHome ? "#0D9488" : "#14B8A6"
          }" stroke="#FFFFFF" stroke-width="${(0.28 * invScale).toFixed(2)}"/>
          <text x="${dx.toFixed(2)}" y="${(dy + 0.36 * invScale).toFixed(
            2
          )}" font-size="${(0.95 * invScale).toFixed(
            2
          )}" fill="#FFFFFF" text-anchor="middle" font-weight="800">门</text>
        </g>
        <g class="clickable-unit" data-unit-id="${escapeHtml(u.unit_id)}">
          <rect x="${(ux - uw / 2).toFixed(2)}" y="${(uy - uh / 2).toFixed(
            2
          )}" width="${uw.toFixed(2)}" height="${uh.toFixed(2)}" rx="${(
            0.85 * invScale
          ).toFixed(2)}"
                fill="${isHome ? "#0071E3" : "rgba(255, 255, 255, 0.95)"}"
                stroke="${
                  isHome ? "#FFFFFF" : "#475569"
                }" stroke-width="${((isHome ? 0.48 : 0.34) * invScale).toFixed(
                  2
                )}"/>
          <text x="${ux.toFixed(2)}" y="${(uy + 0.48 * invScale).toFixed(
            2
          )}" font-size="${(1.32 * invScale).toFixed(2)}" fill="${
            isHome ? "#FFFFFF" : "#0F172A"
          }" text-anchor="middle" font-weight="700">${escapeHtml(
            shortText
          )}</text>
        </g>
      `;
    }

    els.mapSvg.innerHTML = html;
  }

  // ---------------------------------------------------------------------------
  // UI Data Binding & Live Polling
  // ---------------------------------------------------------------------------

  function showMapToast(msg) {
    if (!msg) return;
    els.mapSpotToast.textContent = msg;
    els.mapSpotToast.hidden = false;
    if (state.toastTimer) clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
      els.mapSpotToast.hidden = true;
    }, 2800);
  }

  function populateUnitSelect(units, selectedUnitId) {
    if (!Array.isArray(units) || !units.length) return;
    const currentOpts = Array.from(els.unitSelect.options).map((o) => o.value);
    const nextIds = units.map((u) => u.unit_id);
    const sameList =
      currentOpts.length === nextIds.length &&
      currentOpts.every((id, idx) => id === nextIds[idx]);

    if (!sameList) {
      els.unitSelect.innerHTML = units
        .map(
          (u) =>
            `<option value="${escapeHtml(u.unit_id)}">${escapeHtml(
              u.name || u.unit_id
            )}</option>`
        )
        .join("");
    }
    if (selectedUnitId && nextIds.includes(selectedUnitId)) {
      els.unitSelect.value = selectedUnitId;
    }
  }

  async function ensureBackdropLoaded() {
    if (!state.showBackdrop || state.backdropDataUrl || state.backdropLoading) {
      return;
    }
    state.backdropLoading = true;
    try {
      const resp = await fetch(appendWorkspaceQuery("/api/mobile/backdrop"), {
        cache: "no-store",
        headers: mobileAuthHeaders(),
      });
      if (resp.ok) {
        const data = await resp.json();
        const rawB64 =
          (data && (data.backdrop_data_url || data.backdrop_jpeg_b64)) || "";
        if (data && data.has_backdrop && rawB64) {
          state.backdropDataUrl = rawB64.startsWith("data:")
            ? rawB64
            : `data:image/jpeg;base64,${rawB64}`;
          renderFloorPlanSvg();
        }
      }
    } catch (_err) {
      // Ignore backdrop fetch failure
    } finally {
      state.backdropLoading = false;
    }
  }

  function renderMobileTagFilterBar(ov) {
    if (!els.tagFilterBar) return;
    const tagList = Array.isArray(ov && ov.tag_availability) ? ov.tag_availability : [];
    const activeItems = tagList.filter(
      (t) => Number(t.total_spots || 0) > 0 || (state.tagFilter && t.code === state.tagFilter)
    );
    if (!activeItems.length && !state.tagFilter) {
      els.tagFilterBar.hidden = true;
      els.tagFilterBar.innerHTML = "";
      return;
    }
    const iconMap = {
      ev_charging: "⚡",
      rentable: "🤝",
      private: "🔒",
      large_friendly: "🚙",
      accessible: "♿",
      visitor: "访",
    };
    const totFree = Number(ov.total_effective_free || 0);
    let html = `<button type="button" class="m-tag-chip ${!state.tagFilter ? "is-active" : ""} ${totFree > 0 ? "has-free" : ""}" data-tag="">
      <span>全部</span><em>余${totFree}</em>
    </button>`;
    activeItems.forEach((it) => {
      const code = String(it.code || "");
      const ic = iconMap[code] || it.short_badge || "#";
      const freeN = Number(it.effective_free || 0);
      const totN = Number(it.total_spots || 0);
      const isOn = state.tagFilter === code;
      html += `<button type="button" class="m-tag-chip ${isOn ? "is-active" : ""} ${freeN > 0 ? "has-free" : ""}" data-tag="${escapeHtml(code)}">
        <span>${escapeHtml(ic)} ${escapeHtml(it.label || code)}</span>
        <em>余${freeN}/${totN}</em>
      </button>`;
    });
    els.tagFilterBar.innerHTML = html;
    els.tagFilterBar.hidden = false;
  }

  function renderGuidanceStrip(ov) {
    const rec = ov && ov.recommendation;
    if (!rec) return;
    const tagLbl = String(rec.tag_label || "").trim();

    if (!rec.has_recommendation || !rec.primary) {
      els.guidanceCard.classList.add("is-full");
      els.recBadgeTag.textContent = tagLbl ? `${tagLbl}暂满` : "车位暂满";
      els.recZoneName.textContent = tagLbl
        ? `当前「${tagLbl}」净余 0/${rec.tag_total_spots || 0} 位`
        : "小区地面公益车位暂满";
      els.recFreePill.textContent = "净余 0";
      els.recFreePill.hidden = true;

      let nearestWalkM = null;
      if (Array.isArray(ov.spot_groups)) {
        for (const g of ov.spot_groups) {
          const wm = g.selected_door_walk && g.selected_door_walk.walk_meters;
          if (wm != null && (nearestWalkM == null || wm < nearestWalkM)) {
            nearestWalkM = wm;
          }
        }
      }
      els.recWalkPill.textContent =
        nearestWalkM != null ? `最近 ${nearestWalkM}m` : "周边临停";
      els.recBackupText.textContent =
        rec.voice_text || "当前暂无可用空位，请留意周边临停导引";
      return;
    }

    els.guidanceCard.classList.remove("is-full");
    const primary = rec.primary;
    const freeCnt =
      primary.effective_free != null ? primary.effective_free : 0;
    const walkVal =
      primary.walk_meters != null
        ? primary.walk_meters
        : primary.walk_distance_m;
    const walkM = walkVal != null ? `${walkVal}m` : "--m";

    els.recBadgeTag.textContent = tagLbl ? tagLbl : "首选推荐";
    els.recZoneName.textContent =
      primary.broadcast_zone_name || primary.zone_name || primary.zone_id;
    els.recFreePill.hidden = false;
    els.recFreePill.textContent = `净余 ${freeCnt} 位`;
    els.recWalkPill.textContent = `步行 ${walkM}`;

    const b0 =
      rec.backup || (Array.isArray(rec.backups) ? rec.backups[0] : null);
    if (b0 && b0.zone_name) {
      const bWalk =
        b0.walk_meters != null ? b0.walk_meters : b0.walk_distance_m ?? "--";
      els.recBackupText.textContent = `备选：${b0.zone_name}（余 ${b0.effective_free} 位 · 步行 ${bWalk}m）`;
    } else {
      els.recBackupText.textContent = "轻触地图楼栋或门标可快速切换目的地";
    }
  }

  async function fetchOverview({ forceSpeak = false } = {}) {
    if (state.fetching) {
      // Never drop an explicit unit-switch or manual speak request while a background poll is in flight!
      if (!forceSpeak) return;
      if (state.fetchAbortCtrl) {
        try {
          state.fetchAbortCtrl.abort();
        } catch (_e) {}
      }
    }
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    state.fetchAbortCtrl = ctrl;
    state.fetching = true;
    try {
      const ovParams = new URLSearchParams();
      if (state.unitId) ovParams.set("unit_id", state.unitId);
      if (state.vehicleType && state.vehicleType !== "sedan") {
        ovParams.set("vehicle_type", state.vehicleType);
      }
      if (state.tagFilter) {
        ovParams.set("tag", state.tagFilter);
      }
      const baseOv = ovParams.toString()
        ? `/api/mobile/overview?${ovParams.toString()}`
        : "/api/mobile/overview";
      const resp = await fetch(appendWorkspaceQuery(baseOv), {
        cache: "no-store",
        headers: mobileAuthHeaders(),
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (resp.status === 401) {
        const authErr = await resp.json().catch(() => ({}));
        if (authErr.workspace_name) {
          els.communityName.textContent = authErr.workspace_name;
        }
        if (els.authCard) {
          els.authCard.hidden = false;
          if (els.authTitle) {
            els.authTitle.textContent = `🔐 请输入「${authErr.workspace_name || "当前场所"}」访问口令`;
          }
        }
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      if (els.authCard) els.authCard.hidden = true;
      const data = await resp.json();

      if (data.workspace && data.workspace.preferred_voice_lang != null) {
        state.preferredVoiceLang = data.workspace.preferred_voice_lang || "";
      }

      if (
        state.mobileVersion &&
        data.mobile_version &&
        data.mobile_version !== state.mobileVersion
      ) {
        window.location.reload();
        return;
      }
      if (data.mobile_version) {
        state.mobileVersion = data.mobile_version;
      }

      els.liveDot.classList.remove("is-offline");
      state.overview = data;
      if (data.community_name) {
        els.communityName.textContent = data.community_name;
      }

      const totalFree = Number(
        data.total_effective_free != null
          ? data.total_effective_free
          : (data.summary && data.summary.effective_free_spots) || 0
      );
      els.totalFreeNum.textContent = String(totalFree);
      els.totalFreePill.classList.toggle("is-full", totalFree <= 0);

      const totalEvSpots = Number(data.total_ev_spots || 0);
      const totalEvFree = Number(data.total_ev_free || 0);
      const totalRentable = Number(data.total_rentable_spots || 0);
      if (els.btnEvModeToggle) {
        const showEvBtn = totalEvSpots > 0 || state.vehicleType === "ev";
        els.btnEvModeToggle.hidden = !showEvBtn;
        const evOn = state.vehicleType === "ev";
        els.btnEvModeToggle.classList.toggle("is-active", evOn);
        els.btnEvModeToggle.setAttribute("aria-pressed", evOn ? "true" : "false");
        if (els.evModeLabel) {
          els.evModeLabel.textContent =
            totalEvSpots > 0 ? `充电(${totalEvFree})` : "寻桩";
        }
      }
      if (els.mobLegendEv) els.mobLegendEv.hidden = totalEvSpots <= 0;
      if (els.mobLegendRent) els.mobLegendRent.hidden = totalRentable <= 0;

      const resolvedUnit = data.selected_unit_id || state.unitId;
      if (resolvedUnit && resolvedUnit !== state.unitId) {
        state.unitId = resolvedUnit;
        persistSettings();
      }
      syncUrlUnitParam(state.unitId);
      populateUnitSelect(data.units || [], state.unitId);

      const hasBackdrop = Boolean(data.site_map && data.site_map.has_backdrop);
      if (hasBackdrop && !state.backdropPreferenceSaved) {
        state.showBackdrop = true;
      }
      els.btnBackdropToggle.hidden = !hasBackdrop;
      els.btnBackdropToggle.classList.toggle(
        "is-active",
        hasBackdrop && state.showBackdrop
      );
      if (hasBackdrop && state.showBackdrop) {
        ensureBackdropLoaded();
      }

      renderMobileTagFilterBar(data);
      renderGuidanceStrip(data);
      renderMobileAdSlot(data);
      renderFloorPlanSvg();

      const rec = data.recommendation;
      const voiceText = (rec && rec.voice_text) || "";
      state.latestVoiceText = voiceText;
      state.latestRepeatText = (rec && rec.repeat_voice_text) || "";
      const newSig = buildRecommendationSignature(rec, state.unitId);
      const wasInitialLoad = !state.lastAnnouncedSig;
      const sigChanged = Boolean(newSig && newSig !== state.lastAnnouncedSig);
      state.lastAnnouncedSig = newSig;

      if (state.repeatCount > 0 && voiceText) {
        if (forceSpeak) {
          state.sessionMutedByQuery = false;
          speakBroadcast(voiceText, state.repeatCount, "manual");
        } else if (state.continuousBroadcast && wasInitialLoad && !state.sessionMutedByQuery) {
          speakBroadcast(voiceText, state.repeatCount, "initial");
        } else if (state.continuousBroadcast && !state.sessionMutedByQuery && sigChanged) {
          speakBroadcast(voiceText, state.repeatCount, "change");
        }
      }
    } catch (err) {
      if (err && err.name === "AbortError") return;
      els.liveDot.classList.add("is-offline");
    } finally {
      if (state.fetchAbortCtrl === ctrl) {
        state.fetching = false;
        state.fetchAbortCtrl = null;
      }
    }
  }

  function changeDestination(nextUnitId, speakNow = true) {
    const cleanId = String(nextUnitId || "").trim();
    if (!cleanId) return;
    state.unitId = cleanId;
    els.unitSelect.value = cleanId;
    persistSettings();
    syncUrlUnitParam(cleanId);
    fetchOverview({ forceSpeak: speakNow && state.repeatCount > 0 });
  }

  // ---------------------------------------------------------------------------
  // Touch Pan / Pinch-to-Zoom / Tap Gesture Handling on Floor-Plan Stage
  // ---------------------------------------------------------------------------

  function bindMapGestures() {
    const activePointers = new Map();
    let gestureStartDist = 0;
    let gestureStartZoom = 1;
    let lastSinglePt = null;
    let tapStartPt = null;
    let maxMovePx = 0;

    const getSvgRect = () => els.mapSvg.getBoundingClientRect();

    els.mapSvg.addEventListener("pointerdown", (ev) => {
      activePointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      try {
        els.mapSvg.setPointerCapture(ev.pointerId);
      } catch (_e) {
        // Ignore
      }

      if (activePointers.size === 1) {
        lastSinglePt = { x: ev.clientX, y: ev.clientY };
        tapStartPt = {
          x: ev.clientX,
          y: ev.clientY,
          target: ev.target,
        };
        maxMovePx = 0;
      } else if (activePointers.size === 2) {
        const pts = Array.from(activePointers.values());
        gestureStartDist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
        gestureStartZoom = state.zoom;
        tapStartPt = null;
      }
    });

    els.mapSvg.addEventListener("pointermove", (ev) => {
      if (!activePointers.has(ev.pointerId)) return;
      activePointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });

      if (activePointers.size === 1 && lastSinglePt) {
        const dxPx = ev.clientX - lastSinglePt.x;
        const dyPx = ev.clientY - lastSinglePt.y;
        lastSinglePt = { x: ev.clientX, y: ev.clientY };
        if (tapStartPt) {
          maxMovePx = Math.max(
            maxMovePx,
            Math.hypot(ev.clientX - tapStartPt.x, ev.clientY - tapStartPt.y)
          );
        }
        if (state.zoom > 1.005 && (Math.abs(dxPx) > 0.5 || Math.abs(dyPx) > 0.5)) {
          const rect = getSvgRect();
          if (rect.width > 10 && rect.height > 10) {
            const vw = state.worldW / state.zoom;
            const vh = state.worldH / state.zoom;
            state.panX -= (dxPx / rect.width) * vw;
            state.panY -= (dyPx / rect.height) * vh;
            applySvgViewBox();
          }
        }
      } else if (activePointers.size === 2 && gestureStartDist > 12) {
        const pts = Array.from(activePointers.values());
        const dist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
        const midX = (pts[0].x + pts[1].x) / 2;
        const midY = (pts[0].y + pts[1].y) / 2;
        const rect = getSvgRect();
        const rx =
          rect.width > 0
            ? Math.max(0, Math.min(1, (midX - rect.left) / rect.width))
            : 0.5;
        const ry =
          rect.height > 0
            ? Math.max(0, Math.min(1, (midY - rect.top) / rect.height))
            : 0.5;
        const nextZoom = gestureStartZoom * (dist / gestureStartDist);
        zoomAroundRatio(nextZoom, rx, ry);
      }
    });

    const finishPointer = (ev) => {
      const wasSingleTap =
        activePointers.size === 1 && tapStartPt && maxMovePx < 9;
      const tapOriginTarget = tapStartPt ? tapStartPt.target : null;
      activePointers.delete(ev.pointerId);

      if (activePointers.size === 1) {
        const remaining = Array.from(activePointers.values())[0];
        lastSinglePt = { x: remaining.x, y: remaining.y };
      } else if (activePointers.size === 0) {
        lastSinglePt = null;
      }

      if (wasSingleTap && tapOriginTarget && tapOriginTarget.closest) {
        const unitNode = tapOriginTarget.closest("[data-unit-id]");
        if (unitNode) {
          const uid = unitNode.getAttribute("data-unit-id");
          if (uid) {
            showMapToast(`🏠 已切换目的地：${uid}`);
            changeDestination(uid, true);
          }
          tapStartPt = null;
          return;
        }
        const spotNode = tapOriginTarget.closest("[data-spot-id]");
        if (spotNode) {
          const sid = spotNode.getAttribute("data-spot-id");
          if (sid && openMobileSpotSheetIfRich(sid)) {
            tapStartPt = null;
            return;
          }
        }
        const toastNode = tapOriginTarget.closest("[data-spot-toast]");
        if (toastNode) {
          if (els.spotSheet) els.spotSheet.hidden = true;
          showMapToast(toastNode.getAttribute("data-spot-toast"));
        }
      }
      tapStartPt = null;
    };

    els.mapSvg.addEventListener("pointerup", finishPointer);
    els.mapSvg.addEventListener("pointercancel", finishPointer);

    // Desktop wheel zoom convenience
    els.mapSvg.addEventListener(
      "wheel",
      (ev) => {
        ev.preventDefault();
        const rect = getSvgRect();
        const rx =
          rect.width > 0
            ? Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width))
            : 0.5;
        const ry =
          rect.height > 0
            ? Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height))
            : 0.5;
        const factor = ev.deltaY < 0 ? 1.18 : 1 / 1.18;
        zoomAroundRatio(state.zoom * factor, rx, ry);
      },
      { passive: false }
    );
  }

  // ---------------------------------------------------------------------------
  // Control Events & Lifecycle
  // ---------------------------------------------------------------------------

  function openMobileSpotSheetIfRich(spotId) {
    if (!els.spotSheet || !state.overview) return false;
    let found = null;
    for (const z of state.overview.zones || []) {
      for (const sp of z.spots || []) {
        if (sp.spot_id === spotId) {
          found = { ...sp, zone_name: sp.zone_name || z.name };
          break;
        }
      }
      if (found) break;
    }
    if (!found) return false;
    const tags = Array.isArray(found.tags) ? found.tags : [];
    const meta = found.tag_meta || {};
    const hasRichTags =
      Boolean(found.is_ev || found.is_rentable || found.is_private_reserved) ||
      tags.length > 0;
    if (!hasRichTags) return false;

    const effSt = found.effective_status || found.status || "FREE";
    const stCn =
      found.camera_id === "UNASSIGNED"
        ? "待接入"
        : effSt === "FREE"
        ? "空闲可停"
        : effSt === "OCCUPIED"
        ? "已停车辆"
        : effSt === "BLOCKED"
        ? "占道禁停"
        : "待刷新";

    if (els.mobSheetZone) {
      els.mobSheetZone.textContent = found.group_name || found.zone_name || "小区地面车位";
    }
    if (els.mobSheetTitle) {
      els.mobSheetTitle.textContent = `${found.label} 号车位`;
    }
    if (els.mobSheetStatus) {
      els.mobSheetStatus.textContent = stCn;
      els.mobSheetStatus.className = `m-ss-status${effSt !== "FREE" ? " is-occ" : ""}`;
    }

    const catMap = {
      ev_charging: { label: "⚡ 新能源充电", cls: "is-ev" },
      rentable: { label: "🤝 对外招租/共享", cls: "is-rent" },
      private: { label: "🔒 产权专属位", cls: "" },
      large_friendly: { label: "🚙 宽体大车位", cls: "" },
      accessible: { label: "♿ 无障碍车位", cls: "" },
      visitor: { label: "访客临停", cls: "" },
    };
    const pillList = tags.length ? tags : found.is_ev ? ["ev_charging"] : [];
    if (els.mobSheetTags) {
      els.mobSheetTags.innerHTML = pillList
        .map((t) => {
          const info = catMap[t] || { label: `#${t}`, cls: "" };
          return `<span class="m-ss-tag-pill ${info.cls}">${escapeHtml(info.label)}</span>`;
        })
        .join("");
    }

    const detailLines = [];
    if (meta.charger_power) {
      detailLines.push(`<div>⚡ 充电规格：<strong>${escapeHtml(meta.charger_power)}</strong></div>`);
    }
    if (meta.rental_window || meta.rental_price) {
      const rp = [meta.rental_window, meta.rental_price].filter(Boolean).join(" · ");
      detailLines.push(`<div>🤝 共享招租：<strong>${escapeHtml(rp)}</strong></div>`);
    }
    if (meta.rental_contact) {
      const telMatch = String(meta.rental_contact).match(/(\+?\d[\d-]{6,14}\d)/);
      const actionHtml = telMatch
        ? `<a class="m-ss-action-btn" href="tel:${escapeHtml(telMatch[1].replace(/-/g, ""))}">拨号咨询</a>`
        : `<button type="button" class="m-ss-action-btn" data-copy-contact="${escapeHtml(meta.rental_contact)}">复制联系</button>`;
      detailLines.push(
        `<div class="m-ss-contact-row"><span>联系办理：${escapeHtml(meta.rental_contact)}</span>${actionHtml}</div>`
      );
    }
    if (els.mobSheetDetails) {
      els.mobSheetDetails.innerHTML = detailLines.join("");
    }
    if (els.mapSpotToast) els.mapSpotToast.hidden = true;
    els.spotSheet.hidden = false;
    return true;
  }

  function markUserGesture(ev) {
    const wasBlocked = state.speechBlockedByAutoplay;
    state.userInteracted = true;
    requestWakeLockIfNeeded();
    // Do not auto-trigger on pointerdown if the user is clicking a broadcast/unit control
    // whose own click/change handler will immediately speak (prevents start-then-cancel race!).
    const targetEl = ev && ev.target && ev.target.closest
      ? ev.target.closest("#btnSpeakAction, #btnEvModeToggle, #mobileUnitSelect, #repeatModeGroup, #btnContinuousToggle, #mobileVoiceSelect")
      : null;
    if (
      !targetEl &&
      wasBlocked &&
      !state.sessionMutedByQuery &&
      state.repeatCount > 0 &&
      state.latestVoiceText &&
      !state.isSpeaking
    ) {
      state.speechBlockedByAutoplay = false;
      speakBroadcast(state.latestVoiceText, state.repeatCount, "unlock");
    }
  }

  function bindControls() {
    document.addEventListener("pointerdown", markUserGesture, {
      passive: true,
    });

    els.unitSelect.addEventListener("change", () => {
      state.userInteracted = true;
      changeDestination(els.unitSelect.value, true);
    });

    if (els.btnEvModeToggle) {
      els.btnEvModeToggle.addEventListener("click", () => {
        state.userInteracted = true;
        const turningOn = state.vehicleType !== "ev";
        state.vehicleType = turningOn ? "ev" : "sedan";
        if (turningOn) state.tagFilter = "ev_charging";
        else if (state.tagFilter === "ev_charging") state.tagFilter = "";
        persistSettings();
        fetchOverview({ forceSpeak: state.continuousBroadcast });
      });
    }

    if (els.tagFilterBar) {
      els.tagFilterBar.addEventListener("click", (ev) => {
        const chip = ev.target.closest(".m-tag-chip");
        if (!chip) return;
        state.userInteracted = true;
        const nextTag = chip.getAttribute("data-tag") || "";
        state.tagFilter = state.tagFilter === nextTag && nextTag ? "" : nextTag;
        if (state.tagFilter === "ev_charging") state.vehicleType = "ev";
        else if (!state.tagFilter && state.vehicleType === "ev") state.vehicleType = "sedan";
        persistSettings();
        fetchOverview({ forceSpeak: state.continuousBroadcast });
      });
    }

    if (els.btnCloseMobSpotSheet) {
      els.btnCloseMobSpotSheet.addEventListener("click", () => {
        if (els.spotSheet) els.spotSheet.hidden = true;
      });
    }
    if (els.spotSheet) {
      els.spotSheet.addEventListener("click", (ev) => {
        const copyBtn = ev.target.closest("[data-copy-contact]");
        if (!copyBtn) return;
        const txt = copyBtn.getAttribute("data-copy-contact") || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(txt).catch(() => {});
        }
        copyBtn.textContent = "已复制";
      });
    }

    if (els.voiceSelect) {
      els.voiceSelect.addEventListener("change", () => {
        state.userInteracted = true;
        state.voiceUri = els.voiceSelect.value || "";
        persistSettings();
        if (state.latestVoiceText) {
          const passes = state.repeatCount > 0 ? state.repeatCount : 1;
          speakBroadcast(state.latestVoiceText, passes, "voice-change");
        }
      });
    }

    els.btnSpeakAction.addEventListener("click", () => {
      state.userInteracted = true;
      requestWakeLockIfNeeded();
      if (state.isSpeaking) {
        stopBroadcast(false);
        return;
      }
      // Manual tap speaks even if currently muted (defaults to 1 pass when muted, else repeatCount)
      const passes = state.repeatCount > 0 ? state.repeatCount : 1;
      if (state.latestVoiceText) {
        speakBroadcast(state.latestVoiceText, passes, "button");
      } else {
        fetchOverview({ forceSpeak: true });
      }
    });

    els.repeatModeGroup.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".m-seg-btn");
      if (!btn) return;
      state.userInteracted = true;
      const nextRepeat = Number(btn.dataset.repeat) === 1 ? 1 : 2;
      state.repeatCount = nextRepeat;
      persistSettings();
      requestWakeLockIfNeeded();
      syncBroadcastControlUi();
      if (state.latestVoiceText) {
        speakBroadcast(state.latestVoiceText, nextRepeat, "mode-switch");
      }
    });

    els.btnContinuousToggle.addEventListener("click", () => {
      state.userInteracted = true;
      state.continuousBroadcast = !state.continuousBroadcast;
      persistSettings();
      requestWakeLockIfNeeded();
      if (!state.continuousBroadcast) {
        stopBroadcast(true);
      } else {
        syncBroadcastControlUi();
        if (state.latestVoiceText && !state.isSpeaking) {
          speakBroadcast(
            state.latestVoiceText,
            state.repeatCount,
            "continuous-enable"
          );
        }
      }
    });

    els.btnZoomIn.addEventListener("click", () => {
      zoomAroundRatio(state.zoom * 1.35, 0.5, 0.5);
    });
    els.btnZoomOut.addEventListener("click", () => {
      zoomAroundRatio(state.zoom / 1.35, 0.5, 0.5);
    });
    if (els.btnZoomReset) {
      els.btnZoomReset.addEventListener("click", () => {
        zoomAroundRatio(1.0, 0.5, 0.5);
      });
    }
    els.mapSvg.addEventListener("dblclick", () => {
      if (state.zoom > 1.02) {
        zoomAroundRatio(1.0, 0.5, 0.5);
      }
    });
    els.btnBackdropToggle.addEventListener("click", () => {
      state.showBackdrop = !state.showBackdrop;
      state.backdropPreferenceSaved = true;
      els.btnBackdropToggle.classList.toggle("is-active", state.showBackdrop);
      persistSettings();
      if (state.showBackdrop) {
        ensureBackdropLoaded();
      }
      renderFloorPlanSvg();
    });

    if (els.btnSaveToken) {
      const submitMobToken = () => {
        state.token = String((els.tokenInput && els.tokenInput.value) || "").trim();
        try {
          localStorage.setItem(STORAGE_KEYS.token, state.token);
        } catch (_e) {}
        fetchOverview({ forceSpeak: false });
      };
      els.btnSaveToken.addEventListener("click", submitMobToken);
      if (els.tokenInput) {
        els.tokenInput.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") {
            ev.preventDefault();
            submitMobToken();
          }
        });
      }
    }

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        clearContinuousTimer();
      } else {
        requestWakeLockIfNeeded();
        fetchOverview({ forceSpeak: false });
        scheduleNextContinuousCycle();
      }
    });

    if ("speechSynthesis" in window) {
      populateVoiceSelectOptions();
      if ("onvoiceschanged" in window.speechSynthesis) {
        window.speechSynthesis.onvoiceschanged = () => {
          populateVoiceSelectOptions();
        };
      }
    }
    bindMobileAdControls();
  }

  // ==================== Differentiated :18790 Ad Slot, IAB >=1s Viewability & Wallet Sheet ====================
  const adState = {
    items: [],
    activeIdx: 0,
    activeSig: "",
    impressedIds: new Set(),
    videoViewedIds: new Set(),
    viewTimer: null,
    observer: null,
    issuedCoupons: {},
    visitorFp: (() => {
      try {
        const k = "cp_mob_vfp_v1";
        let v = localStorage.getItem(k);
        if (!v) {
          v = "m_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
          localStorage.setItem(k, v);
        }
        return v;
      } catch (_e) {
        return "m_anon";
      }
    })(),
  };

  async function postMobileAdEvent(campaignId, eventType, extra = {}) {
    if (!campaignId) return null;
    try {
      const resp = await fetch(appendWorkspaceQuery("/api/public/ad-event"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaign_id: campaignId,
          event_type: eventType,
          surface: "18790_mobile",
          visitor_fp: adState.visitorFp,
          unit_id: state.unitId || "",
          vehicle_type: state.vehicleType || "sedan",
          ...extra,
        }),
      });
      return await resp.json();
    } catch (_e) {
      return null;
    }
  }

  function openCrossPlatformMerchantNav(ad) {
    const destName = String(ad.nav_name || ad.advertiser_name || ad.title || "").trim();
    const encName = encodeURIComponent(destName || "周边商户");
    const hasCoords = typeof ad.nav_lat === "number" && typeof ad.nav_lon === "number";
    const ua = navigator.userAgent || "";
    const isWeChat = /MicroMessenger/i.test(ua);
    const isIOS = /iPhone|iPad|iPod/i.test(ua);
    const isAndroid = /Android/i.test(ua);

    postMobileAdEvent(ad.campaign_id, "navigate");

    const amapWeb = hasCoords
      ? `https://uri.amap.com/marker?position=${ad.nav_lon},${ad.nav_lat}&name=${encName}&src=community_parking`
      : `https://uri.amap.com/search?keyword=${encName}&src=community_parking`;

    // Inside WeChat WebView, custom URL schemes are blocked — open mobile H5 map directly!
    if (isWeChat) {
      window.location.href = amapWeb;
      return;
    }
    if (isIOS) {
      const ll = hasCoords ? `&ll=${ad.nav_lat},${ad.nav_lon}` : "";
      window.location.href = `maps://?q=${encName}${ll}`;
      return;
    }
    if (isAndroid) {
      const scheme = hasCoords
        ? `androidamap://viewMap?sourceApplication=CommunityParking&poiname=${encName}&lat=${ad.nav_lat}&lon=${ad.nav_lon}&dev=0`
        : `androidamap://poi?sourceApplication=CommunityParking&keywords=${encName}&dev=0`;
      const t0 = Date.now();
      window.location.href = scheme;
      setTimeout(() => {
        if (!document.hidden && Date.now() - t0 < 2100) {
          window.open(amapWeb, "_blank", "noopener");
        }
      }, 1250);
      return;
    }
    window.open(amapWeb, "_blank", "noopener");
  }

  function observeAdViewability(slotEl, campaignId) {
    if (!slotEl || !campaignId || adState.impressedIds.has(campaignId)) return;
    if (adState.viewTimer) {
      clearTimeout(adState.viewTimer);
      adState.viewTimer = null;
    }
    if (adState.observer) {
      adState.observer.disconnect();
    }
    if (typeof IntersectionObserver === "undefined") {
      adState.impressedIds.add(campaignId);
      postMobileAdEvent(campaignId, "impression");
      return;
    }
    adState.observer = new IntersectionObserver(
      (entries) => {
        const ent = entries[0];
        if (ent && ent.isIntersecting && ent.intersectionRatio >= 0.5) {
          if (!adState.viewTimer) {
            adState.viewTimer = setTimeout(() => {
              if (!adState.impressedIds.has(campaignId) && !document.hidden) {
                adState.impressedIds.add(campaignId);
                postMobileAdEvent(campaignId, "impression");
              }
            }, 1000);
          }
        } else if (adState.viewTimer) {
          clearTimeout(adState.viewTimer);
          adState.viewTimer = null;
        }
      },
      { threshold: [0.5] }
    );
    adState.observer.observe(slotEl);
  }

  function renderMobileAdSlot(data) {
    const slot = document.getElementById("mobileAdSlot");
    if (!slot) return;
    const adsObj = data && data.ads;
    const items = adsObj && adsObj.enabled && Array.isArray(adsObj.items) ? adsObj.items : [];
    if (!items.length) {
      slot.hidden = true;
      slot.innerHTML = "";
      adState.items = [];
      adState.activeSig = "";
      return;
    }
    adState.items = items;
    if (adState.activeIdx >= items.length) adState.activeIdx = 0;
    const cur = items[adState.activeIdx] || items[0];
    const sig = `${items.map((i) => i.campaign_id).join(",")}|${adState.activeIdx}|${cur.layout}|${cur.title}`;
    slot.hidden = false;
    if (sig === adState.activeSig) return;
    adState.activeSig = sig;

    const accent = cur.accent_color || "#0071E3";
    const badgeStyle = `background:${escapeHtml(accent)}1A;color:${escapeHtml(accent)}`;
    const offerPill = cur.offer_value
      ? `<span class="m-ad-offer-pill">${escapeHtml(cur.offer_value)}</span>`
      : "";
    const dotsHtml =
      items.length > 1
        ? `<span class="m-ad-dots">${items
            .map(
              (_, idx) =>
                `<button type="button" class="m-ad-dot ${idx === adState.activeIdx ? "is-active" : ""}" data-ad-idx="${idx}" aria-label="切换推荐 ${idx + 1}"></button>`
            )
            .join("")}</span>`
        : "";

    if (cur.layout === "capsule" && items.length === 1) {
      slot.innerHTML = `
        <button type="button" class="m-ad-capsule" data-open-ad="${escapeHtml(cur.campaign_id)}">
          <span class="m-ad-cap-left">
            <span class="m-ad-badge" style="${badgeStyle}">${escapeHtml(cur.badge_text || "业主礼遇")}</span>
            ${offerPill}
            <span class="m-ad-cap-title">${escapeHtml(cur.title)}</span>
          </span>
          <span class="m-ad-cap-cta" style="color:${escapeHtml(accent)}">${escapeHtml(cur.cta_text || "查看")} ›</span>
        </button>`;
    } else {
      let mediaHtml = "";
      if (cur.layout === "video" && cur.video_url) {
        mediaHtml = `<div class="m-ad-video-wrap">
          <video src="${escapeHtml(cur.video_url)}" autoplay muted loop playsinline webkit-playsinline preload="metadata" data-vid-cid="${escapeHtml(cur.campaign_id)}"></video>
        </div>`;
      } else if (cur.image_data_url || cur.hud_thumb_data_url) {
        mediaHtml = `<img class="m-ad-thumb" src="${escapeHtml(cur.image_data_url || cur.hud_thumb_data_url)}" alt="" />`;
      }
      slot.innerHTML = `
        <div class="m-ad-rich-card" data-open-ad="${escapeHtml(cur.campaign_id)}">
          ${mediaHtml}
          <div class="m-ad-rich-copy">
            <div style="display:flex;align-items:center;gap:5px;">
              <span class="m-ad-badge" style="${badgeStyle}">${escapeHtml(cur.badge_text || "业主礼遇")}</span>
              ${offerPill}
              ${cur.distance_hint ? `<span style="font-size:10.5px;color:#6e6e73;">${escapeHtml(cur.distance_hint)}</span>` : ""}
              ${dotsHtml}
            </div>
            <div class="m-ad-cap-title" style="margin-top:3px;">${escapeHtml(cur.title)}</div>
            ${cur.subtitle ? `<div class="m-ad-rich-sub">${escapeHtml(cur.subtitle)}</div>` : ""}
          </div>
          <span class="m-ad-cap-cta" style="color:${escapeHtml(accent)}">${escapeHtml(cur.cta_text || "详情")} ›</span>
        </div>`;

      const vid = slot.querySelector("video[data-vid-cid]");
      if (vid) {
        vid.play().catch(() => {});
        vid.addEventListener("timeupdate", () => {
          const vcid = vid.getAttribute("data-vid-cid");
          if (vid.currentTime >= 2.0 && vcid && !adState.videoViewedIds.has(vcid)) {
            adState.videoViewedIds.add(vcid);
            postMobileAdEvent(vcid, "video_view");
          }
        });
      }
    }

    observeAdViewability(slot, cur.campaign_id);
  }

  async function openMobileAdSheet(ad) {
    const sheet = document.getElementById("mobileAdSheet");
    const spotSheet = document.getElementById("mobileSpotSheet");
    if (!sheet || !ad) return;
    if (spotSheet) spotSheet.hidden = true;

    postMobileAdEvent(ad.campaign_id, "click");

    document.getElementById("mobAdSheetAdv").textContent =
      (ad.advertiser_name || "社区合作服务") + (ad.distance_hint ? ` · ${ad.distance_hint}` : "");
    document.getElementById("mobAdSheetTitle").textContent = ad.title || "";
    const offerEl = document.getElementById("mobAdSheetOffer");
    if (offerEl) {
      offerEl.hidden = !ad.offer_value;
      offerEl.textContent = ad.offer_value || "";
    }
    document.getElementById("mobAdSheetSub").textContent = ad.subtitle || ad.terms_note || "";

    const body = document.getElementById("mobAdSheetBody");
    const btns = [];
    if (ad.nav_name || ad.nav_address || ad.action_type === "map_nav") {
      btns.push(`<button type="button" class="m-ss-action-btn" data-ad-sheet-act="nav">🧭 地图导航前往</button>`);
    }
    if (ad.phone) {
      btns.push(`<a class="m-ss-action-btn m-ad-btn-ghost" href="tel:${escapeHtml(ad.phone)}" data-ad-sheet-act="call">📞 拨打电话</a>`);
    }
    if (ad.wechat_id) {
      btns.push(`<button type="button" class="m-ss-action-btn m-ad-btn-ghost" data-ad-sheet-act="copy-wx" data-wx="${escapeHtml(ad.wechat_id)}">💬 复制微信</button>`);
    }
    if (ad.action_type === "url" && ad.redirect_path) {
      const rHref = appendWorkspaceQuery(`${ad.redirect_path}?surface=18790_sheet`);
      btns.push(`<a class="m-ss-action-btn" href="${escapeHtml(rHref)}" target="_blank" rel="noopener">🔗 ${escapeHtml(ad.cta_text || "打开活动页")}</a>`);
    }

    let primaryBlock = "";
    if (ad.action_type === "coupon") {
      const cachedCode = adState.issuedCoupons[ad.campaign_id];
      primaryBlock = `
        <div class="m-wallet-ticket" id="mobWalletBox">
          <div>
            <div style="font-size:11px;color:#92400e;font-weight:600;">到店出示专属核销码即享优惠</div>
            <code id="mobCouponCodeTxt">${escapeHtml(cachedCode || "正在生成专属券码…")}</code>
          </div>
          <button type="button" class="m-ss-action-btn" data-ad-sheet-act="copy-code">复制券码</button>
        </div>`;
    } else if (ad.action_type === "lead_form") {
      primaryBlock = `
        <div class="m-lead-row">
          <input type="tel" id="mobLeadPhoneInput" class="m-lead-input" placeholder="输入手机号享专属报价" maxlength="18" />
          <button type="button" class="m-ss-action-btn" data-ad-sheet-act="submit-lead">${escapeHtml(ad.cta_text || "立即预约")}</button>
        </div>
        <div id="mobLeadFeedback" style="font-size:11.5px;color:#047857;font-weight:600;"></div>`;
    }

    body.innerHTML = `${primaryBlock}${btns.length ? `<div class="m-ad-btn-row">${btns.join("")}</div>` : ""}`;
    sheet.dataset.activeCid = ad.campaign_id;
    sheet.hidden = false;

    if (ad.action_type === "coupon" && !adState.issuedCoupons[ad.campaign_id]) {
      const res = await postMobileAdEvent(ad.campaign_id, "issue_coupon");
      const code = res && res.coupon && res.coupon.coupon_code ? res.coupon.coupon_code : "VIP-8888";
      adState.issuedCoupons[ad.campaign_id] = code;
      const codeEl = document.getElementById("mobCouponCodeTxt");
      if (codeEl) codeEl.textContent = code;
    }
  }

  function bindMobileAdControls() {
    const slot = document.getElementById("mobileAdSlot");
    const sheet = document.getElementById("mobileAdSheet");
    const closeBtn = document.getElementById("btnCloseMobAdSheet");
    if (closeBtn && sheet) {
      closeBtn.addEventListener("click", () => {
        sheet.hidden = true;
      });
    }
    if (slot) {
      slot.addEventListener("click", (ev) => {
        const dot = ev.target.closest("[data-ad-idx]");
        if (dot) {
          ev.stopPropagation();
          adState.activeIdx = Number(dot.getAttribute("data-ad-idx") || 0);
          adState.activeSig = "";
          renderMobileAdSlot(state.overview);
          return;
        }
        const card = ev.target.closest("[data-open-ad]");
        if (!card) return;
        const cid = card.getAttribute("data-open-ad");
        const ad = adState.items.find((i) => i.campaign_id === cid) || adState.items[0];
        if (ad) openMobileAdSheet(ad);
      });
    }
    if (sheet) {
      sheet.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("[data-ad-sheet-act]");
        if (!btn) return;
        const act = btn.getAttribute("data-ad-sheet-act");
        const cid = sheet.dataset.activeCid || "";
        const ad = adState.items.find((i) => i.campaign_id === cid);
        if (!ad) return;

        if (act === "nav") {
          openCrossPlatformMerchantNav(ad);
        } else if (act === "call") {
          postMobileAdEvent(cid, "call");
        } else if (act === "copy-wx") {
          const wx = btn.getAttribute("data-wx") || "";
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(wx).catch(() => {});
          }
          postMobileAdEvent(cid, "copy");
          btn.textContent = "✅ 已复制微信";
        } else if (act === "copy-code") {
          const code = document.getElementById("mobCouponCodeTxt")?.textContent || "";
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(code).catch(() => {});
          }
          btn.textContent = "✅ 已复制";
        } else if (act === "submit-lead") {
          const phone = String(document.getElementById("mobLeadPhoneInput")?.value || "").trim();
          const fb = document.getElementById("mobLeadFeedback");
          const res = await postMobileAdEvent(cid, "submit_lead", {
            lead: { phone, unit_id: state.unitId || "", vehicle_type: state.vehicleType || "sedan" },
          });
          if (fb) {
            fb.style.color = res && res.ok ? "#047857" : "#c81e1e";
            fb.textContent = res && res.ok ? "✅ 预约成功，专属顾问将尽快联系您" : (res?.error || "请输入有效手机号");
          }
        }
      });
    }
  }

  function init() {
    loadPersistedSettings();
    syncBroadcastControlUi();
    bindControls();
    bindMapGestures();
    fetchOverview({ forceSpeak: false });
    state.pollTimer = setInterval(() => {
      if (!document.hidden) {
        fetchOverview({ forceSpeak: false });
      }
    }, POLL_INTERVAL_MS);
  }

  init();
})();
