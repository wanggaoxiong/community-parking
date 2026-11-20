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

  const LS_UNIT = `cp_voice_${WS_KEY_PREFIX}unit_id`;
  const LS_VEHICLE = `cp_voice_${WS_KEY_PREFIX}vehicle`;
  const LS_REPEAT = `cp_voice_${WS_KEY_PREFIX}repeat`;
  const LS_TOKEN = `cp_voice_${WS_KEY_PREFIX}token`;
  const LS_VOICE_LANG = `cp_voice_${WS_KEY_PREFIX}voice_lang`;
  const LS_SC_LANG = `cp_voice_${WS_KEY_PREFIX}sc_voice_lang`;
  const LS_VOICE_URI = `cp_voice_${WS_KEY_PREFIX}tts_uri`;
  const LS_VOICE_URI_MAP = `cp_voice_${WS_KEY_PREFIX}tts_uri_map`;
  const LS_SC_NAME = `cp_voice_${WS_KEY_PREFIX}sc_name`;
  const LS_SC_UNIT_MODE = `cp_voice_${WS_KEY_PREFIX}sc_unit_mode_v2`;
  const LS_SC_ACTION = `cp_voice_${WS_KEY_PREFIX}sc_action`;
  const LS_SC_SHOW_MAP_LEGACY = `cp_voice_${WS_KEY_PREFIX}sc_show_map`;
  const LS_SC_POPUP_MODE = `cp_voice_${WS_KEY_PREFIX}sc_popup_mode`;
  const LS_SC_ARRIVAL_ON = `cp_voice_${WS_KEY_PREFIX}sc_arrival_on`;
  const LS_SC_ARRIVAL_PLACE = `cp_voice_${WS_KEY_PREFIX}sc_arrival_place`;
  const LS_SC_ARRIVAL_COORDS = `cp_voice_${WS_KEY_PREFIX}sc_arrival_coords`;
  const LS_SC_ARRIVAL_RADIUS = `cp_voice_${WS_KEY_PREFIX}sc_arrival_radius`;
  const LS_MAP_APP = `cp_voice_${WS_KEY_PREFIX}map_app`;
  const LS_DEVICE_UID = `cp_voice_device_uid`;
  const LS_CUSTOM_CHIPS = `cp_voice_${WS_KEY_PREFIX}custom_chips`;

  const MANUAL_OPT_VAL = "__manual__";

  function mintFreshShortcutBindUid() {
    return "sc_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  }

  function ensureDeviceUid() {
    return mintFreshShortcutBindUid();
  }

  function loadSavedPopupMode() {
    const saved = localStorage.getItem(LS_SC_POPUP_MODE) || "";
    if (saved === "text" || saved === "map" || saved === "both" || saved === "silent") {
      return saved;
    }
    return localStorage.getItem(LS_SC_SHOW_MAP_LEGACY) === "1" ? "both" : "text";
  }

  function loadSavedCustomChips() {
    try {
      const raw = JSON.parse(localStorage.getItem(LS_CUSTOM_CHIPS) || "[]");
      if (Array.isArray(raw)) {
        return raw.map((x) => String(x || "").trim()).filter(Boolean).slice(0, 12);
      }
    } catch (_e) {}
    return [];
  }

  function loadSavedArrivalCoords() {
    try {
      const raw = JSON.parse(localStorage.getItem(LS_SC_ARRIVAL_COORDS) || "null");
      if (raw && typeof raw.lat === "number" && typeof raw.lon === "number") {
        return raw;
      }
    } catch (_e) {}
    return null;
  }

  function loadSavedVoiceUriMap() {
    try {
      const raw = JSON.parse(localStorage.getItem(LS_VOICE_URI_MAP) || "{}");
      if (raw && typeof raw === "object") return raw;
    } catch (_e) {}
    return {};
  }

  const savedUnitMode = localStorage.getItem(LS_SC_UNIT_MODE) || "";
  const savedMapApp = localStorage.getItem(LS_MAP_APP) || "";
  const state = {
    workspaceId: WORKSPACE_ID,
    deviceUid: ensureDeviceUid(),
    preferredVoiceLang: "",
    voiceLanguages: [],
    voiceLang: localStorage.getItem(LS_VOICE_LANG) || "",
    shortcutLang: localStorage.getItem(LS_SC_LANG) || "",
    voiceUriByLang: loadSavedVoiceUriMap(),
    activePage: "live", // "live" | "ai" | "shortcut"
    generatingShortcut: false,
    token: "",
    units: [],
    unitId: localStorage.getItem(LS_UNIT) || "",
    vehicle: localStorage.getItem(LS_VEHICLE) || "sedan",
    repeat: parseInt(localStorage.getItem(LS_REPEAT) || "2", 10) === 1 ? 1 : 2,
    voiceUri: localStorage.getItem(LS_VOICE_URI) || "",
    shortcutName: localStorage.getItem(LS_SC_NAME) || "",
    unitMode: savedUnitMode === "menu" || savedUnitMode === "setup_once" || savedUnitMode === "fixed" ? savedUnitMode : "fixed",
    shortcutAction: localStorage.getItem(LS_SC_ACTION) || "brief",
    popupMode: loadSavedPopupMode(),
    arrivalEnabled: localStorage.getItem(LS_SC_ARRIVAL_ON) === "1",
    arrivalPlace: localStorage.getItem(LS_SC_ARRIVAL_PLACE) || "",
    arrivalCoords: loadSavedArrivalCoords(),
    arrivalRadius: localStorage.getItem(LS_SC_ARRIVAL_RADIUS) || "200",
    mapApp: ["amap", "apple", "baidu", "tencent"].includes(savedMapApp) ? savedMapApp : "amap",
    customChips: loadSavedCustomChips(),
    lastAskedQuestion: "",
    latestSingleText: "",
    latestRepeatText: "",
    latestSpeech: "",
    latestAnswer: "",
    speaking: false,
    activeAudio: null,
    speechSessionId: 0,
    speechPollTimer: null,
    speechWatchdogTimer: null,
    speechPassTimer: null,
    onlineFallbackTimer: null,
    placeSearchTimer: null,
    mapOverview: null,
    mapBackdropUrl: null,
    mapBackdropLoading: false,
    mapShowBackdrop: true,
    mapBackdropTouched: false,
    mapZoom: 1.0,
    mapPanX: 0,
    mapPanY: 0,
    mapWorldW: 145,
    mapWorldH: 100,
    mapToastTimer: null,
    recognition: null,
    recording: false,
    mobileUrl: "",
    icloudShortcutUrl: "",
    shortcutSignedReady: true,
    currentShortcutUrl: "",
    autostartRequested: false,
  };

  // Retain active SpeechSynthesisUtterance objects so WebKit/Safari GC never drops `onend`!
  const activeUtterancePool = new Set();

  const urlParams = new URLSearchParams(window.location.search);
  const queryTok = urlParams.get("token") || urlParams.get("k") || "";
  if (queryTok) {
    state.token = queryTok.trim();
    localStorage.setItem(LS_TOKEN, state.token);
  } else {
    state.token = localStorage.getItem(LS_TOKEN) || "";
  }
  if (urlParams.get("unit")) {
    state.unitId = urlParams.get("unit").trim();
    localStorage.setItem(LS_UNIT, state.unitId);
  }
  if (urlParams.get("lang") || urlParams.get("voice_lang")) {
    state.voiceLang = (urlParams.get("lang") || urlParams.get("voice_lang") || "").trim();
    if (state.voiceLang) localStorage.setItem(LS_VOICE_LANG, state.voiceLang);
  }
  if (urlParams.get("autostart") === "1") {
    state.autostartRequested = true;
  }

  const el = (id) => document.getElementById(id);

  function showToast(msg) {
    const t = el("vgToast");
    if (!t) return;
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => t.classList.add("hidden"), 2600);
  }

  function authHeaders(extra) {
    const h = Object.assign({}, extra || {});
    if (state.workspaceId && state.workspaceId !== "default") {
      h["X-Parking-Workspace"] = state.workspaceId;
    }
    if (state.token) {
      h["X-Parking-Token"] = state.token;
    }
    return h;
  }

  function applyWsParam(urlObj) {
    if (state.workspaceId && state.workspaceId !== "default") {
      urlObj.searchParams.set("ws", state.workspaceId);
    }
    return urlObj;
  }

  function resolveMobileViewerUrl(configuredUrl) {
    if (configuredUrl && configuredUrl.trim()) {
      return configuredUrl.trim();
    }
    const host = window.location.hostname || "127.0.0.1";
    if (/^voice\./i.test(host) && host.length > 6) {
      return `${window.location.protocol}//parking.${host.slice(6)}`;
    }
    return `${window.location.protocol}//${host}:18790`;
  }

  function sanitizeShortcutName(raw) {
    // Strip dots (`.` / `．`) so `<name>.shortcut` NEVER creates a double-extension filename on iOS/macOS!
    const cleaned = String(raw || "")
      .replace(/[\\/:*?"<>|.\r\n\t．]+/g, "")
      .trim();
    return (cleaned.slice(0, 32) || "小区车位").trim();
  }

  // ---------------------------------------------------------------------------
  // Instant Browser Built-in TTS (Default) + Optional Voice Timbre Selector
  // ---------------------------------------------------------------------------

  function classifyVoiceDialect(v) {
    const lang = String(v.lang || "").toLowerCase();
    const name = String(v.name || "");
    if (lang.startsWith("en") || /samantha|ava|allison|alex|daniel|karen/i.test(name)) {
      return { group: "english", tag: "English" };
    }
    if (
      lang.includes("hk") ||
      lang.includes("mo") ||
      lang.includes("yue") ||
      /sin[-_ ]?ji|善怡|hiumaan|hiugaai|wanlung|晓曼|曉曼|晓佳|曉佳|云龙|雲龍|粤|粵|cantonese/i.test(name)
    ) {
      return { group: "cantonese", tag: "粤语" };
    }
    if (/cnsc|sichuan|fangfang|盼盼|四川|川渝/i.test(`${lang} ${name}`)) {
      return { group: "dialect", tag: "四川话" };
    }
    if (/cnln|liaoning|dongmei|冬梅|晓北|东北/i.test(`${lang} ${name}`)) {
      return { group: "dialect", tag: "东北话" };
    }
    if (/cnsn|shaanxi|haohao|浩浩|晓妮|陕西/i.test(`${lang} ${name}`)) {
      return { group: "dialect", tag: "陕西话" };
    }
    if (lang.includes("tw") || /mei[-_ ]?jia|美佳|美嘉|hsiaochen|hsiaoyu|yunjhe|曉臻|曉雨|雲哲/i.test(name)) {
      return { group: "dialect", tag: "台湾腔" };
    }
    return { group: "mandarin", tag: "普通话" };
  }

  function voiceLangToBcp47Fallback(code) {
    const c = String(code || "zh-CN").trim();
    if (c === "zh-HK") return "zh-HK";
    if (c === "en-US") return "en-US";
    if (c === "zh-NE") return "zh-CN";
    if (c === "zh-SC") return "zh-CN";
    if (c === "zh-TW") return "zh-TW";
    return "zh-CN";
  }

  function isCloudOnlineVoice(v) {
    if (!v) return false;
    return /online|cloud|natural/i.test(`${v.name || ""} ${v.voiceURI || ""}`);
  }

  function normalizeVoiceBcp47Lang(v, fallbackLang) {
    if (v) {
      const info = classifyVoiceDialect(v);
      const raw = String(v.lang || "").trim().replace(/_/g, "-");
      if (info.group === "english") {
        return /^en/i.test(raw) ? raw : "en-US";
      }
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

  function doesVoiceMatchLangCode(v, langCode) {
    if (!v) return false;
    const info = classifyVoiceDialect(v);
    const code = String(langCode || "zh-CN").trim();
    if (code === "zh-HK") return info.group === "cantonese";
    if (code === "en-US") return info.group === "english";
    if (code === "zh-SC") return info.tag === "四川话";
    if (code === "zh-NE") return info.tag === "东北话";
    if (code === "zh-SN") return info.tag === "陕西话";
    if (code === "zh-TW") return info.tag === "台湾腔";
    return info.group === "mandarin";
  }

  function isCloudTtsReadyForLang(langCode) {
    const code = String(langCode || "").trim();
    const found = (state.voiceLanguages || []).find((x) => x.code === code);
    if (found && typeof found.cloud_tts_ready === "boolean") {
      return found.cloud_tts_ready;
    }
    return code === "zh-NE" || code === "zh-SN";
  }

  function hasLocalAppleDialectPack(langCode) {
    const code = String(langCode || "zh-CN").trim();
    if (!("speechSynthesis" in window)) return false;
    const voices = window.speechSynthesis.getVoices() || [];
    if (!voices.length) return false;
    return voices.some(
      (v) =>
        !isCloudOnlineVoice(v) &&
        !/microsoft/i.test(`${v.name || ""} ${v.voiceURI || ""}`) &&
        doesVoiceMatchLangCode(v, code)
    );
  }

  function hasBrowserVoiceForLang(langCode) {
    const code = String(langCode || "zh-CN").trim();
    if (!code || code === "zh-CN") return true;
    if (isCloudTtsReadyForLang(code)) return true;
    if (!("speechSynthesis" in window)) return false;
    const voices = window.speechSynthesis.getVoices() || [];
    if (!voices.length) return null; // still loading asynchronously
    return voices.some((v) => doesVoiceMatchLangCode(v, code));
  }

  function hasAppleOfflineShortcutVoiceForLang(langCode, popupModeOverride) {
    const code = String(langCode || "zh-CN").trim();
    const pmode = String(popupModeOverride || state.popupMode || "text").trim();
    if (code === "ask_once") return true;
    // Mandarin, Cantonese (Sinji), English (Samantha), and Taiwan Mandarin (Meijia) are pre-installed
    // and launch simultaneously (`0ms`) with `文字 + 平面图同屏`!
    if (!code || code === "zh-CN" || code === "zh-HK" || code === "en-US" || code === "zh-TW") {
      return true;
    }
    // In `silent` (pure voice, no popup) mode, `zh-NE` / `zh-SN` can play Cloud Neural `晓北`/`晓妮` without blocking any UI!
    if (pmode === "silent" && isCloudTtsReadyForLang(code)) {
      return true;
    }
    return hasLocalAppleDialectPack(code);
  }

  function getLangDisplayShortName(code) {
    const map = {
      "zh-CN": "普通话",
      "zh-HK": "粤语",
      "en-US": "English",
      "zh-SC": "四川话",
      "zh-NE": "东北话",
      "zh-SN": "陕西话",
      "zh-TW": "闽台国语",
    };
    return map[code] || code;
  }

  function formatVoiceLabel(v) {
    const info = classifyVoiceDialect(v);
    const rawName = String(v.name || v.voiceURI || "语音")
      .replace(/^Microsoft\s+/i, "")
      .replace(/\s*Online\s*\(Natural\)\s*-\s*(Chinese|English).*$/i, "")
      .replace(/\s*-\s*(Chinese|English).*$/i, "")
      .trim();
    const isOnline = isCloudOnlineVoice(v);
    return `${isOnline ? "✨" : "⚡"} ${rawName} (${isOnline ? "联网" : "本地"})`;
  }

  function getPinnedVoiceUriForLang(langCode) {
    const code = String(langCode || state.voiceLang || "zh-CN").trim();
    return String((state.voiceUriByLang && state.voiceUriByLang[code]) || "").trim();
  }

  function setPinnedVoiceUriForLang(langCode, uri) {
    const code = String(langCode || state.voiceLang || "zh-CN").trim();
    if (!state.voiceUriByLang) state.voiceUriByLang = {};
    if (uri) {
      state.voiceUriByLang[code] = uri;
    } else {
      delete state.voiceUriByLang[code];
    }
    try {
      localStorage.setItem(LS_VOICE_URI_MAP, JSON.stringify(state.voiceUriByLang));
    } catch (_e) {}
  }

  function populateVoiceOptions() {
    const sel = el("vgVoiceSelect");
    if (!sel || !("speechSynthesis" in window)) return;
    const activeLang = String(state.voiceLang || state.preferredVoiceLang || "zh-CN").trim();
    const allVoices = window.speechSynthesis.getVoices() || [];
    // Strictly filter timbres to ONLY the currently selected language (`activeLang`) — ZERO cross-language conflict!
    const matchingVoices = allVoices.filter((v) => doesVoiceMatchLangCode(v, activeLang));
    matchingVoices.sort((a, b) => {
      const aCloud = isCloudOnlineVoice(a) ? 1 : 0;
      const bCloud = isCloudOnlineVoice(b) ? 1 : 0;
      if (aCloud !== bCloud) return aCloud - bCloud;
      const aLocal = a.localService !== false ? 0 : 1;
      const bLocal = b.localService !== false ? 0 : 1;
      if (aLocal !== bLocal) return aLocal - bLocal;
      return String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
    });

    const cur = getPinnedVoiceUriForLang(activeLang);
    sel.innerHTML = "";
    const langTag = getLangDisplayShortName(activeLang);
    const defOpt = document.createElement("option");
    defOpt.value = "";
    if (activeLang === "en-US") {
      defOpt.textContent = "🔊 Auto (English)";
    } else if (activeLang === "zh-NE" && !matchingVoices.length && isCloudTtsReadyForLang("zh-NE")) {
      defOpt.textContent = "✨ 晓北 · 东北原声";
    } else {
      defOpt.textContent = `🔊 自动优选 (${langTag})`;
    }
    sel.appendChild(defOpt);

    const seen = new Set();
    const seenLabels = new Set();
    matchingVoices.forEach((v) => {
      const uri = String(v.voiceURI || v.name || "").trim();
      if (!uri || seen.has(uri)) return;
      const lbl = formatVoiceLabel(v);
      if (seenLabels.has(lbl) && uri !== cur) return;
      seen.add(uri);
      seenLabels.add(lbl);
      const opt = document.createElement("option");
      opt.value = uri;
      opt.textContent = lbl;
      sel.appendChild(opt);
    });

    if (cur && seen.has(cur)) {
      sel.value = cur;
    } else {
      sel.value = "";
      if (cur && allVoices.length > 0) {
        setPinnedVoiceUriForLang(activeLang, "");
      }
    }
  }

  function resolveSelectedVoice(preferLocalFallbackForLang) {
    if (!("speechSynthesis" in window)) return null;
    const voices = window.speechSynthesis.getVoices() || [];
    const activeLang = String(state.voiceLang || state.preferredVoiceLang || "zh-CN").trim();
    if (preferLocalFallbackForLang) {
      return (
        voices.find(
          (v) => !isCloudOnlineVoice(v) && doesVoiceMatchLangCode(v, activeLang)
        ) || null
      );
    }
    const pinnedUri = getPinnedVoiceUriForLang(activeLang);
    if (pinnedUri) {
      const matches = voices.filter(
        (v) =>
          (v.voiceURI === pinnedUri || v.name === pinnedUri) &&
          doesVoiceMatchLangCode(v, activeLang)
      );
      if (matches.length) {
        return matches.find((v) => v.localService !== false) || matches[0];
      }
    }
    if (activeLang === "en-US") {
      return (
        voices.find((v) => !isCloudOnlineVoice(v) && /samantha/i.test(String(v.name || ""))) ||
        voices.find((v) => !isCloudOnlineVoice(v) && doesVoiceMatchLangCode(v, "en-US")) ||
        voices.find((v) => doesVoiceMatchLangCode(v, "en-US")) ||
        null
      );
    }
    if (activeLang === "zh-HK") {
      return (
        voices.find((v) => !isCloudOnlineVoice(v) && doesVoiceMatchLangCode(v, "zh-HK")) ||
        voices.find((v) => doesVoiceMatchLangCode(v, "zh-HK")) ||
        null
      );
    }
    if (activeLang === "zh-SC" || activeLang === "zh-NE" || activeLang === "zh-SN" || activeLang === "zh-TW") {
      return (
        voices.find((v) => !isCloudOnlineVoice(v) && doesVoiceMatchLangCode(v, activeLang)) ||
        voices.find((v) => doesVoiceMatchLangCode(v, activeLang)) ||
        null
      );
    }
    const localZh = voices.find(
      (v) => !isCloudOnlineVoice(v) && doesVoiceMatchLangCode(v, "zh-CN")
    );
    return localZh || voices.find((v) => doesVoiceMatchLangCode(v, "zh-CN")) || null;
  }

  function clearSpeechTimers() {
    if (state.onlineFallbackTimer) {
      clearTimeout(state.onlineFallbackTimer);
      state.onlineFallbackTimer = null;
    }
    if (state.speechPollTimer) {
      clearInterval(state.speechPollTimer);
      state.speechPollTimer = null;
    }
    if (state.speechWatchdogTimer) {
      clearTimeout(state.speechWatchdogTimer);
      state.speechWatchdogTimer = null;
    }
    if (state.speechPassTimer) {
      clearTimeout(state.speechPassTimer);
      state.speechPassTimer = null;
    }
  }

  function stopSpeaking() {
    state.speechSessionId += 1;
    clearSpeechTimers();
    activeUtterancePool.clear();
    if (state.activeAudio) {
      try {
        state.activeAudio.pause();
        state.activeAudio.src = "";
      } catch (_ea) {}
      state.activeAudio = null;
    }
    if ("speechSynthesis" in window) {
      try {
        window.speechSynthesis.cancel();
      } catch (_e) {}
    }
    state.speaking = false;
    updateSpeakBtnUI();
  }

  function speakPassList(passStrings, onDone) {
    const queue = (Array.isArray(passStrings) ? passStrings : [passStrings])
      .map((s) => String(s || "").trim())
      .filter(Boolean);
    if (!queue.length) {
      stopSpeaking();
      if (onDone) onDone();
      return;
    }

    const activeLangCode = String(state.voiceLang || state.preferredVoiceLang || "zh-CN").trim();
    const browserVoicesNow = "speechSynthesis" in window ? window.speechSynthesis.getVoices() || [] : [];
    const hasLocalDialectVoice = browserVoicesNow.some((v) => doesVoiceMatchLangCode(v, activeLangCode));

    // When user chooses `zh-NE` (东北话) / `zh-SN` on iPhone Safari/Chrome (no Edge Xiaobei in WebKit),
    // stream `/api/voice/tts.mp3` (`晓北` neural voice) directly via HTML5 Audio!
    if (
      (activeLangCode === "zh-NE" || activeLangCode === "zh-SN") &&
      !hasLocalDialectVoice &&
      isCloudTtsReadyForLang(activeLangCode) &&
      typeof Audio !== "undefined"
    ) {
      stopSpeaking();
      const sessionId = state.speechSessionId;
      state.speaking = true;
      updateSpeakBtnUI();

      const playAudioIdx = (idx) => {
        if (sessionId !== state.speechSessionId) return;
        if (idx >= queue.length) {
          state.activeAudio = null;
          state.speaking = false;
          updateSpeakBtnUI();
          if (onDone) onDone();
          return;
        }
        const u = applyWsParam(new URL("/api/voice/tts.mp3", window.location.origin));
        u.searchParams.set("lang", activeLangCode);
        u.searchParams.set("text", queue[idx]);
        if (state.token) u.searchParams.set("token", state.token);
        const audio = new Audio(u.toString());
        state.activeAudio = audio;
        const nextSeg = () => {
          if (sessionId !== state.speechSessionId) return;
          if (idx + 1 < queue.length) {
            state.speechPassTimer = setTimeout(() => playAudioIdx(idx + 1), 220);
          } else {
            state.activeAudio = null;
            state.speaking = false;
            updateSpeakBtnUI();
            if (onDone) onDone();
          }
        };
        audio.onended = nextSeg;
        audio.onerror = nextSeg;
        audio.play().catch(() => nextSeg());
      };
      playAudioIdx(0);
      return;
    }

    if (!("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") {
      showToast("当前浏览器不支持语音合成朗读");
      state.speaking = false;
      updateSpeakBtnUI();
      if (onDone) onDone();
      return;
    }

    // Cancel any existing queue & advance session ID synchronously inside user gesture!
    state.speechSessionId += 1;
    const sessionId = state.speechSessionId;
    clearSpeechTimers();
    activeUtterancePool.clear();
    try {
      if (
        window.speechSynthesis.speaking ||
        window.speechSynthesis.pending ||
        window.speechSynthesis.paused
      ) {
        window.speechSynthesis.cancel();
      }
      if (window.speechSynthesis.paused) {
        window.speechSynthesis.resume();
      }
    } catch (_e) {}

    state.speaking = true;
    updateSpeakBtnUI();

    const runIdx = (idx, forceLocalVoice) => {
      if (sessionId !== state.speechSessionId) return;
      if (idx >= queue.length) {
        clearSpeechTimers();
        activeUtterancePool.clear();
        state.speaking = false;
        updateSpeakBtnUI();
        if (onDone) onDone();
        return;
      }

      const segText = queue[idx];
      let started = false;
      let finished = false;
      let startIssuedAt = Date.now();

      const utter = new SpeechSynthesisUtterance(segText);
      activeUtterancePool.add(utter);

      const finishSegment = () => {
        if (finished || sessionId !== state.speechSessionId) return;
        finished = true;
        clearSpeechTimers();
        activeUtterancePool.delete(utter);
        if (idx + 1 < queue.length) {
          state.speechPassTimer = setTimeout(() => {
            runIdx(idx + 1, forceLocalVoice);
          }, 260);
        } else {
          state.speaking = false;
          updateSpeakBtnUI();
          if (onDone) onDone();
        }
      };

      utter.rate = 1.04;
      utter.pitch = 1.0;
      const primaryPick = resolveSelectedVoice(null);
      const fallbackBcp = voiceLangToBcp47Fallback(state.voiceLang || state.preferredVoiceLang || "zh-CN");
      const targetLang = normalizeVoiceBcp47Lang(primaryPick, fallbackBcp);
      const chosen = forceLocalVoice ? resolveSelectedVoice(targetLang) : primaryPick;
      if (chosen) {
        utter.voice = chosen;
        utter.lang = normalizeVoiceBcp47Lang(chosen, targetLang);
      } else {
        utter.lang = targetLang;
      }
      const isOnline = isCloudOnlineVoice(chosen);

      utter.onstart = () => {
        if (sessionId !== state.speechSessionId) return;
        started = true;
        if (state.onlineFallbackTimer) {
          clearTimeout(state.onlineFallbackTimer);
          state.onlineFallbackTimer = null;
        }
      };
      utter.onend = () => finishSegment();
      utter.onerror = (ev) => {
        if (sessionId !== state.speechSessionId) return;
        const code = String((ev && ev.error) || "").toLowerCase();
        if (code.includes("interrupted") || code.includes("canceled")) {
          return;
        }
        if (!started && !forceLocalVoice) {
          finished = true;
          clearSpeechTimers();
          activeUtterancePool.delete(utter);
          runIdx(idx, true);
          return;
        }
        finishSegment();
      };

      try {
        if (window.speechSynthesis.paused) {
          window.speechSynthesis.resume();
        }
        // Synchronous speak() preserves iOS Safari user-gesture audio permission!
        window.speechSynthesis.speak(utter);
      } catch (_err) {
        finishSegment();
        return;
      }

      // Layer 2: 220ms state poller — guarantees `⏹ 停止` auto-resets to `🔊 播报` even if WebKit drops `onend`!
      state.speechPollTimer = setInterval(() => {
        if (finished || sessionId !== state.speechSessionId) {
          clearInterval(state.speechPollTimer);
          state.speechPollTimer = null;
          return;
        }
        const synthBusy = Boolean(
          window.speechSynthesis.speaking || window.speechSynthesis.pending
        );
        if (window.speechSynthesis.speaking && !started) {
          started = true;
        }
        if (started && !synthBusy && Date.now() - startIssuedAt > 350) {
          finishSegment();
        } else if (!started && !synthBusy && Date.now() - startIssuedAt > 1800) {
          // Synthesizer failed to start (e.g. wedged WebKit queue or iOS silent autoplay block)
          if (!forceLocalVoice && chosen) {
            finished = true;
            clearSpeechTimers();
            activeUtterancePool.delete(utter);
            try {
              window.speechSynthesis.cancel();
            } catch (_e) {}
            runIdx(idx, true);
          } else {
            finishSegment();
          }
        }
      }, 220);

      // Layer 3: Cloud neural stall fallback
      if (isOnline && !forceLocalVoice) {
        state.onlineFallbackTimer = setTimeout(() => {
          if (
            !finished &&
            sessionId === state.speechSessionId &&
            !started &&
            !window.speechSynthesis.speaking
          ) {
            finished = true;
            clearSpeechTimers();
            activeUtterancePool.delete(utter);
            try {
              window.speechSynthesis.cancel();
            } catch (_e) {}
            runIdx(idx, true);
          }
        }, 2800);
      }

      // Layer 4: Hard max-duration safety watchdog so `⏹ 停止` can NEVER stick permanently
      const maxMs = Math.max(7500, segText.length * 360);
      state.speechWatchdogTimer = setTimeout(() => {
        if (!finished && sessionId === state.speechSessionId) {
          finishSegment();
        }
      }, maxMs);
    };

    runIdx(0, false);
  }

  function speakText(text, onDone) {
    speakPassList([text], onDone);
  }

  function speakCurrentBrief() {
    if (state.latestSingleText) {
      const combined =
        state.repeat >= 2 && state.latestRepeatText
          ? `${state.latestSingleText} 再为您重复播报一遍：${state.latestRepeatText}`
          : state.latestSingleText;
      // Single synchronous speak() at t=0ms preserves iOS Safari user-activation across both passes!
      speakPassList([combined]);
    } else if (state.latestSpeech) {
      speakPassList([state.latestSpeech]);
    }
  }

  function updateSpeakBtnUI() {
    const btn = el("vgSpeakBriefBtn");
    if (!btn) return;
    btn.classList.toggle("is-speaking", state.speaking);
    btn.textContent = state.speaking ? "⏹ 停止" : "🔊 播报";
  }

  function syncRepeatButtonsUI() {
    document.querySelectorAll("#vgRepeatSeg .vg-seg-btn").forEach((btn) => {
      const val = parseInt(btn.getAttribute("data-repeat") || "2", 10);
      const active = val === state.repeat;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-checked", active ? "true" : "false");
    });
  }

  function switchPage(page) {
    const target = page === "ai" || page === "shortcut" ? page : "live";
    state.activePage = target;
    document.querySelectorAll("#vgPageTabs .vg-page-tab").forEach((btn) => {
      const active = btn.getAttribute("data-page") === target;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    el("vgPageLive").classList.toggle("hidden", target !== "live");
    el("vgPageAi").classList.toggle("hidden", target !== "ai");
    el("vgPageShortcut").classList.toggle("hidden", target !== "shortcut");
  }

  // ---------------------------------------------------------------------------
  // Native In-DOM 18790-Parity Floor-Plan SVG Renderer & Page-Scrollable Gestures
  // ---------------------------------------------------------------------------

  function escapeSvgHtml(val) {
    return String(val == null ? "" : val)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showMapSpotToast(msg) {
    const t = el("vgMapSpotToast");
    if (!t || !msg) return;
    t.textContent = msg;
    t.classList.remove("hidden");
    if (state.mapToastTimer) clearTimeout(state.mapToastTimer);
    state.mapToastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
  }

  function getMapMetrics(smap) {
    const aspect = Math.max(0.45, Math.min(2.6, Number((smap && smap.aspect_ratio) || 1.45)));
    const worldW = aspect >= 1 ? +(100 * aspect).toFixed(3) : 100;
    const worldH = aspect >= 1 ? 100 : +(100 / aspect).toFixed(3);
    return {
      worldW,
      worldH,
      px: (v) => +((Number(v || 0) / 100) * worldW).toFixed(3),
      py: (v) => +((Number(v || 0) / 100) * worldH).toFixed(3),
    };
  }

  function applyVoiceMapViewBox() {
    const svg = el("vgMapSvg");
    const stage = el("vgMapStage");
    if (!svg) return;
    const z = Math.max(1, Math.min(4.5, Number(state.mapZoom || 1)));
    const vw = state.mapWorldW / z;
    const vh = state.mapWorldH / z;
    const maxX = Math.max(0, state.mapWorldW - vw);
    const maxY = Math.max(0, state.mapWorldH - vh);
    state.mapPanX = Math.max(0, Math.min(maxX, state.mapPanX));
    state.mapPanY = Math.max(0, Math.min(maxY, state.mapPanY));
    svg.setAttribute(
      "viewBox",
      `${state.mapPanX.toFixed(2)} ${state.mapPanY.toFixed(2)} ${vw.toFixed(2)} ${vh.toFixed(2)}`
    );
    // At 1x zoom, let single-finger vertical swipes natively scroll the page (`pan-y pinch-zoom`)!
    const ta = z > 1.01 ? "none" : "pan-y pinch-zoom";
    svg.style.touchAction = ta;
    if (stage) stage.style.touchAction = ta;
  }

  function zoomVoiceMapAround(newZoom, anchorRatioX = 0.5, anchorRatioY = 0.5) {
    const oldZ = Math.max(1, Math.min(4.5, Number(state.mapZoom || 1)));
    const targetZ = Math.max(1, Math.min(4.5, Number(newZoom || 1)));
    const oldVw = state.mapWorldW / oldZ;
    const oldVh = state.mapWorldH / oldZ;
    const focusX = state.mapPanX + oldVw * anchorRatioX;
    const focusY = state.mapPanY + oldVh * anchorRatioY;

    const newVw = state.mapWorldW / targetZ;
    const newVh = state.mapWorldH / targetZ;
    state.mapZoom = targetZ;
    if (targetZ <= 1.005) {
      state.mapZoom = 1.0;
      state.mapPanX = 0;
      state.mapPanY = 0;
    } else {
      state.mapPanX = focusX - newVw * anchorRatioX;
      state.mapPanY = focusY - newVh * anchorRatioY;
    }
    applyVoiceMapViewBox();
    renderVoiceMapSvg();
  }

  function formatUnitBadgeShort(u, allUnits) {
    const uid = String((u && u.unit_id) || "").trim();
    const m = uid.match(/^([A-Za-z0-9]+)[-_#](\d+)$/);
    if (!m) return `${uid}栋`;
    const bldg = m[1];
    const unitNo = Number(m[2]);
    const siblings = (allUnits || []).filter((x) => {
      const xm = String((x && x.unit_id) || "").match(/^([A-Za-z0-9]+)[-_#](\d+)$/);
      return xm && xm[1].toUpperCase() === bldg.toUpperCase();
    }).length;
    return siblings >= 2 || unitNo >= 2 ? `${bldg}栋${unitNo}单元` : `${bldg}栋`;
  }

  async function ensureMapBackdropLoaded() {
    if (!state.mapShowBackdrop || state.mapBackdropUrl || state.mapBackdropLoading) return;
    state.mapBackdropLoading = true;
    try {
      const u = applyWsParam(new URL("/api/voice/map-backdrop", window.location.origin));
      const res = await fetch(u.toString(), { headers: authHeaders(), cache: "no-store" });
      if (res.ok) {
        const d = await res.json();
        const raw = (d && d.backdrop_jpeg_b64) || "";
        if (d && d.has_backdrop && raw) {
          state.mapBackdropUrl = raw.startsWith("data:") ? raw : `data:image/jpeg;base64,${raw}`;
          renderVoiceMapSvg();
        }
      }
    } catch (_e) {
    } finally {
      state.mapBackdropLoading = false;
    }
  }

  function renderVoiceMapSvg() {
    const svg = el("vgMapSvg");
    const ov = state.mapOverview;
    if (!svg || !ov) return;
    const smap = ov.site_map || {};
    const { worldW, worldH, px, py } = getMapMetrics(smap);
    state.mapWorldW = worldW;
    state.mapWorldH = worldH;
    applyVoiceMapViewBox();

    const invScale = Math.pow(Math.max(1, state.mapZoom || 1), -0.52);
    const topo = smap.topology || {};
    const roads =
      topo.roads && topo.roads.length
        ? topo.roads
        : [{ road_id: "R_MAIN", points: [[8, 83], [88, 83], [88, 22], [12, 22]], width: 5.2 }];
    const walkways = topo.walkways || [];
    const gates =
      topo.gates && topo.gates.length
        ? topo.gates
        : [{ gate_id: "south", name: "南门入口", x: 47, y: 96.5 }];
    const greens = topo.greens || [];
    const unitsToDraw = Array.isArray(ov.units) ? ov.units : [];
    const rec = ov.recommendation || null;
    const recPrimary = rec && rec.has_recommendation ? rec.primary : null;
    const recZoneId = recPrimary ? recPrimary.zone_id : "";
    const recGroupCode = recPrimary ? recPrimary.group_code || "" : "";

    const spotsToDraw = [];
    (ov.zones || []).forEach((z) => {
      (z.spots || []).forEach((sp) => {
        if (sp.map_placed === false || sp.map_x == null || sp.map_y == null) return;
        spotsToDraw.push({
          ...sp,
          zone_id: sp.zone_id || z.zone_id,
          zone_name: sp.zone_name || z.name,
        });
      });
    });

    let html = `<rect x="0.25" y="0.25" width="${(worldW - 0.5).toFixed(2)}" height="${(
      worldH - 0.5
    ).toFixed(2)}" rx="2.6" fill="#F8FAFC" stroke="#E2E8F0" stroke-width="0.4"/>`;

    if (state.mapShowBackdrop && state.mapBackdropUrl) {
      const bgAlpha = Number((smap.meta && smap.meta.backdrop_alpha) || 0.58).toFixed(2);
      html += `<image href="${state.mapBackdropUrl}" x="0.25" y="0.25" width="${(
        worldW - 0.5
      ).toFixed(2)}" height="${(worldH - 0.5).toFixed(2)}" preserveAspectRatio="none" opacity="${bgAlpha}"/>`;
    } else {
      greens.forEach((gr) => {
        html += `<rect x="${px(gr.x)}" y="${py(gr.y)}" width="${px(gr.w)}" height="${py(
          gr.h
        )}" rx="2.2" fill="rgba(52, 199, 89, 0.09)" stroke="rgba(52, 199, 89, 0.20)" stroke-width="0.3"/>`;
      });
    }

    const renderChevrons = (ptsArr, color, scaleMul = 1.0, twoWay = false) => {
      let cHtml = "";
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
          cHtml += `<g transform="translate(${cx.toFixed(2)} ${cy.toFixed(2)}) rotate(${ang.toFixed(1)})">
            <polygon points="${(1.25 * s).toFixed(2)},0 ${(-0.85 * s).toFixed(2)},${(-0.85 * s).toFixed(
              2
            )} ${(-0.38 * s).toFixed(2)},0 ${(-0.85 * s).toFixed(2)},${(0.85 * s).toFixed(2)}" fill="${color}"/>
          </g>`;
          if (twoWay && segLen > 7.5) {
            const cx2 = x1 + dx * Math.min(0.86, t + 0.16);
            const cy2 = y1 + dy * Math.min(0.86, t + 0.16);
            cHtml += `<g transform="translate(${cx2.toFixed(2)} ${cy2.toFixed(2)}) rotate(${(
              ang + 180
            ).toFixed(1)})">
              <polygon points="${(1.05 * s).toFixed(2)},0 ${(-0.75 * s).toFixed(2)},${(
                -0.75 * s
              ).toFixed(2)} ${(-0.32 * s).toFixed(2)},0 ${(-0.75 * s).toFixed(2)},${(
                0.75 * s
              ).toFixed(2)}" fill="${color}" opacity="0.82"/>
            </g>`;
          }
          nextAt += stepWorld;
        }
        accum += segLen;
      }
      return cHtml;
    };

    roads.forEach((r) => {
      const ptsArr = (r.points || []).map(([rx, ry]) => [px(rx), py(ry)]);
      if (ptsArr.length < 2) return;
      const ptsStr = ptsArr.map(([x, y]) => `${x},${y}`).join(" ");
      const rw = Math.max(1.8, Number(r.width || 4.4));
      const isTwoWay = r.one_way === false || r.direction === "twoway";
      const roadStroke = state.mapShowBackdrop ? "rgba(148, 163, 184, 0.38)" : "#E2E8F0";
      const dashStroke = state.mapShowBackdrop ? "rgba(2, 132, 199, 0.62)" : "rgba(14, 165, 233, 0.45)";
      html += `<g pointer-events="none">
        <polyline points="${ptsStr}" fill="none" stroke="${roadStroke}" stroke-width="${rw}" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${ptsStr}" fill="none" stroke="${dashStroke}" stroke-width="0.40" stroke-dasharray="1.6,1.3"/>
        ${renderChevrons(ptsArr, "rgba(2, 132, 199, 0.80)", 0.92, isTwoWay)}
      </g>`;
    });

    walkways.forEach((wk) => {
      const ptsArr = (wk.points || []).map(([wx, wy]) => [px(wx), py(wy)]);
      if (ptsArr.length < 2) return;
      const ptsStr = ptsArr.map(([x, y]) => `${x},${y}`).join(" ");
      html += `<g pointer-events="none">
        <polyline points="${ptsStr}" fill="none" stroke="rgba(16, 185, 129, 0.16)" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${ptsStr}" fill="none" stroke="#10B981" stroke-width="0.46" stroke-dasharray="1.05,0.85" stroke-linecap="round" stroke-linejoin="round"/>
      </g>`;
    });

    const recRoute = rec && rec.recommended_route;
    if (recRoute && Array.isArray(recRoute.points) && recRoute.points.length >= 2) {
      const recPts = recRoute.points.map(([rx, ry]) => [px(rx), py(ry)]);
      const recStr = recPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
      const [sx0, sy0] = recPts[0];
      const gateLabel = recRoute.entry_gate_name ? `🚗 ${recRoute.entry_gate_name}行车引导` : "🚗 进场行车引导";
      const gPillW = Math.max(7.2, gateLabel.length * 1.12 + 1.6) * invScale;
      const gPillH = 1.92 * invScale;
      const gLblY = Math.max(gPillH * 0.7, sy0 - 1.55 * invScale);
      html += `<g pointer-events="none">
        <polyline points="${recStr}" fill="none" stroke="rgba(0, 113, 227, 0.22)" stroke-width="${(1.85 * invScale).toFixed(2)}" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${recStr}" fill="none" stroke="#0071E3" stroke-width="${(0.68 * invScale).toFixed(2)}" stroke-dasharray="${(1.35 * invScale).toFixed(2)},${(0.75 * invScale).toFixed(2)}" class="active-guidance-route" stroke-linecap="round" stroke-linejoin="round"/>
        ${renderChevrons(recPts, "#1D4ED8", 1.05 * invScale, false)}
        <circle cx="${sx0.toFixed(2)}" cy="${sy0.toFixed(2)}" r="${(0.78 * invScale).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(0.28 * invScale).toFixed(2)}"/>
        <rect x="${(sx0 - gPillW / 2).toFixed(2)}" y="${(gLblY - gPillH / 2).toFixed(2)}" width="${gPillW.toFixed(2)}" height="${gPillH.toFixed(2)}" rx="${(gPillH / 2).toFixed(2)}" fill="rgba(0, 113, 227, 0.92)" stroke="#FFFFFF" stroke-width="${(0.22 * invScale).toFixed(2)}"/>
        <text x="${sx0.toFixed(2)}" y="${(gLblY + 0.36 * invScale).toFixed(2)}" font-size="${(0.98 * invScale).toFixed(2)}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeSvgHtml(gateLabel)}</text>
      </g>`;
    }

    spotsToDraw.forEach((sp, idx) => {
      const sx = px(sp.map_x ?? 50);
      const sy = py(sp.map_y ?? 50);
      const w = Math.max(0.85, Number(sp.map_w || (sp.spot_type === "elastic_strip" ? 7.8 : 1.75)));
      const h = Math.max(0.85, Number(sp.map_h || (sp.spot_type === "elastic_strip" ? 2.05 : 3.25)));
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
      if (isUnassigned) fill = "#475569";

      const isRecZone =
        (recGroupCode ? sp.group_code === recGroupCode : Boolean(recZoneId && sp.zone_id === recZoneId)) &&
        effSt === "FREE" &&
        !isUnassigned;

      const statusCn = isUnassigned
        ? "待接入摄像头"
        : effSt === "FREE"
        ? "空闲可停"
        : effSt === "OCCUPIED"
        ? "已停车辆"
        : effSt === "BLOCKED"
        ? "占道禁停"
        : "待识别";
      const spotToast = `${sp.group_name || sp.zone_name || sp.zone_id}：${statusCn}`;
      const outerStroke = isUnassigned ? "#F59E0B" : "rgba(15, 23, 42, 0.36)";
      const outerDash = isUnassigned ? 'stroke-dasharray="0.65,0.4"' : "";

      html += `<g transform="translate(${sx.toFixed(2)} ${sy.toFixed(2)}) rotate(${angle.toFixed(1)})" data-spot-toast="${escapeSvgHtml(spotToast)}">`;
      if (isRecZone) {
        html += `<rect x="${(-w / 2 - 0.45).toFixed(2)}" y="${(-h / 2 - 0.45).toFixed(2)}" width="${(w + 0.9).toFixed(2)}" height="${(h + 0.9).toFixed(2)}" rx="0.65" fill="rgba(40, 205, 65, 0.28)" stroke="#28CD41" stroke-width="0.30"/>`;
      }
      html += `<rect x="${(-w / 2).toFixed(2)}" y="${(-h / 2).toFixed(2)}" width="${w.toFixed(2)}" height="${h.toFixed(2)}" rx="0.36" fill="${fill}" stroke="${outerStroke}" stroke-width="0.2" ${outerDash}/>
        <rect x="${(-w / 2 + 0.14).toFixed(2)}" y="${(-h / 2 + 0.14).toFixed(2)}" width="${Math.max(0.2, w - 0.28).toFixed(2)}" height="${Math.max(0.2, h - 0.28).toFixed(2)}" rx="0.26" fill="none" stroke="rgba(255,255,255,0.72)" stroke-width="0.14"/>`;

      const minDim = Math.min(w, h);
      if (minDim >= 1.1 && sp.spot_type !== "elastic_strip") {
        const mNum = String(sp.label || "").trim().match(/(\d{1,3})$/);
        const shortLbl = mNum ? mNum[1].padStart(2, "0") : String(idx + 1).padStart(2, "0");
        const fontSz = Math.min(1.18, Math.max(0.82, minDim * 0.56)).toFixed(2);
        html += `<g transform="rotate(${(-angle).toFixed(1)})"><text x="0" y="${(Number(fontSz) * 0.35).toFixed(2)}" font-size="${fontSz}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeSvgHtml(shortLbl)}</text></g>`;
      }
      html += `</g>`;
    });

    let walkObj =
      rec && rec.door_walk_path && rec.door_walk_path.unit_id === state.unitId
        ? rec.door_walk_path
        : null;
    if (!walkObj && state.unitId && Array.isArray(ov.spot_groups)) {
      const pref = ov.spot_groups
        .filter(
          (g) =>
            g.map_placed !== false &&
            g.selected_door_walk &&
            Array.isArray(g.selected_door_walk.path_points) &&
            g.selected_door_walk.path_points.length >= 2
        )
        .sort((a, b) => {
          const af = Number(a.effective_free || 0) > 0 ? 0 : 1;
          const bf = Number(b.effective_free || 0) > 0 ? 0 : 1;
          if (af !== bf) return af - bf;
          return Number(a.selected_door_walk.walk_meters ?? 9999) - Number(b.selected_door_walk.walk_meters ?? 9999);
        })[0];
      if (pref) {
        walkObj = {
          unit_id: state.unitId,
          path_points: pref.selected_door_walk.path_points,
          walk_meters: pref.selected_door_walk.walk_meters,
        };
      }
    }

    if (walkObj && Array.isArray(walkObj.path_points) && walkObj.path_points.length >= 2) {
      const wPts = walkObj.path_points.map(([wx, wy]) => [px(wx), py(wy)]);
      const wStr = wPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
      let mxLbl = (wPts[0][0] + wPts[wPts.length - 1][0]) / 2;
      let myLbl = (wPts[0][1] + wPts[wPts.length - 1][1]) / 2 - 1.25 * invScale;
      const walkText = `🚶 距单元门 ${walkObj.walk_meters}m`;
      const pillW = Math.max(7.6, walkText.length * 1.15 + 1.8) * invScale;
      const pillH = 2.0 * invScale;
      html += `<g pointer-events="none">
        <polyline points="${wStr}" fill="none" stroke="rgba(13, 148, 136, 0.22)" stroke-width="${(1.45 * invScale).toFixed(2)}" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${wStr}" fill="none" stroke="#0D9488" stroke-width="${(0.52 * invScale).toFixed(2)}" stroke-dasharray="${(0.95 * invScale).toFixed(2)},${(0.65 * invScale).toFixed(2)}" class="active-walk-route" stroke-linecap="round" stroke-linejoin="round"/>
        ${renderChevrons(wPts, "#0F766E", 0.85 * invScale, false)}
        <rect x="${(mxLbl - pillW / 2).toFixed(2)}" y="${(myLbl - pillH / 2).toFixed(2)}" width="${pillW.toFixed(2)}" height="${pillH.toFixed(2)}" rx="${(pillH / 2).toFixed(2)}" fill="rgba(15, 118, 110, 0.94)" stroke="#FFFFFF" stroke-width="${(0.24 * invScale).toFixed(2)}"/>
        <text x="${mxLbl.toFixed(2)}" y="${(myLbl + 0.38 * invScale).toFixed(2)}" font-size="${(1.06 * invScale).toFixed(2)}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeSvgHtml(walkText)}</text>
      </g>`;
    }

    const occupiedBoxes = unitsToDraw.map((u) => {
      const ux = px(u.map_x);
      const uy = py(u.map_y);
      const shortText = `${u.unit_id === state.unitId ? "★ " : ""}${formatUnitBadgeShort(u, unitsToDraw)}`;
      return { cx: ux, cy: uy, w: Math.max(5.6, shortText.length * 1.28 + 1.8) * invScale + 0.6, h: 2.55 * invScale + 0.6 };
    });
    const overlaps = (a, b) => Math.abs(a.cx - b.cx) * 2 < a.w + b.w && Math.abs(a.cy - b.cy) * 2 < a.h + b.h;

    (ov.spot_groups || []).forEach((grp) => {
      if (grp.map_placed === false || !grp.spot_count) return;
      const gSpots = spotsToDraw.filter((s) => (s.group_code || "") === grp.group_code);
      if (!gSpots.length) return;
      const gPts = gSpots.map((s) => [px(s.map_x), py(s.map_y)]);
      const baseCx = gPts.reduce((a, p) => a + p[0], 0) / gPts.length;
      const topY = Math.min(...gPts.map((p) => p[1])) - 2.45 * invScale;
      const botY = Math.max(...gPts.map((p) => p[1])) + 2.55 * invScale;
      const dispName = grp.group_name || grp.group_code;
      const shortDisp = dispName.length > 8 ? `${dispName.slice(0, 7)}…` : dispName;
      const isRecGrp = recGroupCode && grp.group_code === recGroupCode;
      const chipTxt = `${isRecGrp ? "★ " : ""}${shortDisp} · ${grp.is_bound ? `余${grp.effective_free}` : "待接入"}`;
      const bw = Math.max(7.2, chipTxt.length * 1.05 + 1.6) * invScale;
      const bh = 1.92 * invScale;
      let gcx = baseCx;
      let minGY = topY;
      for (const [cxCand, cyCand] of [[baseCx, topY], [baseCx, topY - 2.15 * invScale], [baseCx, botY]]) {
        const clampedX = Math.max(bw / 2 + 0.6, Math.min(worldW - bw / 2 - 0.6, cxCand));
        const clampedY = Math.max(bh / 2 + 0.6, Math.min(worldH - bh / 2 - 0.6, cyCand));
        const box = { cx: clampedX, cy: clampedY, w: bw + 0.4, h: bh + 0.35 };
        if (!occupiedBoxes.some((ob) => overlaps(box, ob))) {
          gcx = clampedX;
          minGY = clampedY;
          break;
        }
      }
      occupiedBoxes.push({ cx: gcx, cy: minGY, w: bw + 0.4, h: bh + 0.35 });
      const bgFill = isRecGrp ? "#0071E3" : grp.is_bound ? "rgba(15, 23, 42, 0.84)" : "rgba(100, 116, 139, 0.86)";
      const grpToast = `${dispName}：剩余 ${grp.effective_free}/${grp.total_capacity} 个停车位`;
      html += `<g data-spot-toast="${escapeSvgHtml(grpToast)}">
        <rect x="${(gcx - bw / 2).toFixed(2)}" y="${(minGY - bh / 2).toFixed(2)}" width="${bw.toFixed(2)}" height="${bh.toFixed(2)}" rx="${(bh / 2).toFixed(2)}" fill="${bgFill}" stroke="rgba(255,255,255,0.88)" stroke-width="${(0.24 * invScale).toFixed(2)}"/>
        <text x="${gcx.toFixed(2)}" y="${(minGY + 0.36 * invScale).toFixed(2)}" font-size="${(1.02 * invScale).toFixed(2)}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeSvgHtml(chipTxt)}</text>
      </g>`;
    });

    gates.forEach((g) => {
      const gx = px(Number(g.x ?? 50));
      const gy = py(Number(g.y ?? 96));
      const labelStr = g.name || "出入口";
      const gw = Math.max(5.8, Math.min(11.5, labelStr.length * 1.65 + 2.2)) * invScale;
      const gh = 2.45 * invScale;
      html += `<g pointer-events="none">
        <rect x="${(gx - gw / 2).toFixed(2)}" y="${(gy - gh / 2).toFixed(2)}" width="${gw.toFixed(2)}" height="${gh.toFixed(2)}" rx="${(gh / 2).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(0.3 * invScale).toFixed(2)}"/>
        <text x="${gx.toFixed(2)}" y="${(gy + 0.46 * invScale).toFixed(2)}" font-size="${(1.32 * invScale).toFixed(2)}" fill="#FFFFFF" font-weight="600" text-anchor="middle">${escapeSvgHtml(labelStr)}</text>
      </g>`;
    });

    unitsToDraw.forEach((u) => {
      const ux = px(u.map_x);
      const uy = py(u.map_y);
      const dx = px(u.door_x != null ? u.door_x : u.map_x);
      const dy = py(u.door_y != null ? u.door_y : u.map_y);
      const isHome = u.unit_id === state.unitId;
      const shortText = `${isHome ? "★ " : ""}${formatUnitBadgeShort(u, unitsToDraw)}`;
      const uw = Math.max(5.6, shortText.length * 1.28 + 1.8) * invScale;
      const uh = 2.55 * invScale;
      const doorR = (isHome ? 1.12 : 0.88) * invScale;
      html += `<g class="clickable-unit" data-unit-id="${escapeSvgHtml(u.unit_id)}">
        <circle cx="${dx.toFixed(2)}" cy="${dy.toFixed(2)}" r="${doorR.toFixed(2)}" fill="${isHome ? "#0D9488" : "#14B8A6"}" stroke="#FFFFFF" stroke-width="${(0.28 * invScale).toFixed(2)}"/>
        <text x="${dx.toFixed(2)}" y="${(dy + 0.36 * invScale).toFixed(2)}" font-size="${(0.95 * invScale).toFixed(2)}" fill="#FFFFFF" text-anchor="middle" font-weight="800">门</text>
      </g>
      <g class="clickable-unit" data-unit-id="${escapeSvgHtml(u.unit_id)}">
        <rect x="${(ux - uw / 2).toFixed(2)}" y="${(uy - uh / 2).toFixed(2)}" width="${uw.toFixed(2)}" height="${uh.toFixed(2)}" rx="${(0.85 * invScale).toFixed(2)}" fill="${isHome ? "#0071E3" : "rgba(255, 255, 255, 0.95)"}" stroke="${isHome ? "#FFFFFF" : "#475569"}" stroke-width="${((isHome ? 0.48 : 0.34) * invScale).toFixed(2)}"/>
        <text x="${ux.toFixed(2)}" y="${(uy + 0.48 * invScale).toFixed(2)}" font-size="${(1.32 * invScale).toFixed(2)}" fill="${isHome ? "#FFFFFF" : "#0F172A"}" text-anchor="middle" font-weight="700">${escapeSvgHtml(shortText)}</text>
      </g>`;
    });

    svg.innerHTML = html;
  }

  async function refreshVoiceMap() {
    try {
      const u = applyWsParam(new URL("/api/voice/map-overview", window.location.origin));
      if (state.unitId) u.searchParams.set("unit", state.unitId);
      u.searchParams.set("vehicle", state.vehicle || "sedan");
      const res = await fetch(u.toString(), { headers: authHeaders(), cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      state.mapOverview = data;
      const hasBackdrop = Boolean(data.site_map && data.site_map.has_backdrop);
      if (hasBackdrop && !state.mapBackdropTouched) {
        state.mapShowBackdrop = true;
      }
      const bdBtn = el("vgBackdropBtn");
      if (bdBtn) {
        bdBtn.classList.toggle("hidden", !hasBackdrop);
        bdBtn.classList.toggle("is-active", hasBackdrop && state.mapShowBackdrop);
      }
      if (hasBackdrop && state.mapShowBackdrop) {
        ensureMapBackdropLoaded();
      }
      renderVoiceMapSvg();
    } catch (_e) {}
  }

  function bindVoiceMapGestures() {
    const svg = el("vgMapSvg");
    if (!svg) return;

    let pinchStartDist = 0;
    let pinchStartZoom = 1;
    let lastTouchPt = null;
    let mouseDragPt = null;

    // Touch gestures:
    // - At 1x zoom: 1-finger vertical swipe is NEVER intercepted -> smooth native page scroll!
    // - 2-finger pinch: zooms map smoothly!
    // - When zoomed (> 1.01x): 1-finger pans map, and still passes vertical scroll through at top/bottom map edges!
    svg.addEventListener(
      "touchstart",
      (ev) => {
        if (ev.touches.length === 2) {
          pinchStartDist = Math.hypot(
            ev.touches[1].clientX - ev.touches[0].clientX,
            ev.touches[1].clientY - ev.touches[0].clientY
          );
          pinchStartZoom = state.mapZoom;
          lastTouchPt = null;
        } else if (ev.touches.length === 1) {
          lastTouchPt = { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
        }
      },
      { passive: true }
    );

    svg.addEventListener(
      "touchmove",
      (ev) => {
        if (ev.touches.length === 2 && pinchStartDist > 12) {
          ev.preventDefault();
          const dist = Math.hypot(
            ev.touches[1].clientX - ev.touches[0].clientX,
            ev.touches[1].clientY - ev.touches[0].clientY
          );
          const midX = (ev.touches[0].clientX + ev.touches[1].clientX) / 2;
          const midY = (ev.touches[0].clientY + ev.touches[1].clientY) / 2;
          const rect = svg.getBoundingClientRect();
          const rx = rect.width > 0 ? Math.max(0, Math.min(1, (midX - rect.left) / rect.width)) : 0.5;
          const ry = rect.height > 0 ? Math.max(0, Math.min(1, (midY - rect.top) / rect.height)) : 0.5;
          zoomVoiceMapAround(pinchStartZoom * (dist / pinchStartDist), rx, ry);
          return;
        }
        if (ev.touches.length === 1 && lastTouchPt && state.mapZoom > 1.01) {
          const curX = ev.touches[0].clientX;
          const curY = ev.touches[0].clientY;
          const dxPx = curX - lastTouchPt.x;
          const dyPx = curY - lastTouchPt.y;
          lastTouchPt = { x: curX, y: curY };

          const vh = state.mapWorldH / state.mapZoom;
          const maxY = Math.max(0, state.mapWorldH - vh);
          const isVerticalSwipe = Math.abs(dyPx) > Math.abs(dxPx) * 1.15;
          const atTopPullingDown = dyPx > 0 && state.mapPanY <= 0.05;
          const atBotPushingUp = dyPx < 0 && state.mapPanY >= maxY - 0.05;
          if (isVerticalSwipe && (atTopPullingDown || atBotPushingUp)) {
            // Let the browser natively scroll the page when user reaches top/bottom edge of zoomed map!
            return;
          }

          ev.preventDefault();
          const rect = svg.getBoundingClientRect();
          if (rect.width > 10 && rect.height > 10) {
            const vw = state.mapWorldW / state.mapZoom;
            state.mapPanX -= (dxPx / rect.width) * vw;
            state.mapPanY -= (dyPx / rect.height) * vh;
            applyVoiceMapViewBox();
          }
        }
      },
      { passive: false }
    );

    svg.addEventListener(
      "touchend",
      (ev) => {
        if (ev.touches.length === 1) {
          lastTouchPt = { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
        } else if (ev.touches.length === 0) {
          lastTouchPt = null;
          pinchStartDist = 0;
        }
      },
      { passive: true }
    );

    // Desktop mouse pan when zoomed in
    svg.addEventListener("mousedown", (ev) => {
      if (state.mapZoom > 1.01) {
        mouseDragPt = { x: ev.clientX, y: ev.clientY };
      }
    });
    window.addEventListener("mousemove", (ev) => {
      if (!mouseDragPt || state.mapZoom <= 1.01) return;
      const dxPx = ev.clientX - mouseDragPt.x;
      const dyPx = ev.clientY - mouseDragPt.y;
      mouseDragPt = { x: ev.clientX, y: ev.clientY };
      const rect = svg.getBoundingClientRect();
      if (rect.width > 10 && rect.height > 10) {
        state.mapPanX -= (dxPx / rect.width) * (state.mapWorldW / state.mapZoom);
        state.mapPanY -= (dyPx / rect.height) * (state.mapWorldH / state.mapZoom);
        applyVoiceMapViewBox();
      }
    });
    window.addEventListener("mouseup", () => {
      mouseDragPt = null;
    });

    // Click / tap on building unit badge or parking stall
    svg.addEventListener("click", (ev) => {
      const unitNode = ev.target.closest && ev.target.closest("[data-unit-id]");
      if (unitNode) {
        const nextUid = unitNode.getAttribute("data-unit-id");
        if (nextUid) {
          state.unitId = nextUid;
          localStorage.setItem(LS_UNIT, state.unitId);
          populateUnitDropdown(state.units, state.unitId);
          syncShortcutBuilderUI();
          refreshBrief(true);
        }
        return;
      }
      const spotNode = ev.target.closest && ev.target.closest("[data-spot-toast]");
      if (spotNode) {
        showMapSpotToast(spotNode.getAttribute("data-spot-toast"));
      }
    });

    svg.addEventListener("dblclick", () => {
      if (state.mapZoom > 1.02) {
        zoomVoiceMapAround(1.0, 0.5, 0.5);
      }
    });

    el("vgZoomInBtn").addEventListener("click", () => {
      zoomVoiceMapAround(state.mapZoom * 1.35, 0.5, 0.5);
    });
    el("vgZoomOutBtn").addEventListener("click", () => {
      zoomVoiceMapAround(state.mapZoom / 1.35, 0.5, 0.5);
    });
    el("vgBackdropBtn").addEventListener("click", () => {
      state.mapShowBackdrop = !state.mapShowBackdrop;
      state.mapBackdropTouched = true;
      el("vgBackdropBtn").classList.toggle("is-active", state.mapShowBackdrop);
      if (state.mapShowBackdrop) ensureMapBackdropLoaded();
      renderVoiceMapSvg();
    });
  }

  function syncMobileLinks() {
    refreshVoiceMap();
  }

  function getSelectedUnitDisplayLabel() {
    const uid = state.unitId || "8-1";
    const found = (state.units || []).find((u) => u.unit_id === uid);
    return found ? `${found.name}` : uid;
  }

  function populateLanguageDropdowns(langs, activeWebCode, activeScCode) {
    const list =
      Array.isArray(langs) && langs.length
        ? langs
        : [
            { code: "zh-CN", short_label: "🎙️ 普通话", label: "普通话（标准播报）" },
            { code: "zh-HK", short_label: "🇭🇰 粤语", label: "粤语 · 广东话" },
            { code: "en-US", short_label: "🌐 English", label: "English（英文播报）" },
            { code: "zh-SC", short_label: "🌶️ 四川话", label: "四川话 · 川渝方言" },
            { code: "zh-NE", short_label: "❄️ 东北话", label: "东北话 · 东北方言" },
          ];
    const liveSel = el("vgLangSelect");
    const scSel = el("vgShortcutLangSelect");
    const chosenWeb = activeWebCode || state.voiceLang || "zh-CN";
    const chosenSc = activeScCode || state.shortcutLang || "zh-CN";

    if (liveSel) {
      liveSel.innerHTML = "";
      list.forEach((item) => {
        const opt = document.createElement("option");
        opt.value = item.code;
        const avail = hasBrowserVoiceForLang(item.code);
        const baseLbl = item.short_label || item.label || item.code;
        opt.textContent = avail === false ? `${baseLbl} (无语音包·自动普通话)` : baseLbl;
        liveSel.appendChild(opt);
      });
      if ([...liveSel.options].some((o) => o.value === chosenWeb)) {
        liveSel.value = chosenWeb;
      }
    }
    if (scSel) {
      scSel.disabled = false;
      scSel.innerHTML = "";
      if (state.unitMode === "setup_once") {
        const askOpt = document.createElement("option");
        askOpt.value = "ask_once";
        askOpt.textContent = "✨ 首次运行时弹窗选语言（推荐）";
        scSel.appendChild(askOpt);
      }
      list.forEach((item) => {
        const opt = document.createElement("option");
        opt.value = item.code;
        const scAvail = hasAppleOfflineShortcutVoiceForLang(item.code);
        const iconPrefix = (item.short_label || "").split(/\s+/)[0];
        const prefix = iconPrefix && !String(item.label || "").startsWith(iconPrefix) ? `${iconPrefix} ` : "";
        const baseScLbl = `${prefix}${item.label || item.short_label || item.code}`;
        opt.textContent = scAvail ? baseScLbl : `${baseScLbl}（未装离线包·自动普通话）`;
        scSel.appendChild(opt);
      });
      const targetScVal =
        state.unitMode !== "setup_once" && chosenSc === "ask_once"
          ? state.voiceLang || "zh-CN"
          : chosenSc;
      if ([...scSel.options].some((o) => o.value === targetScVal)) {
        scSel.value = targetScVal;
      } else {
        scSel.value = state.unitMode === "setup_once" ? "ask_once" : "zh-CN";
      }
    }
    if (state.recognition) {
      const matchProf = list.find((x) => x.code === chosenWeb);
      state.recognition.lang = (matchProf && matchProf.sr_lang) || voiceLangToBcp47Fallback(chosenWeb);
    }
  }

  function reconcileInstalledVoicePacks(showToastOnFallback) {
    let changedWeb = false;
    const curWeb = state.voiceLang || "zh-CN";
    const webAvail = hasBrowserVoiceForLang(curWeb);
    if (webAvail === false && curWeb !== "zh-CN") {
      const missingName = getLangDisplayShortName(curWeb);
      state.voiceLang = "zh-CN";
      localStorage.setItem(LS_VOICE_LANG, "zh-CN");
      changedWeb = true;
      if (showToastOnFallback) {
        showToast(`当前设备未安装「${missingName}」语音包，已自动改为标准普通话模式`);
      }
    }

    const curSc =
      state.shortcutLang ||
      (state.unitMode === "setup_once" ? "ask_once" : state.voiceLang || "zh-CN");
    if (curSc !== "ask_once" && !hasAppleOfflineShortcutVoiceForLang(curSc)) {
      state.shortcutLang = state.unitMode === "setup_once" ? "ask_once" : "zh-CN";
      localStorage.setItem(LS_SC_LANG, state.shortcutLang);
    } else if (!state.shortcutLang) {
      state.shortcutLang = curSc;
    }

    populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
    populateVoiceOptions();
    syncShortcutBuilderUI();
    if (changedWeb) {
      refreshBrief(false);
    }
  }

  function syncShortcutBuilderUI() {
    const safeName = sanitizeShortcutName(state.shortcutName || "小区车位");
    el("vgWakeNamePreview").textContent = safeName;
    el("vgArrivalScNameRef").textContent = safeName;
    el("vgAndroidWakeRef").textContent = safeName;

    const unitHint = el("vgUnitTargetHint");
    if (unitHint) {
      if (state.unitMode === "menu") {
        unitHint.textContent = "每次运行时弹出单元门选择列表";
      } else if (state.unitMode === "setup_once") {
        unitHint.textContent = "添加免设置，首次运行时依次点选「单元门 + 播报语言」，后续永久免选";
      } else {
        unitHint.textContent = `已绑定：${getSelectedUnitDisplayLabel()}（一键添加直接用）`;
      }
    }

    const scLangRowSel = el("vgShortcutLangSelect");
    const langHint = el("vgLangTargetHint");
    if (scLangRowSel) {
      scLangRowSel.disabled = false;
    }
    const isSetupOnceAskLang =
      state.unitMode === "setup_once" && (!state.shortcutLang || state.shortcutLang === "ask_once");
    if (langHint) {
      if (isSetupOnceAskLang) {
        langHint.textContent =
          state.popupMode === "silent"
            ? "首次运行弹窗点选语言（含东北话晓北原声），也可在右侧直接指定语言"
            : "首次运行弹窗点选预装语言（普通话/粤语/English），与平面图0延迟同时显示";
      } else if (state.unitMode === "setup_once") {
        langHint.textContent = `首次运行仅选单元门，语言已锁定为：${getLangDisplayShortName(state.shortcutLang)}`;
      } else {
        const effScL =
          !state.shortcutLang || state.shortcutLang === "ask_once"
            ? state.voiceLang || "zh-CN"
            : state.shortcutLang;
        langHint.textContent =
          effScL === "zh-NE" && state.popupMode === "silent"
            ? "已绑定：东北话（纯语音模式播放晓北原声）"
            : `已绑定语音：${getLangDisplayShortName(effScL)}（语音与画面同时一并显示）`;
      }
    }

    const popupHints = {
      text: "播报同时弹出单份精炼推荐文字（无需等语音播完）",
      map: "播报同时弹出纯净实时动态平面图（不弹多余文字框）",
      both: "播报同时同屏弹出「精炼推荐文字 + 动态平面图」",
      silent: "仅车载语音播报，屏幕不弹任何卡片",
    };
    el("vgShowMapHint").textContent = popupHints[state.popupMode] || popupHints.text;

    el("vgArrivalConfigBox").classList.toggle("hidden", !state.arrivalEnabled);

    const mapLabels = {
      amap: "打开高德选点",
      apple: "打开苹果地图",
      baidu: "打开百度地图",
      tencent: "打开腾讯地图",
    };
    const openMapBtn = el("vgOpenNativeMapBtn");
    if (openMapBtn) {
      openMapBtn.textContent = mapLabels[state.mapApp] || "打开地图";
    }

    const uid = state.unitId || "8-1";
    const rep = state.repeat || 2;
    const vtype = state.vehicle || "sedan";
    const webLang = state.voiceLang || state.preferredVoiceLang || "zh-CN";
    const rawScLang =
      state.unitMode !== "setup_once" && state.shortcutLang === "ask_once"
        ? webLang
        : state.shortcutLang || (state.unitMode === "setup_once" ? "ask_once" : webLang);
    const scLang =
      rawScLang === "ask_once"
        ? "ask_once"
        : hasAppleOfflineShortcutVoiceForLang(rawScLang)
        ? rawScLang
        : "zh-CN";
    const scDialectReady =
      (scLang === "zh-SC" || scLang === "zh-NE" || scLang === "zh-SN") &&
      hasLocalAppleDialectPack(scLang);

    const briefUrl = applyWsParam(new URL("/api/voice/brief", window.location.origin));
    briefUrl.searchParams.set("unit", uid);
    briefUrl.searchParams.set("repeat", String(rep));
    briefUrl.searchParams.set("vehicle", vtype);
    briefUrl.searchParams.set("lang", webLang);
    if (hasBrowserVoiceForLang(webLang) === true && (webLang === "zh-SC" || webLang === "zh-NE")) {
      briefUrl.searchParams.set("dialect_ready", "1");
    }
    briefUrl.searchParams.set("format", "text");
    if (state.token) briefUrl.searchParams.set("token", state.token);
    el("vgBriefUrlCode").value = briefUrl.toString();

    const wsRootPath =
      state.workspaceId && state.workspaceId !== "default"
        ? `/w/${encodeURIComponent(state.workspaceId)}`
        : "/";
    const quickLaunchUrl = new URL(wsRootPath, window.location.origin);
    quickLaunchUrl.searchParams.set("unit", uid);
    quickLaunchUrl.searchParams.set("lang", webLang);
    quickLaunchUrl.searchParams.set("autostart", "1");
    if (state.token) quickLaunchUrl.searchParams.set("token", state.token);
    el("vgAndroidQuickLaunchCode").value = quickLaunchUrl.toString();

    const scUrl = applyWsParam(new URL("/api/voice/siri.shortcut", window.location.origin));
    scUrl.searchParams.set("name", safeName);
    scUrl.searchParams.set("unit", uid);
    scUrl.searchParams.set("unit_mode", state.unitMode);
    scUrl.searchParams.set("mode", state.shortcutAction);
    scUrl.searchParams.set("repeat", String(rep));
    scUrl.searchParams.set("vehicle", vtype);
    scUrl.searchParams.set("lang", scLang);
    if (scLang === "ask_once") {
      scUrl.searchParams.set("lang_mode", "ask_once");
    }
    if (scDialectReady) {
      scUrl.searchParams.set("dialect_ready", "1");
    }
    scUrl.searchParams.set("show_map", state.popupMode || "text");
    if (state.unitMode === "setup_once") {
      scUrl.searchParams.set("device_uid", state.deviceUid);
    }
    if (state.arrivalEnabled && state.arrivalPlace.trim()) {
      scUrl.searchParams.set("arrival_place", state.arrivalPlace.trim());
      scUrl.searchParams.set("arrival_radius", state.arrivalRadius || "200");
    }
    if (state.token) scUrl.searchParams.set("token", state.token);
    state.currentShortcutUrl = scUrl.toString();

    const dlBtn = el("vgDownloadShortcutBtn");
    if (!state.generatingShortcut && dlBtn) {
      if (state.icloudShortcutUrl) {
        dlBtn.textContent = ` 添加「${safeName}」快捷指令 (iCloud)`;
      } else if (state.shortcutSignedReady) {
        dlBtn.textContent = ` 生成并添加「${safeName}」快捷指令`;
      } else {
        dlBtn.textContent = ` 打开「${safeName}」快捷指令向导`;
      }
    }
  }

  async function handleGenerateShortcutClick() {
    if (state.generatingShortcut) return;
    const dlBtn = el("vgDownloadShortcutBtn");
    const statusHint = el("vgShortcutStatusHint");
    // Mint a fresh unbound token whenever generating a `setup_once` shortcut so its 1st run always prompts ONCE!
    if (state.unitMode === "setup_once") {
      state.deviceUid = mintFreshShortcutBindUid();
    }
    syncShortcutBuilderUI();

    if (state.icloudShortcutUrl) {
      window.open(state.icloudShortcutUrl, "_blank", "noopener");
      return;
    }

    state.generatingShortcut = true;
    dlBtn.disabled = true;
    dlBtn.classList.add("is-busy");
    dlBtn.textContent = "⏳ 正在签名生成快捷指令，请稍候…";
    if (statusHint) {
      statusHint.textContent = "正在进行 Apple 官方签名打包，完成后自动弹出添加页，请勿重复点击";
    }

    try {
      const prepUrl = new URL(state.currentShortcutUrl);
      prepUrl.searchParams.set("prepare", "1");
      const res = await fetch(prepUrl.toString(), {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (res.status === 401) {
        el("vgAuthBar").classList.remove("hidden");
        showToast("请先验证站点访问口令");
        return;
      }
      if (!res.ok) {
        showToast("快捷指令生成失败，请稍后重试");
        return;
      }
      // Signed bytes are now warm in memory cache (<5ms) — append `_ts` to defeat Safari/Chrome URL cache!
      const dlTarget = new URL(state.currentShortcutUrl);
      dlTarget.searchParams.set("_ts", String(Date.now()));
      window.location.assign(dlTarget.toString());
      showToast("✅ 已生成，请在弹出的快捷指令页点击「添加快捷指令」");
    } catch (_err) {
      showToast("网络连接异常，请稍后重试");
    } finally {
      setTimeout(() => {
        state.generatingShortcut = false;
        dlBtn.disabled = false;
        dlBtn.classList.remove("is-busy");
        if (statusHint) {
          statusHint.textContent = "Apple 官方签名 · 支持添加至主屏幕图标、Siri 与 CarPlay";
        }
        syncShortcutBuilderUI();
      }, 1600);
    }
  }

  function populateUnitDropdown(units, currentUnitId) {
    const sel = el("vgUnitSelect");
    const manualRow = el("vgManualUnitRow");
    const manualInput = el("vgManualUnitInput");
    sel.innerHTML = "";

    let matchedInList = false;
    units.forEach((u) => {
      const opt = document.createElement("option");
      opt.value = u.unit_id;
      opt.textContent = `🏠 ${u.name} (${u.unit_id})`;
      if (u.unit_id === currentUnitId) matchedInList = true;
      sel.appendChild(opt);
    });

    if (currentUnitId && !matchedInList) {
      const customOpt = document.createElement("option");
      customOpt.value = currentUnitId;
      customOpt.textContent = `🏠 手动指定：${currentUnitId}`;
      sel.appendChild(customOpt);
    }

    const manualOpt = document.createElement("option");
    manualOpt.value = MANUAL_OPT_VAL;
    manualOpt.textContent = "✏️ 手动输入其他单元…";
    sel.appendChild(manualOpt);

    if (currentUnitId) {
      sel.value = currentUnitId;
    }
    manualRow.classList.add("hidden");
    manualInput.value = matchedInList ? "" : currentUnitId || "";
  }

  async function loadGatewayStatus() {
    const statusUrl = applyWsParam(new URL("/api/voice/status", window.location.origin));
    const res = await fetch(statusUrl.toString(), { headers: authHeaders() });
    const data = await res.json();
    const gw = data.gateway || {};
    state.preferredVoiceLang = data.preferred_voice_lang || gw.preferred_voice_lang || "";
    state.voiceLanguages = data.voice_languages || gw.voice_languages || [];
    if (!state.voiceLang) {
      state.voiceLang =
        data.default_voice_lang ||
        gw.default_voice_lang ||
        state.preferredVoiceLang ||
        "zh-CN";
    }
    if (!state.shortcutLang) {
      state.shortcutLang = hasAppleOfflineShortcutVoiceForLang(state.voiceLang)
        ? state.voiceLang
        : "zh-CN";
    }
    reconcileInstalledVoicePacks(false);

    if (data.community_name) {
      el("vgTitle").textContent = data.community_name;
      const shortComm = String(data.community_name).split(/[·.．｜|\-—\s]/)[0].slice(0, 10);
      // Auto-heal any previously cached shortcutName that contained `.` (e.g. `阳光花园.邻里公益寻车位`)
      if (!state.shortcutName || /[.．]/.test(state.shortcutName)) {
        state.shortcutName = shortComm ? `${shortComm}车位` : "小区车位";
        localStorage.setItem(LS_SC_NAME, state.shortcutName);
      }
    }
    if (!state.shortcutName) {
      state.shortcutName = "小区车位";
    }
    el("vgShortcutNameInput").value = sanitizeShortcutName(state.shortcutName);

    const authReq = data.auth_required ?? gw.token_required;
    const isAuth = data.authorized ?? true;
    if (authReq && !isAuth) {
      el("vgAuthBar").classList.remove("hidden");
      return false;
    }
    el("vgAuthBar").classList.add("hidden");

    state.icloudShortcutUrl = data.icloud_shortcut_url || gw.icloud_shortcut_url || "";
    state.shortcutSignedReady = Boolean(
      data.shortcut_signed_ready ?? gw.shortcut_signed_ready ?? true
    );
    if (!state.arrivalPlace && (data.arrival_place || gw.arrival_place)) {
      state.arrivalPlace = data.arrival_place || gw.arrival_place || "";
    }
    el("vgArrivalPlaceInput").value = state.arrivalPlace;
    el("vgArrivalRadiusSelect").value = String(state.arrivalRadius || "200");
    el("vgUnitModeSelect").value = state.unitMode;
    el("vgShortcutActionSelect").value = state.shortcutAction;
    el("vgPopupModeSelect").value = state.popupMode;
    el("vgMapAppSelect").value = state.mapApp;
    el("vgOptArrival").checked = state.arrivalEnabled;

    state.units = data.units || [];
    if (!state.unitId) {
      state.unitId =
        data.default_unit_id ||
        gw.default_unit_id ||
        (state.units[0] && state.units[0].unit_id) ||
        "8-1";
    }
    populateUnitDropdown(state.units, state.unitId);

    el("vgVehicleSelect").value = state.vehicle;
    syncRepeatButtonsUI();

    state.mobileUrl = resolveMobileViewerUrl(data.public_mobile_url || gw.public_mobile_url);
    syncMobileLinks();

    const llmBadge = el("vgLlmBadge");
    if ((data.vlm_configured ?? gw.llm_configured) && (data.llm_qa_enabled ?? gw.llm_qa_enabled)) {
      llmBadge.textContent = "✨ 自有大模型在线";
    } else {
      llmBadge.textContent = "⚡ 本地极速引擎";
    }

    syncShortcutBuilderUI();
    return true;
  }

  async function refreshBrief(speakAfterFetch) {
    const u = applyWsParam(new URL("/api/voice/brief", window.location.origin));
    u.searchParams.set("unit", state.unitId || "8-1");
    u.searchParams.set("repeat", String(state.repeat || 2));
    u.searchParams.set("vehicle", state.vehicle || "sedan");
    if (state.voiceLang) {
      u.searchParams.set("lang", state.voiceLang);
    }
    u.searchParams.set("format", "json");

    try {
      const res = await fetch(u.toString(), { headers: authHeaders(), cache: "no-store" });
      if (res.status === 401) {
        el("vgAuthBar").classList.remove("hidden");
        showToast("请输入站点访问口令");
        return;
      }
      const data = await res.json();
      el("vgLiveDot").classList.remove("is-offline");

      const totFree = Number(data.total_effective_free || 0);
      el("vgFreeNum").textContent = String(totFree);
      el("vgFreeBadge").classList.toggle("is-full", totFree <= 0);

      const recTag = el("vgRecTag");
      if (data.primary_zone) {
        recTag.textContent = "推荐";
        recTag.classList.remove("is-empty");
        el("vgBriefSummary").textContent =
          `${data.primary_zone.zone_name}（余 ${data.primary_zone.effective_free} 位）`;
        el("vgBriefWalk").textContent = `🚶 ${Math.round(data.primary_zone.distance_m)}m`;
      } else {
        recTag.textContent = "已满";
        recTag.classList.add("is-empty");
        el("vgBriefSummary").textContent = "院内车位已停满，请往周边车场";
        el("vgBriefWalk").textContent = data.unit_name || "";
      }

      state.latestSingleText = data.single_text || "";
      state.latestRepeatText = data.repeat_text || "";
      state.latestSpeech = data.speech_text || data.single_text || "";

      const altWrap = el("vgAlternatives");
      const alts = data.alternatives || [];
      altWrap.textContent = alts.length
        ? `备选 ${alts[0].display_name} 余${alts[0].effective_free}`
        : "";

      refreshVoiceMap();
      updateSpeakBtnUI();
      if (speakAfterFetch && (state.latestSingleText || state.latestSpeech)) {
        speakCurrentBrief();
      }
    } catch (_err) {
      el("vgLiveDot").classList.add("is-offline");
    }
  }

  async function askGateway(questionText) {
    const q = String(questionText || "").trim();
    if (!q) {
      showToast("请先说话或输入问题");
      return;
    }
    state.lastAskedQuestion = q;
    const sendBtn = el("vgAskSendBtn");
    sendBtn.disabled = true;
    sendBtn.textContent = "思考…";

    try {
      const askUrl = applyWsParam(new URL("/api/voice/ask", window.location.origin));
      const res = await fetch(askUrl.toString(), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          question: q,
          unit_id: state.unitId,
          vehicle_type: state.vehicle,
          voice_lang: state.voiceLang || "zh-CN",
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.error || "提问失败");
        return;
      }
      if (data.voice_lang && data.voice_lang !== state.voiceLang) {
        if (hasBrowserVoiceForLang(data.voice_lang) === false) {
          state.voiceLang = "zh-CN";
          localStorage.setItem(LS_VOICE_LANG, "zh-CN");
          showToast(
            `当前设备未安装「${getLangDisplayShortName(data.voice_lang)}」语音包，已自动切回标准普通话`
          );
          await refreshBrief(false);
          state.latestAnswer = state.latestSingleText || state.latestAnswer;
          el("vgAnswerText").textContent = state.latestAnswer;
        } else {
          state.voiceLang = data.voice_lang;
          localStorage.setItem(LS_VOICE_LANG, state.voiceLang);
          if (hasAppleOfflineShortcutVoiceForLang(state.voiceLang)) {
            state.shortcutLang = state.voiceLang;
            localStorage.setItem(LS_SC_LANG, state.shortcutLang);
          }
        }
        populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
        populateVoiceOptions();
        syncShortcutBuilderUI();
      }
      state.latestAnswer = data.answer || data.speech_text || "";
      const emptyHint = el("vgAiEmptyHint");
      if (emptyHint) emptyHint.classList.add("hidden");
      el("vgAnswerCard").classList.remove("hidden");
      el("vgAnswerText").textContent = state.latestAnswer;

      const eng = data.engine || data.source || "";
      const srcLabel =
        eng === "custom_llm"
          ? `✨ 自有大模型 · ${data.elapsed_ms || 0}ms`
          : eng === "fast_bind" || eng === "command_bind"
          ? "📌 已切换目的单元"
          : `⚡ 极速引擎 · ${data.elapsed_ms || 0}ms`;
      el("vgAnswerSource").textContent = srcLabel;

      if (data.unit_id && data.unit_id !== state.unitId) {
        state.unitId = data.unit_id;
        localStorage.setItem(LS_UNIT, state.unitId);
        populateUnitDropdown(state.units, state.unitId);
        syncMobileLinks();
        syncShortcutBuilderUI();
        refreshBrief(false);
      }
      speakText(state.latestAnswer);
    } catch (err) {
      showToast("网络请求异常：" + err.message);
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = "发送";
    }
  }

  function setupSpeechRecognition() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const micBtn = el("vgMicBtn");
    const micLabel = el("vgMicLabel");
    if (!SR) {
      micBtn.addEventListener("click", () => {
        el("vgAskInput").focus();
        showToast("可点键盘麦克风语音听写输入，或使用 Siri 快捷指令");
      });
      return;
    }
    const rec = new SR();
    rec.lang = "zh-CN";
    rec.interimResults = false;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      state.recording = true;
      micBtn.classList.add("recording");
      micLabel.textContent = "聆听中";
    };
    rec.onend = () => {
      state.recording = false;
      micBtn.classList.remove("recording");
      micLabel.textContent = "说话";
    };
    rec.onerror = () => {
      state.recording = false;
      micBtn.classList.remove("recording");
      micLabel.textContent = "说话";
      showToast("未听清语音，请重试或直接输入");
    };
    rec.onresult = (ev) => {
      const transcript =
        ev.results && ev.results[0] && ev.results[0][0] ? ev.results[0][0].transcript : "";
      if (transcript) {
        el("vgAskInput").value = transcript;
        askGateway(transcript);
      }
    };
    state.recognition = rec;

    micBtn.addEventListener("click", () => {
      if (state.recording) {
        rec.stop();
      } else {
        stopSpeaking();
        rec.start();
      }
    });
  }

  function switchPlatformTab(platform) {
    const isAndroid = platform === "android";
    document.querySelectorAll("#vgPlatformTabs .vg-seg-btn").forEach((btn) => {
      const active = btn.getAttribute("data-platform") === platform;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    el("vgPanelApple").classList.toggle("hidden", isAndroid);
    el("vgPanelAndroid").classList.toggle("hidden", !isAndroid);
  }

  // ---------------------------------------------------------------------------
  // Custom Reusable Quick Questions (Persisted per Workspace)
  // ---------------------------------------------------------------------------

  function renderCustomChips() {
    const wrap = el("vgCustomChipsList");
    if (!wrap) return;
    wrap.innerHTML = "";
    state.customChips.forEach((qText, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "vg-chip vg-chip-custom";
      btn.setAttribute("data-q", qText);
      btn.title = qText;

      const labelSpan = document.createElement("span");
      labelSpan.textContent = qText.length > 16 ? `${qText.slice(0, 15)}…` : qText;

      const delSpan = document.createElement("span");
      delSpan.className = "vg-chip-del";
      delSpan.textContent = "×";
      delSpan.title = "删除该常用问题";
      delSpan.addEventListener("click", (ev) => {
        ev.stopPropagation();
        state.customChips.splice(idx, 1);
        localStorage.setItem(LS_CUSTOM_CHIPS, JSON.stringify(state.customChips));
        renderCustomChips();
        showToast("已移除自定义问题");
      });

      btn.appendChild(labelSpan);
      btn.appendChild(delSpan);
      btn.addEventListener("click", () => {
        el("vgAskInput").value = qText;
        askGateway(qText);
      });
      wrap.appendChild(btn);
    });
  }

  function addCustomQuestionChip(rawText) {
    const clean = String(rawText || "").trim().slice(0, 120);
    if (!clean) {
      showToast("请输入要保存的常用问题");
      return false;
    }
    if (state.customChips.includes(clean)) {
      showToast("该常用问题已存在");
      return true;
    }
    state.customChips.push(clean);
    if (state.customChips.length > 12) {
      state.customChips = state.customChips.slice(-12);
    }
    localStorage.setItem(LS_CUSTOM_CHIPS, JSON.stringify(state.customChips));
    renderCustomChips();
    showToast("✅ 已添加为常用快捷问题，下次点击即可提问");
    return true;
  }

  // ---------------------------------------------------------------------------
  // Native Apple Maps / Local Map Address Autocomplete & Geolocation Picker
  // ---------------------------------------------------------------------------

  function syncSelectedPlaceMetaUI() {
    const metaEl = el("vgSelectedPlaceMeta");
    if (!metaEl) return;
    if (state.arrivalPlace && state.arrivalCoords) {
      metaEl.textContent = `📍 已锁定本地地图坐标 (${state.arrivalCoords.lat.toFixed(4)}, ${state.arrivalCoords.lon.toFixed(4)})`;
    } else if (state.arrivalPlace) {
      metaEl.textContent = `📍 当前地址：${state.arrivalPlace}`;
    } else {
      metaEl.textContent = "输入关键字即可从  本地地图检索小区大门/停车场入口";
    }
  }

  function hidePlaceDropdown() {
    const dd = el("vgPlaceDropdown");
    if (dd) dd.classList.add("hidden");
  }

  function renderPlaceDropdown(places) {
    const dd = el("vgPlaceDropdown");
    if (!dd) return;
    dd.innerHTML = "";
    if (!places || !places.length) {
      dd.classList.add("hidden");
      return;
    }
    places.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "vg-place-option";
      btn.setAttribute("role", "option");

      const main = document.createElement("div");
      main.className = "vg-place-opt-main";
      const t = document.createElement("span");
      t.className = "vg-place-opt-title";
      t.textContent = `📍 ${p.title || ""}`;
      const sub = document.createElement("span");
      sub.className = "vg-place-opt-sub";
      sub.textContent = p.subtitle || p.full_address || "";
      main.appendChild(t);
      if (sub.textContent) main.appendChild(sub);

      const tag = document.createElement("span");
      tag.className = "vg-place-opt-tag";
      tag.textContent = p.source === "apple_maps" ? " 本地地图" : "地图地址";

      btn.appendChild(main);
      btn.appendChild(tag);
      btn.addEventListener("click", () => {
        const chosenText = p.title || p.full_address || "";
        state.arrivalPlace = chosenText;
        el("vgArrivalPlaceInput").value = chosenText;
        localStorage.setItem(LS_SC_ARRIVAL_PLACE, state.arrivalPlace);
        if (typeof p.lat === "number" && typeof p.lon === "number") {
          state.arrivalCoords = { lat: p.lat, lon: p.lon, source: p.source || "" };
          localStorage.setItem(LS_SC_ARRIVAL_COORDS, JSON.stringify(state.arrivalCoords));
        } else {
          state.arrivalCoords = null;
          localStorage.removeItem(LS_SC_ARRIVAL_COORDS);
        }
        hidePlaceDropdown();
        syncSelectedPlaceMetaUI();
        syncShortcutBuilderUI();
        copyToClipboard(chosenText, `已选定并复制「${chosenText}」`);
      });
      dd.appendChild(btn);
    });
    dd.classList.remove("hidden");
  }

  async function queryLocalMapPlaces(qStr) {
    const clean = String(qStr || "").trim();
    if (clean.length < 2) {
      hidePlaceDropdown();
      return;
    }
    try {
      const u = applyWsParam(new URL("/api/voice/places", window.location.origin));
      u.searchParams.set("q", clean);
      const res = await fetch(u.toString(), { headers: authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      renderPlaceDropdown(data.places || []);
    } catch (_e) {}
  }

  function showGeoPermissionGuide(kind) {
    const box = el("vgGeoPermGuide");
    const txt = el("vgGeoPermGuideText");
    if (!box || !txt) return;
    const ua = navigator.userAgent || "";
    const isIOS = /iphone|ipad|ipod/i.test(ua);
    const isChromeIOS = /crios/i.test(ua);
    const isAndroid = /android/i.test(ua);

    if (kind === "insecure_http") {
      txt.innerHTML =
        "<strong>🔒 局域网 HTTP 网页定位受浏览器安全保护限制：</strong><br>" +
        "苹果/谷歌规定仅 <code>HTTPS</code> 域名网页可直接读取手机 GPS（绑定 Cloudflare 域名后即可自动定位）。<br>" +
        "👉 <strong>当前最快方式：</strong>直接在上方输入框打字搜索小区名，或点下方按钮打开您手机上的<strong>高德/苹果/百度地图</strong>选点复制！";
    } else if (isIOS && isChromeIOS) {
      txt.innerHTML =
        "<strong>📍 开启 iPhone Chrome 位置权限步骤：</strong><br>" +
        "打开 iPhone 系统<strong>「设置 → App → Chrome → 位置」</strong>，勾选<strong>「使用 App 期间」</strong>并打开「精确位置」，返回本页再点定位即可。";
    } else if (isIOS) {
      txt.innerHTML =
        "<strong>📍 开启 iPhone Safari 位置权限步骤：</strong><br>" +
        "打开 iPhone 系统<strong>「设置 → 隐私与安全性 → 定位服务 → Safari 网站」</strong>，勾选<strong>「使用 App 期间」</strong>（或在地址栏点「大小/设置」图标 → 位置 → 允许）。";
    } else if (isAndroid) {
      txt.innerHTML =
        "<strong>📍 开启安卓浏览器位置权限步骤：</strong><br>" +
        "点击浏览器地址栏左侧<strong>「🔒 锁图标 / 站点设置」→「权限」→ 开启「位置信息」</strong>（同时确认系统下拉控制中心已开启 GPS 定位）。";
    } else {
      txt.innerHTML =
        "<strong>📍 开启浏览器位置权限步骤：</strong><br>" +
        "请在系统「设置 → 隐私与安全性 → 定位服务」中允许当前浏览器访问位置，或直接输入小区名称检索标准地址。";
    }
    box.classList.remove("hidden");
  }

  function locateCurrentAddress() {
    if (!window.isSecureContext && !/^(localhost|127\.0\.0\.1)$/i.test(window.location.hostname)) {
      showGeoPermissionGuide("insecure_http");
      return;
    }
    if (!navigator.geolocation) {
      showGeoPermissionGuide("unsupported");
      return;
    }
    const btn = el("vgLocateMeBtn");
    btn.disabled = true;
    btn.textContent = "定位中…";
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          el("vgGeoPermGuide").classList.add("hidden");
          const lat = pos.coords.latitude;
          const lon = pos.coords.longitude;
          const u = applyWsParam(new URL("/api/voice/places", window.location.origin));
          u.searchParams.set("lat", String(lat));
          u.searchParams.set("lon", String(lon));
          const res = await fetch(u.toString(), { headers: authHeaders() });
          const data = await res.json();
          const places = data.places || [];
          if (places.length) {
            const p0 = places[0];
            state.arrivalPlace = p0.title || p0.full_address || "";
            state.arrivalCoords = { lat, lon, source: p0.source || "" };
            el("vgArrivalPlaceInput").value = state.arrivalPlace;
            localStorage.setItem(LS_SC_ARRIVAL_PLACE, state.arrivalPlace);
            localStorage.setItem(LS_SC_ARRIVAL_COORDS, JSON.stringify(state.arrivalCoords));
            syncSelectedPlaceMetaUI();
            syncShortcutBuilderUI();
            if (places.length > 1) renderPlaceDropdown(places);
            showToast(`📍 已定位：${state.arrivalPlace}`);
          } else {
            showToast("已获取坐标，请输入小区名称选择精确大门");
          }
        } catch (_e) {
          showToast("逆地理编码失败，请直接输入小区名称检索");
        } finally {
          btn.disabled = false;
          btn.textContent = "📍 定位";
        }
      },
      (err) => {
        btn.disabled = false;
        btn.textContent = "📍 定位";
        if (err && err.code === 1) {
          showGeoPermissionGuide("denied");
        } else {
          showToast("定位超时，可直接输入小区名称搜索或打开手机地图");
        }
      },
      { enableHighAccuracy: true, timeout: 6500 }
    );
  }

  // WGS-84 -> GCJ-02 conversion for Amap / Tencent / Baidu accuracy in Mainland China
  function wgs84ToGcj02(lat, lon) {
    if (lon < 72.004 || lon > 137.8347 || lat < 0.8293 || lat > 55.8271) {
      return { lat, lon };
    }
    const a = 6378245.0;
    const ee = 0.00669342162296594323;
    const transformLat = (x, y) => {
      let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
      ret += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0;
      ret += ((20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin((y / 3.0) * Math.PI)) * 2.0) / 3.0;
      ret += ((160.0 * Math.sin((y / 12.0) * Math.PI) + 320 * Math.sin((y * Math.PI) / 30.0)) * 2.0) / 3.0;
      return ret;
    };
    const transformLon = (x, y) => {
      let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
      ret += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0;
      ret += ((20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin((x / 3.0) * Math.PI)) * 2.0) / 3.0;
      ret += ((150.0 * Math.sin((x / 12.0) * Math.PI) + 300.0 * Math.sin((x / 30.0) * Math.PI)) * 2.0) / 3.0;
      return ret;
    };
    let dLat = transformLat(lon - 105.0, lat - 35.0);
    let dLon = transformLon(lon - 105.0, lat - 35.0);
    const radLat = (lat / 180.0) * Math.PI;
    let magic = Math.sin(radLat);
    magic = 1 - ee * magic * magic;
    const sqrtMagic = Math.sqrt(magic);
    dLat = (dLat * 180.0) / (((a * (1 - ee)) / (magic * sqrtMagic)) * Math.PI);
    dLon = (dLon * 180.0) / ((a / sqrtMagic) * Math.cos(radLat) * Math.PI);
    return { lat: lat + dLat, lon: lon + dLon };
  }

  function openSelectedMapApp() {
    const rawSite = String(el("vgTitle").textContent || "").split(/[·.．｜|\-—\s]/)[0].trim();
    const q = (state.arrivalPlace || el("vgArrivalPlaceInput").value || rawSite || "停车场").trim();
    const encQ = encodeURIComponent(q);
    const ua = navigator.userAgent || "";
    const isIOS = /iphone|ipad|ipod/i.test(ua);
    const isAndroid = /android/i.test(ua);
    const isMac = /macintosh|mac os/i.test(ua) && !isIOS;
    const app = state.mapApp || "amap";

    let gcj = null;
    if (state.arrivalCoords && typeof state.arrivalCoords.lat === "number") {
      // Apple MapKit in Mainland China already returns GCJ-02; OSM Nominatim returns WGS-84
      gcj =
        state.arrivalCoords.source === "osm_map"
          ? wgs84ToGcj02(state.arrivalCoords.lat, state.arrivalCoords.lon)
          : { lat: state.arrivalCoords.lat, lon: state.arrivalCoords.lon };
    }

    const launchSchemeWithFallback = (schemeUrl, webFallbackUrl) => {
      const startAt = Date.now();
      window.location.href = schemeUrl;
      setTimeout(() => {
        if (!document.hidden && Date.now() - startAt < 2200) {
          window.open(webFallbackUrl, "_blank", "noopener");
        }
      }, 1350);
    };

    if (app === "apple") {
      const llPart = gcj ? `&ll=${gcj.lat},${gcj.lon}` : "";
      if (isIOS || isMac) {
        window.location.href = `maps://?q=${encQ}${llPart}`;
      } else {
        window.open(`https://maps.apple.com/?q=${encQ}${llPart}`, "_blank", "noopener");
      }
      return;
    }

    if (app === "amap") {
      const webUrl = gcj
        ? `https://uri.amap.com/marker?position=${gcj.lon},${gcj.lat}&name=${encQ}&callnative=1`
        : `https://uri.amap.com/search?keyword=${encQ}&callnative=1`;
      if (isIOS) {
        const scheme = gcj
          ? `iosamap://viewMap?sourceApplication=CommunityParking&poiname=${encQ}&lat=${gcj.lat}&lon=${gcj.lon}&dev=0`
          : `iosamap://poi?sourceApplication=CommunityParking&name=${encQ}&dev=0`;
        launchSchemeWithFallback(scheme, webUrl);
      } else if (isAndroid) {
        const scheme = gcj
          ? `androidamap://viewMap?sourceApplication=CommunityParking&poiname=${encQ}&lat=${gcj.lat}&lon=${gcj.lon}&dev=0`
          : `androidamap://poi?sourceApplication=CommunityParking&keywords=${encQ}&dev=0`;
        launchSchemeWithFallback(scheme, webUrl);
      } else {
        window.open(webUrl, "_blank", "noopener");
      }
      return;
    }

    if (app === "baidu") {
      const webUrl = `https://api.map.baidu.com/geocoder?address=${encQ}&output=html&src=CommunityParking`;
      if (isIOS || isAndroid) {
        const scheme = gcj
          ? `baidumap://map/marker?location=${gcj.lat},${gcj.lon}&title=${encQ}&coord_type=gcj02&src=CommunityParking`
          : `baidumap://map/geocoder?address=${encQ}&src=CommunityParking`;
        launchSchemeWithFallback(scheme, webUrl);
      } else {
        window.open(webUrl, "_blank", "noopener");
      }
      return;
    }

    // Tencent Map (`tencent`)
    const qqWeb = `https://apis.map.qq.com/uri/v1/search?keyword=${encQ}&referer=CommunityParking`;
    if (isIOS || isAndroid) {
      const scheme = gcj
        ? `qqmap://map/marker?marker=coord:${gcj.lat},${gcj.lon};title:${encQ}&referer=CommunityParking`
        : `qqmap://map/search?keyword=${encQ}&referer=CommunityParking`;
      launchSchemeWithFallback(scheme, qqWeb);
    } else {
      window.open(qqWeb, "_blank", "noopener");
    }
  }

  // Universal Clipboard Copy — works on BOTH HTTPS and LAN HTTP (`http://192.168.x.x`) across Android & iOS!
  async function copyToClipboard(text, okMsg, fallbackInputEl) {
    const clean = String(text || "").trim();
    if (!clean) return;

    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(clean);
        showToast(okMsg);
        return;
      } catch (_e) {}
    }

    // Synchronous textarea fallback for LAN HTTP (`http://192.168.x.x`) on Android Chrome / Vivo / Xiaomi / iOS
    let copied = false;
    const ta = document.createElement("textarea");
    ta.value = clean;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "-9999px";
    ta.style.fontSize = "16px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    try {
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, clean.length);
      copied = Boolean(document.execCommand("copy"));
    } catch (_err) {
      copied = false;
    }
    document.body.removeChild(ta);

    if (copied) {
      showToast(okMsg);
      return;
    }

    if (fallbackInputEl && fallbackInputEl.select) {
      fallbackInputEl.focus();
      fallbackInputEl.select();
      try {
        if (document.execCommand("copy")) {
          showToast(okMsg);
          return;
        }
      } catch (_e2) {}
      showToast("已全选链接，点击弹出菜单「复制」即可");
    } else {
      window.prompt("请长按全选并复制以下地址：", clean);
    }
  }

  function bindEvents() {
    el("vgPageTabs").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".vg-page-tab");
      if (!btn) return;
      switchPage(btn.getAttribute("data-page") || "live");
    });

    el("vgSaveTokenBtn").addEventListener("click", async () => {
      state.token = el("vgTokenInput").value.trim();
      localStorage.setItem(LS_TOKEN, state.token);
      const ok = await loadGatewayStatus();
      if (ok) {
        showToast("口令验证通过");
        refreshBrief(false);
      } else {
        showToast("口令不正确");
      }
    });

    el("vgUnitSelect").addEventListener("change", (e) => {
      const val = e.target.value;
      if (val === MANUAL_OPT_VAL) {
        el("vgManualUnitRow").classList.remove("hidden");
        el("vgManualUnitInput").focus();
        return;
      }
      el("vgManualUnitRow").classList.add("hidden");
      state.unitId = val;
      localStorage.setItem(LS_UNIT, state.unitId);
      syncMobileLinks();
      syncShortcutBuilderUI();
      refreshBrief(true);
    });

    const applyManualUnit = () => {
      const manualVal = el("vgManualUnitInput").value.trim();
      if (!manualVal) {
        showToast("请输入单元号，例如 8-1 或 3-1");
        return;
      }
      state.unitId = manualVal;
      localStorage.setItem(LS_UNIT, state.unitId);
      populateUnitDropdown(state.units, state.unitId);
      syncMobileLinks();
      syncShortcutBuilderUI();
      refreshBrief(true);
      showToast(`已切换至单元 ${manualVal}`);
    };
    el("vgApplyManualUnitBtn").addEventListener("click", applyManualUnit);
    el("vgManualUnitInput").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        applyManualUnit();
      }
    });

    el("vgRepeatSeg").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".vg-seg-btn");
      if (!btn) return;
      state.repeat = parseInt(btn.getAttribute("data-repeat") || "2", 10) === 1 ? 1 : 2;
      localStorage.setItem(LS_REPEAT, String(state.repeat));
      syncRepeatButtonsUI();
      syncShortcutBuilderUI();
      refreshBrief(true);
    });

    el("vgVehicleSelect").addEventListener("change", (e) => {
      state.vehicle = e.target.value;
      localStorage.setItem(LS_VEHICLE, state.vehicle);
      syncShortcutBuilderUI();
      refreshBrief(false);
    });

    const handleWebVoiceLangChange = (requestedLang) => {
      const target = requestedLang || "zh-CN";
      const avail = hasBrowserVoiceForLang(target);
      if (avail === false && target !== "zh-CN") {
        const missingName = getLangDisplayShortName(target);
        state.voiceLang = "zh-CN";
        localStorage.setItem(LS_VOICE_LANG, "zh-CN");
        populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
        populateVoiceOptions();
        syncShortcutBuilderUI();
        showToast(
          `当前设备未预装「${missingName}」语音包，已自动改为普通话模式（避免普通话强读方言）`
        );
        refreshBrief(true);
        return;
      }
      state.voiceLang = target;
      localStorage.setItem(LS_VOICE_LANG, state.voiceLang);
      if (state.shortcutLang !== "ask_once" && hasAppleOfflineShortcutVoiceForLang(target)) {
        state.shortcutLang = target;
        localStorage.setItem(LS_SC_LANG, state.shortcutLang);
      }
      populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
      populateVoiceOptions();
      syncShortcutBuilderUI();
      refreshBrief(true);
    };

    const handleShortcutLangChange = (requestedLang) => {
      const target = requestedLang || "zh-CN";
      if (target === "ask_once") {
        state.shortcutLang = "ask_once";
        localStorage.setItem(LS_SC_LANG, "ask_once");
        populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
        syncShortcutBuilderUI();
        return;
      }
      if (!hasAppleOfflineShortcutVoiceForLang(target) && target !== "zh-CN") {
        const missingName = getLangDisplayShortName(target);
        state.shortcutLang = "zh-CN";
        localStorage.setItem(LS_SC_LANG, "zh-CN");
        populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
        syncShortcutBuilderUI();
        if (isCloudTtsReadyForLang(target) && state.popupMode !== "silent") {
          showToast(
            `系统未装「${missingName}」离线包，为确保文字与平面图同时一并弹出已改为普通话（纯语音模式可播晓北原声）`
          );
        } else {
          showToast(
            `系统未预装「${missingName}」语音包，快捷指令已自动改为标准普通话模式（避免普通话强读方言）`
          );
        }
        return;
      }
      state.shortcutLang = target;
      localStorage.setItem(LS_SC_LANG, state.shortcutLang);
      if (hasBrowserVoiceForLang(target) !== false) {
        state.voiceLang = target;
        localStorage.setItem(LS_VOICE_LANG, state.voiceLang);
        populateVoiceOptions();
        refreshBrief(false);
      }
      populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
      syncShortcutBuilderUI();
    };

    const liveLangEl = el("vgLangSelect");
    if (liveLangEl) {
      liveLangEl.addEventListener("change", (e) => {
        handleWebVoiceLangChange(e.target.value);
      });
    }

    const scLangEl = el("vgShortcutLangSelect");
    if (scLangEl) {
      scLangEl.addEventListener("change", (e) => {
        handleShortcutLangChange(e.target.value);
      });
    }

    el("vgVoiceSelect").addEventListener("change", (e) => {
      const activeLang = String(state.voiceLang || state.preferredVoiceLang || "zh-CN").trim();
      setPinnedVoiceUriForLang(activeLang, e.target.value || "");
      if (state.latestSingleText || state.latestSpeech) {
        speakCurrentBrief();
      }
    });

    el("vgSpeakBriefBtn").addEventListener("click", () => {
      if (state.speaking) {
        stopSpeaking();
        return;
      }
      if (state.latestSingleText || state.latestSpeech) {
        speakCurrentBrief();
        refreshBrief(false);
      } else {
        refreshBrief(true);
      }
    });

    // Recover cleanly if phone screen slept or browser suspended speech queue
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.speaking && "speechSynthesis" in window) {
        if (!window.speechSynthesis.speaking && !window.speechSynthesis.pending) {
          stopSpeaking();
        }
      }
    });

    bindVoiceMapGestures();

    // Platform Tabs (Apple vs Android)
    el("vgPlatformTabs").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".vg-seg-btn");
      if (!btn) return;
      switchPlatformTab(btn.getAttribute("data-platform") || "apple");
    });

    // Apple Shortcut Customizer Controls
    el("vgShortcutNameInput").addEventListener("input", (e) => {
      state.shortcutName = e.target.value;
      localStorage.setItem(LS_SC_NAME, state.shortcutName);
      syncShortcutBuilderUI();
    });

    el("vgUnitModeSelect").addEventListener("change", (e) => {
      const prevMode = state.unitMode;
      state.unitMode = e.target.value;
      localStorage.setItem(LS_SC_UNIT_MODE, state.unitMode);
      if (state.unitMode === "setup_once" && prevMode !== "setup_once" && !localStorage.getItem(LS_SC_LANG)) {
        state.shortcutLang = "ask_once";
      } else if (state.unitMode !== "setup_once" && state.shortcutLang === "ask_once") {
        state.shortcutLang = state.voiceLang || "zh-CN";
        localStorage.setItem(LS_SC_LANG, state.shortcutLang);
      }
      populateLanguageDropdowns(state.voiceLanguages, state.voiceLang, state.shortcutLang);
      syncShortcutBuilderUI();
    });

    el("vgShortcutActionSelect").addEventListener("change", (e) => {
      state.shortcutAction = e.target.value;
      localStorage.setItem(LS_SC_ACTION, state.shortcutAction);
      syncShortcutBuilderUI();
    });

    el("vgPopupModeSelect").addEventListener("change", (e) => {
      state.popupMode = e.target.value || "text";
      localStorage.setItem(LS_SC_POPUP_MODE, state.popupMode);
      reconcileInstalledVoicePacks(false);
    });

    el("vgMapAppSelect").addEventListener("change", (e) => {
      state.mapApp = e.target.value || "amap";
      localStorage.setItem(LS_MAP_APP, state.mapApp);
      syncShortcutBuilderUI();
    });

    el("vgOptArrival").addEventListener("change", (e) => {
      state.arrivalEnabled = Boolean(e.target.checked);
      localStorage.setItem(LS_SC_ARRIVAL_ON, state.arrivalEnabled ? "1" : "0");
      syncShortcutBuilderUI();
    });

    el("vgArrivalPlaceInput").addEventListener("input", (e) => {
      state.arrivalPlace = e.target.value;
      state.arrivalCoords = null;
      localStorage.setItem(LS_SC_ARRIVAL_PLACE, state.arrivalPlace);
      localStorage.removeItem(LS_SC_ARRIVAL_COORDS);
      syncSelectedPlaceMetaUI();
      syncShortcutBuilderUI();
      clearTimeout(state.placeSearchTimer);
      state.placeSearchTimer = setTimeout(() => {
        queryLocalMapPlaces(state.arrivalPlace);
      }, 220);
    });

    el("vgArrivalPlaceInput").addEventListener("focus", () => {
      const q = (el("vgArrivalPlaceInput").value || "").trim();
      if (q.length >= 2) {
        queryLocalMapPlaces(q);
      } else {
        const shortSite = String(el("vgTitle").textContent || "").split(/[·.．｜|\-—\s]/)[0].trim();
        if (shortSite.length >= 2) {
          queryLocalMapPlaces(shortSite);
        }
      }
    });

    document.addEventListener("click", (ev) => {
      if (!ev.target.closest(".vg-place-picker")) {
        hidePlaceDropdown();
      }
    });

    el("vgLocateMeBtn").addEventListener("click", locateCurrentAddress);
    el("vgOpenNativeMapBtn").addEventListener("click", openSelectedMapApp);
    el("vgGeoOpenMapFallbackBtn").addEventListener("click", openSelectedMapApp);
    el("vgGeoDismissBtn").addEventListener("click", () => {
      el("vgGeoPermGuide").classList.add("hidden");
    });

    el("vgArrivalRadiusSelect").addEventListener("change", (e) => {
      state.arrivalRadius = e.target.value || "200";
      localStorage.setItem(LS_SC_ARRIVAL_RADIUS, state.arrivalRadius);
      syncShortcutBuilderUI();
    });

    el("vgCopyPlaceBtn").addEventListener("click", () => {
      const p = state.arrivalPlace.trim();
      if (!p) {
        showToast("请先输入或搜索选择小区大门地址");
        return;
      }
      copyToClipboard(p, "已复制标准地址，可前往快捷指令「自动化 → 到达」粘贴", el("vgArrivalPlaceInput"));
    });

    // Greyed-out & locked Shortcut Generation Button (prevents rapid multi-clicks!)
    el("vgDownloadShortcutBtn").addEventListener("click", handleGenerateShortcutClick);

    // Android Quick Launch & API Copy (100% LAN HTTP compatible!)
    const qlInput = el("vgAndroidQuickLaunchCode");
    const apiInput = el("vgBriefUrlCode");
    qlInput.addEventListener("click", () => qlInput.select());
    apiInput.addEventListener("click", () => apiInput.select());

    el("vgOpenQuickLaunchBtn").addEventListener("click", () => {
      const target = (qlInput.value || "").trim();
      if (target && target !== "--") {
        window.open(target, "_blank", "noopener");
      }
    });

    el("vgCopyQuickLaunchBtn").addEventListener("click", () => {
      copyToClipboard(
        qlInput.value || "",
        "✅ 已复制即点即播链接，浏览器打开后点「添加到主屏幕」即可",
        qlInput
      );
    });

    el("vgCopyBriefUrlBtn").addEventListener("click", () => {
      copyToClipboard(
        apiInput.value || "",
        "✅ 已复制极速纯文本播报 API 地址",
        apiInput
      );
    });

    // Custom Reusable Quick Questions UI
    renderCustomChips();
    syncSelectedPlaceMetaUI();

    el("vgToggleAddChipBtn").addEventListener("click", () => {
      const ed = el("vgCustomChipEditor");
      const inp = el("vgCustomChipInput");
      const opening = ed.classList.contains("hidden");
      ed.classList.toggle("hidden", !opening);
      if (opening) {
        if (!inp.value && el("vgAskInput").value.trim()) {
          inp.value = el("vgAskInput").value.trim();
        }
        inp.focus();
      }
    });

    const saveChipFromInput = () => {
      const inp = el("vgCustomChipInput");
      if (addCustomQuestionChip(inp.value)) {
        inp.value = "";
        el("vgCustomChipEditor").classList.add("hidden");
      }
    };
    el("vgSaveCustomChipBtn").addEventListener("click", saveChipFromInput);
    el("vgCustomChipInput").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        saveChipFromInput();
      }
    });
    el("vgCancelCustomChipBtn").addEventListener("click", () => {
      el("vgCustomChipEditor").classList.add("hidden");
    });
    el("vgPinQuestionBtn").addEventListener("click", () => {
      const q = state.lastAskedQuestion || el("vgAskInput").value.trim();
      addCustomQuestionChip(q);
    });

    // Custom LLM Q&A
    el("vgAskSendBtn").addEventListener("click", () => {
      askGateway(el("vgAskInput").value);
    });
    el("vgAskInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        askGateway(el("vgAskInput").value);
      }
    });
    el("vgReplayAnswerBtn").addEventListener("click", () => {
      if (state.latestAnswer) speakText(state.latestAnswer);
    });
    document.querySelectorAll("#vgQuickChipsRow > .vg-chip[data-q]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const q = chip.getAttribute("data-q") || "";
        el("vgAskInput").value = q;
        askGateway(q);
      });
    });

    if ("speechSynthesis" in window) {
      reconcileInstalledVoicePacks(false);
      if ("onvoiceschanged" in window.speechSynthesis) {
        window.speechSynthesis.onvoiceschanged = () => {
          reconcileInstalledVoicePacks(false);
        };
      }
    }

    setupSpeechRecognition();
  }

  async function init() {
    if (/android/i.test(navigator.userAgent || "")) {
      switchPlatformTab("android");
    }
    bindEvents();
    const ok = await loadGatewayStatus();
    if (ok) {
      await refreshBrief(state.autostartRequested);
    }
    setInterval(() => {
      if (!document.hidden && el("vgAuthBar").classList.contains("hidden")) {
        refreshBrief(false);
      }
    }, 12000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
