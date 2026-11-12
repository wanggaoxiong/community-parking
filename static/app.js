/* Community Parking Welfare System - Cupertino HIG Frontend Controller */
const state = {
  overview: null,
  lastRecommendedZoneId: null,
  calib: {
    cameraId: "CAM_3F_WIN",
    zoneId: "Z_BLDG3_EAST",
    mode: "perspective_row", // perspective_row | irregular_poly | elastic_strip | edit_vertices
    bgImage: null,
    imgW: 1280,
    imgH: 720,
    spots: [],
    draftPoints: [],
    previewTrapezoids: [],
    draggingHandle: null,
  },
  vlmPresets: {},
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) {
    const msg = data.error || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.payload = data;
    throw err;
  }
  return data;
}

/* ==================== BOOTSTRAP ==================== */
document.addEventListener("DOMContentLoaded", async () => {
  bindTabs();
  bindModelModal();
  bindDriverActions();
  bindCalibrationConsole();
  bindTestLab();
  drawSyntheticDemoLotOnCalibCanvas();
  await refreshOverview();
  await loadCameraSpotsForCalib(state.calib.cameraId);
});

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "calibrate") renderCalibCanvas();
    });
  });
}

async function refreshOverview() {
  const vtype = document.getElementById("vehicleTypeSelect")?.value || "sedan";
  const data = await api(`/api/overview?vehicle_type=${encodeURIComponent(vtype)}`);
  state.overview = data;
  state.vlmPresets = data.config?.vlm_presets || {};

  document.getElementById("communityTitle").textContent = data.community_name;
  document.getElementById("statDisk").textContent = data.privacy_stats.raw_images_written_to_disk;
  document.getElementById("statRam").textContent = data.privacy_stats.frames_processed_in_ram;
  document.getElementById("statTotalFree").textContent = data.total_effective_free;
  document.getElementById("ftpInfoHost").textContent = `${location.hostname || "127.0.0.1"}:${data.config.ftp_port || 2121}`;

  populateSelectors(data);
  renderSoftLocks(data.active_locks);
  renderAbstractSvgMap(data);
  renderZoneCards(data.zones);
  populateConfigUI(data.config);
}

function populateSelectors(data) {
  const unitSel = document.getElementById("unitSelect");
  const curUnit = unitSel.value || "3-1";
  unitSel.innerHTML = data.units
    .map((u) => `<option value="${u.unit_id}">${u.name}</option>`)
    .join("");
  if (data.units.some((u) => u.unit_id === curUnit)) unitSel.value = curUnit;
  updateSiriHint();

  const camOptions = data.cameras
    .map((c) => `<option value="${c.camera_id}">${c.name} (${c.camera_id})</option>`)
    .join("");
  const calibCamSel = document.getElementById("calibCameraSelect");
  const labCamSel = document.getElementById("labCameraSelect");
  const prevCalibCam = calibCamSel.value || state.calib.cameraId;
  const prevLabCam = labCamSel.value || "CAM_3F_WIN";
  calibCamSel.innerHTML = camOptions;
  labCamSel.innerHTML = camOptions;
  if (data.cameras.some((c) => c.camera_id === prevCalibCam)) calibCamSel.value = prevCalibCam;
  if (data.cameras.some((c) => c.camera_id === prevLabCam)) labCamSel.value = prevLabCam;

  const zoneSel = document.getElementById("calibZoneSelect");
  const prevZone = zoneSel.value || state.calib.zoneId;
  zoneSel.innerHTML = data.zones
    .map((z) => `<option value="${z.zone_id}">${z.name}</option>`)
    .join("");
  if (data.zones.some((z) => z.zone_id === prevZone)) zoneSel.value = prevZone;
}

/* ==================== MODEL SETTINGS MODAL (AZURE AI FOUNDRY) ==================== */
function openModelModal() {
  document.getElementById("vlmTestResultBox").classList.add("hidden");
  document.getElementById("cfgSaveMsg").textContent = "";
  document.getElementById("modelModal").classList.remove("hidden");
}

function closeModelModal() {
  document.getElementById("modelModal").classList.add("hidden");
}

function bindModelModal() {
  document.getElementById("btnOpenModelModal").addEventListener("click", openModelModal);
  document.getElementById("btnLabOpenModelSettings").addEventListener("click", openModelModal);
  document.getElementById("btnCloseModelModal").addEventListener("click", closeModelModal);
  document.getElementById("modelModalBackdrop").addEventListener("click", closeModelModal);

  document.getElementById("btnToggleKeyVis").addEventListener("click", () => {
    const inp = document.getElementById("vlmApiKey");
    const isPwd = inp.type === "password";
    inp.type = isPwd ? "text" : "password";
    document.getElementById("btnToggleKeyVis").textContent = isPwd ? "隐藏" : "显示";
  });

  document.getElementById("vlmPresetSelect").addEventListener("change", (e) => {
    const p = state.vlmPresets[e.target.value];
    if (!p) return;
    const urlInput = document.getElementById("vlmBaseUrl");
    if (!urlInput.value || urlInput.value.includes("<your-resource>") || !e.target.value.startsWith("azure")) {
      urlInput.value = p.base_url.includes("<your-resource>") ? "" : p.base_url;
      urlInput.placeholder = p.base_url;
    }
    document.getElementById("vlmModel").value = p.model || "";
    document.getElementById("vlmApiVersion").value = p.api_version || "";
    document.getElementById("vlmAuthMode").value = p.auth_mode || "auto";
  });

  document.getElementById("btnTestVlm").addEventListener("click", async () => {
    const btn = document.getElementById("btnTestVlm");
    const banner = document.getElementById("vlmTestResultBox");
    banner.classList.remove("hidden", "test-ok", "test-err");
    banner.className = "test-result-banner";
    banner.textContent = "正在向模型发送 64×64 测试车位切片...";
    btn.disabled = true;

    try {
      const vlmDraft = collectVlmFormValues();
      const res = await api("/api/vlm/test", {
        method: "POST",
        body: JSON.stringify({ vlm: vlmDraft }),
      });
      banner.classList.add("test-ok");
      banner.innerHTML = `<strong>连接成功 (${res.latency_ms} ms)</strong> · 模型响应：<code>${escapeHtml(
        res.reply || ""
      )}</code><br/><small>解析地址：${escapeHtml(res.resolved_url)}</small>`;
    } catch (err) {
      const p = err.payload || {};
      banner.classList.add("test-err");
      banner.innerHTML = `<strong>连接未通过：</strong>${escapeHtml(err.message)}${
        p.resolved_url ? `<br/><small>请求地址：${escapeHtml(p.resolved_url)}</small>` : ""
      }`;
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("btnSaveConfig").addEventListener("click", async () => {
    const payload = {
      debounce_seconds: Number(document.getElementById("cfgDebounceSec").value || 8),
      soft_lock_ttl_seconds: Number(document.getElementById("cfgSoftLockSec").value || 180),
      vlm: collectVlmFormValues(),
    };
    const updated = await api("/api/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    document.getElementById("vlmApiKey").value = "";
    populateConfigUI(updated);
    document.getElementById("cfgSaveMsg").textContent = "设置已保存并立即生效";
    setTimeout(() => {
      closeModelModal();
    }, 650);
  });
}

function collectVlmFormValues() {
  return {
    mode: document.getElementById("vlmModeSelect").value,
    preset: document.getElementById("vlmPresetSelect").value,
    base_url: document.getElementById("vlmBaseUrl").value.trim(),
    model: document.getElementById("vlmModel").value.trim(),
    api_version: document.getElementById("vlmApiVersion").value.trim(),
    auth_mode: document.getElementById("vlmAuthMode").value,
    api_key: document.getElementById("vlmApiKey").value.trim(),
  };
}

function populateConfigUI(cfg) {
  if (!cfg) return;
  document.getElementById("cfgDebounceSec").value = cfg.debounce_seconds;
  document.getElementById("cfgSoftLockSec").value = cfg.soft_lock_ttl_seconds;
  const v = cfg.vlm || {};
  document.getElementById("vlmModeSelect").value = v.mode || "hybrid";
  if (v.preset && document.querySelector(`#vlmPresetSelect option[value="${v.preset}"]`)) {
    document.getElementById("vlmPresetSelect").value = v.preset;
  }
  document.getElementById("vlmBaseUrl").value = v.base_url || "";
  document.getElementById("vlmModel").value = v.model || "gpt-4o";
  document.getElementById("vlmApiVersion").value = v.api_version || "2024-12-01-preview";
  document.getElementById("vlmAuthMode").value = v.auth_mode || "auto";
  document.getElementById("apiKeyStatus").textContent = v.has_api_key
    ? `已保存 (${v.api_key_masked})`
    : "未设置密钥";

  // Update header pill & Lab card summary
  const isConfigured =
    v.mode !== "local_only" &&
    Boolean(v.base_url) &&
    (v.has_api_key || v.base_url.includes("127.0.0.1") || v.base_url.includes("localhost"));

  const dot = document.getElementById("modelStatusDot");
  const pillTxt = document.getElementById("modelStatusText");
  const modeLabels = {
    hybrid: "智能混合仲裁",
    always_vlm: "全量模型直判",
    local_only: "纯本地引擎",
  };
  document.getElementById("labModeBadge").textContent = modeLabels[v.mode] || "混合模式";

  if (isConfigured) {
    dot.className = "status-dot dot-ready";
    pillTxt.textContent = `Azure / ${v.model || "VLM"} 已就绪`;
    document.getElementById("labModelSummaryText").innerHTML =
      `已接入模型 <strong>${escapeHtml(v.model || "")}</strong>（策略：${
        modeLabels[v.mode]
      }），慢路调用累计 <strong>${state.overview?.privacy_stats?.slow_lane_vlm_calls || 0}</strong> 次。`;
  } else {
    dot.className = "status-dot dot-idle";
    pillTxt.textContent = v.mode === "local_only" ? "本地视觉模式" : "配置 Azure 模型";
    document.getElementById("labModelSummaryText").textContent =
      "当前运行本地 OpenCV 几何与色彩引擎。点击下方按钮填入 Azure AI Foundry 部署信息即可启用多模态识别。";
  }
}

/* ==================== TAB 1: DRIVER GUIDANCE & ARCHITECTURAL MAP ==================== */
function bindDriverActions() {
  document.getElementById("unitSelect").addEventListener("change", () => {
    updateSiriHint();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });
  document.getElementById("vehicleTypeSelect").addEventListener("change", () => {
    updateSiriHint();
    refreshOverview();
  });
  document.getElementById("btnRefreshOverview").addEventListener("click", refreshOverview);

  document.getElementById("btnRecommendPrimary").addEventListener("click", () =>
    triggerRecommendation("业主车辆 A")
  );
  document.getElementById("btnRecommendSecondCar").addEventListener("click", () =>
    triggerRecommendation("同单元后车 B")
  );
  document.getElementById("btnClearLocks").addEventListener("click", async () => {
    await api("/api/locks", { method: "DELETE" });
    state.lastRecommendedZoneId = null;
    await refreshOverview();
  });
  document.getElementById("btnCopySiri").addEventListener("click", async () => {
    const url = `${location.origin}/api/recommend/voice?unit_id=${
      document.getElementById("unitSelect").value
    }&vehicle_type=${document.getElementById("vehicleTypeSelect").value}`;
    try {
      await navigator.clipboard.writeText(url);
      document.getElementById("btnCopySiri").textContent = "已复制";
      setTimeout(() => (document.getElementById("btnCopySiri").textContent = "复制链接"), 1800);
    } catch (_) {}
  });
}

function updateSiriHint() {
  const uid = document.getElementById("unitSelect").value || "3-1";
  const vt = document.getElementById("vehicleTypeSelect").value || "sedan";
  document.getElementById(
    "siriEndpointCode"
  ).textContent = `GET ${location.origin}/api/recommend/voice?unit_id=${uid}&vehicle_type=${vt}`;
}

async function triggerRecommendation(driverTag) {
  const unit_id = document.getElementById("unitSelect").value;
  const vehicle_type = document.getElementById("vehicleTypeSelect").value;
  const res = await api("/api/recommend", {
    method: "POST",
    body: JSON.stringify({ unit_id, vehicle_type, driver_tag: driverTag, acquire_lock: true }),
  });

  state.lastRecommendedZoneId = res.primary ? res.primary.zone_id : null;
  const box = document.getElementById("voiceResultBox");
  box.classList.remove("hidden");
  document.getElementById("voiceText").textContent = res.voice_text;

  if (res.primary) {
    document.getElementById("recMeta").innerHTML =
      `首选：<strong>${escapeHtml(res.primary.zone_name)}</strong>（步行 ${
        res.primary.walk_meters
      }m · 代价分 ${res.primary.score}）` +
      (res.lock_created
        ? ` · 已锁定 180 秒在途名额 (#${res.lock_created.lock_id})`
        : "");
  } else {
    document.getElementById("recMeta").textContent = "全区满位或在途车辆已预占剩余孤位";
  }

  speakChinese(res.voice_text);
  await refreshOverview();
}

function speakChinese(text) {
  if (!document.getElementById("ttsEnabled").checked) return;
  if (!("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "zh-CN";
    utter.rate = 1.05;
    window.speechSynthesis.speak(utter);
  } catch (_) {}
}

function renderSoftLocks(locks) {
  const el = document.getElementById("softLocksList");
  if (!locks || locks.length === 0) {
    el.textContent = "当前无在途车辆，全部空位开放推荐";
    return;
  }
  el.innerHTML = locks
    .map(
      (lk) =>
        `<div class="lock-row">
          <span>${escapeHtml(lk.driver_tag)} → <strong>${escapeHtml(
          lk.zone_name || lk.zone_id
        )}</strong></span>
          <span>${lk.remaining_seconds}s 后释放</span>
        </div>`
    )
    .join("");
}

function renderAbstractSvgMap(data) {
  const svg = document.getElementById("communitySvg");
  const selectedUnitId = document.getElementById("unitSelect").value;

  let html = `
    <rect x="0.5" y="0.5" width="99" height="99" rx="4" fill="#F8FAFC" stroke="#E2E8F0" stroke-width="0.5"/>
    <!-- Clean Apple Maps Style Internal Roads -->
    <path d="M 8 83 H 88 V 22 H 12" fill="none" stroke="#E2E8F0" stroke-width="6.2" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M 8 83 H 88 V 22 H 12" fill="none" stroke="#FFFFFF" stroke-width="0.45" stroke-dasharray="1.8,1.8"/>
    <!-- Community Gates -->
    <rect x="40" y="94.5" width="14" height="4.2" rx="1.8" fill="#0071E3"/>
    <text x="47" y="97.4" font-size="2.4" fill="#FFFFFF" font-weight="600" text-anchor="middle">南门入口</text>
    <rect x="93.5" y="46" width="5.5" height="12" rx="1.8" fill="#0071E3"/>
    <text x="96.2" y="53" font-size="2.2" fill="#FFFFFF" font-weight="600" text-anchor="middle">东门</text>
  `;

  for (const z of data.zones) {
    const isRec = z.zone_id === state.lastRecommendedZoneId;
    const boxFill = isRec ? "#EAFBF0" : "#FFFFFF";
    const boxStroke = isRec ? "#28CD41" : "#CBD5E1";
    const strokeW = isRec ? "0.8" : "0.45";

    html += `
      <g>
        <rect x="${z.map_x}" y="${z.map_y}" width="${z.map_w}" height="${z.map_h}" rx="2"
              fill="${boxFill}" stroke="${boxStroke}" stroke-width="${strokeW}"/>
        <text x="${z.map_x + 1.4}" y="${z.map_y + 3.1}" font-size="2.35" fill="#1D1D1F" font-weight="600">
          ${escapeHtml(z.name)} · 余${z.effective_free}
        </text>
    `;

    const spots = z.spots || [];
    const n = Math.max(1, spots.length);
    const pad = 1.4;
    const availW = z.map_w - pad * 2;
    const cellW = Math.min(5.8, (availW - (n - 1) * 0.7) / n);
    spots.forEach((sp, idx) => {
      let fill = "#28CD41";
      if (sp.status === "OCCUPIED") fill = "#FF3B30";
      else if (sp.status === "BLOCKED") fill = "#FF9500";
      else if (sp.status === "UNKNOWN") fill = "#94A3B8";
      const sx = z.map_x + pad + idx * (cellW + 0.7);
      const sy = z.map_y + 4.4;
      const sh = Math.max(3.2, z.map_h - 5.6);
      const dash = sp.pending_status
        ? 'stroke="#5856D6" stroke-width="0.65" stroke-dasharray="0.9,0.6"'
        : "";
      html += `<rect x="${sx}" y="${sy}" width="${cellW}" height="${sh}" rx="0.8" fill="${fill}" ${dash}>
        <title>${escapeHtml(sp.label)}: ${sp.status} (${escapeHtml(sp.last_reason)})</title>
      </rect>`;
      if (sp.spot_type === "elastic_strip") {
        html += `<text x="${sx + cellW / 2}" y="${sy + sh / 2 + 0.8}" font-size="2.0" fill="#FFFFFF" font-weight="700" text-anchor="middle">余${sp.free_units}位</text>`;
      }
    });

    if (z.soft_locked > 0) {
      html += `<rect x="${z.map_x + z.map_w - 10.6}" y="${z.map_y + 0.8}" width="9.8" height="3.0" rx="1.2" fill="#0071E3"/>
               <text x="${z.map_x + z.map_w - 5.7}" y="${z.map_y + 2.9}" font-size="1.95" fill="#FFFFFF" font-weight="600" text-anchor="middle">在途×${z.soft_locked}</text>`;
    }
    html += `</g>`;
  }

  for (const u of data.units) {
    const isHome = u.unit_id === selectedUnitId;
    html += `
      <g>
        <rect x="${u.map_x - 6.2}" y="${u.map_y - 3.8}" width="12.4" height="6.8" rx="1.6"
              fill="${isHome ? "#0071E3" : "#FFFFFF"}"
              stroke="${isHome ? "#0071E3" : "#94A3B8"}" stroke-width="0.55"/>
        <text x="${u.map_x}" y="${u.map_y + 0.4}" font-size="2.25"
              fill="${isHome ? "#FFFFFF" : "#1D1D1F"}" text-anchor="middle" font-weight="600">
          ${isHome ? "★ " : ""}${escapeHtml(u.unit_id)}栋
        </text>
      </g>
    `;
  }

  svg.innerHTML = html;
}

function renderZoneCards(zones) {
  const wrap = document.getElementById("zoneCardsGrid");
  wrap.innerHTML = zones
    .map((z) => {
      const isRec = z.zone_id === state.lastRecommendedZoneId;
      const pills = (z.spots || [])
        .map((s) => {
          const pCls = s.pending_status ? "chip-pending" : "";
          const extra =
            s.spot_type === "elastic_strip"
              ? ` · 可停${s.free_units}辆`
              : s.compact_only
              ? " · 小车"
              : "";
          const pendTxt = s.pending_status ? ` → 待确认(${s.pending_status})` : "";
          return `<span class="spot-chip chip-${s.status} ${pCls}" title="${escapeHtml(
            s.last_reason
          )}">${escapeHtml(s.label)}${extra}${pendTxt}</span>`;
        })
        .join("");

      return `
        <div class="zone-tile ${isRec ? "recommended" : ""}">
          <div class="zone-tile-head">
            <span>${isRec ? "首选 · " : ""}${escapeHtml(z.name)}</span>
            <span style="color:${z.effective_free > 0 ? "#1D9E31" : "#D70015"}">
              净余 ${z.effective_free} 位${z.soft_locked > 0 ? ` (在途-${z.soft_locked})` : ""}
            </span>
          </div>
          <div class="zone-tile-sub">${escapeHtml(z.description)}</div>
          <div class="spot-badges">${pills}</div>
        </div>
      `;
    })
    .join("");
}

/* ==================== TAB 2: CALIBRATION CONSOLE ==================== */
function bindCalibrationConsole() {
  const canvas = document.getElementById("calibCanvas");

  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.calib.mode = btn.dataset.mode;
      state.calib.draftPoints = [];
      state.calib.previewTrapezoids = [];
      document.getElementById("ctrlPerspective").classList.toggle("hidden", state.calib.mode !== "perspective_row");
      document.getElementById("ctrlIrregular").classList.toggle("hidden", state.calib.mode !== "irregular_poly");
      document.getElementById("ctrlElastic").classList.toggle("hidden", state.calib.mode !== "elastic_strip");
      document.getElementById("ctrlEditVertices").classList.toggle("hidden", state.calib.mode !== "edit_vertices");
      updateCalibButtons();
      renderCalibCanvas();
    });
  });

  document.getElementById("calibCameraSelect").addEventListener("change", async (e) => {
    state.calib.cameraId = e.target.value;
    await loadCameraSpotsForCalib(state.calib.cameraId);
  });

  document.getElementById("calibZoneSelect").addEventListener("change", (e) => {
    state.calib.zoneId = e.target.value;
  });

  document.getElementById("btnCreateCamera").addEventListener("click", async () => {
    const camera_id = document.getElementById("newCamId").value.trim();
    const name = document.getElementById("newCamName").value.trim();
    const zone_id = document.getElementById("calibZoneSelect").value;
    if (!camera_id || !name) return alert("请填写机位代号与名称");
    await api("/api/cameras", {
      method: "POST",
      body: JSON.stringify({
        camera_id,
        name,
        zone_id,
        image_width: state.calib.imgW,
        image_height: state.calib.imgH,
      }),
    });
    state.calib.cameraId = camera_id;
    await refreshOverview();
    document.getElementById("calibCameraSelect").value = camera_id;
    await loadCameraSpotsForCalib(camera_id);
  });

  document.getElementById("calibImageInput").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const img = new Image();
      img.onload = () => {
        state.calib.bgImage = img;
        state.calib.imgW = img.naturalWidth;
        state.calib.imgH = img.naturalHeight;
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        renderCalibCanvas();
      };
      img.src = ev.target.result;
    };
    reader.readAsDataURL(file);
  });

  document.getElementById("btnGenDemoScene").addEventListener("click", () => {
    state.calib.bgImage = null;
    state.calib.imgW = 1280;
    state.calib.imgH = 720;
    canvas.width = 1280;
    canvas.height = 720;
    drawSyntheticDemoLotOnCalibCanvas();
    renderCalibCanvas();
  });

  const recomputeRow = async () => {
    document.getElementById("rowGammaVal").textContent = Number(
      document.getElementById("rowGamma").value
    ).toFixed(2);
    if (state.calib.mode === "perspective_row" && state.calib.draftPoints.length === 4) {
      const res = await api("/api/calibration/subdivide", {
        method: "POST",
        body: JSON.stringify({
          outer_quad: state.calib.draftPoints,
          count: Number(document.getElementById("rowSpotCount").value || 5),
          direction: document.getElementById("rowDirection").value,
          foreshortening_gamma: Number(document.getElementById("rowGamma").value || 1.0),
          gap_ratio: 0.02,
        }),
      });
      state.calib.previewTrapezoids = res.trapezoids;
      renderCalibCanvas();
    }
  };
  document.getElementById("rowSpotCount").addEventListener("input", recomputeRow);
  document.getElementById("rowDirection").addEventListener("change", recomputeRow);
  document.getElementById("rowGamma").addEventListener("input", recomputeRow);

  document.getElementById("btnCommitRow").addEventListener("click", () => {
    if (!state.calib.previewTrapezoids.length) return;
    const baseIdx = state.calib.spots.length + 1;
    state.calib.previewTrapezoids.forEach((poly, i) => {
      state.calib.spots.push({
        label: `车位-${String(baseIdx + i).padStart(2, "0")}`,
        spot_type: "standard",
        compact_only: false,
        total_meters: 5.2,
        capacity: 1,
        polygon: poly,
      });
    });
    state.calib.draftPoints = [];
    state.calib.previewTrapezoids = [];
    updateCalibButtons();
    renderSpotListSidebar();
    renderCalibCanvas();
  });

  document.getElementById("btnCancelDraft").addEventListener("click", () => {
    state.calib.draftPoints = [];
    state.calib.previewTrapezoids = [];
    updateCalibButtons();
    renderCalibCanvas();
  });

  document.getElementById("btnFinishIrregular").addEventListener("click", () => {
    if (state.calib.draftPoints.length < 3) return;
    const compact = document.getElementById("irrCompactOnly").checked;
    const idx = state.calib.spots.length + 1;
    state.calib.spots.push({
      label: compact ? `异形小车-${idx}` : `异形位-${idx}`,
      spot_type: "irregular",
      compact_only: compact,
      total_meters: compact ? 4.3 : 5.2,
      capacity: 1,
      polygon: [...state.calib.draftPoints],
    });
    state.calib.draftPoints = [];
    updateCalibButtons();
    renderSpotListSidebar();
    renderCalibCanvas();
  });

  document.getElementById("btnCancelIrregular").addEventListener("click", () => {
    state.calib.draftPoints = [];
    updateCalibButtons();
    renderCalibCanvas();
  });

  document.getElementById("btnFinishElastic").addEventListener("click", () => {
    if (state.calib.draftPoints.length < 4) return;
    const meters = Number(document.getElementById("stripMeters").value || 18.0);
    const cap = Math.max(1, Math.floor(meters / 5.2));
    state.calib.spots.push({
      label: `${meters}m弹性顺停带`,
      spot_type: "elastic_strip",
      compact_only: false,
      total_meters: meters,
      capacity: cap,
      polygon: state.calib.draftPoints.slice(0, 4),
    });
    state.calib.draftPoints = [];
    updateCalibButtons();
    renderSpotListSidebar();
    renderCalibCanvas();
  });

  document.getElementById("btnCancelElastic").addEventListener("click", () => {
    state.calib.draftPoints = [];
    updateCalibButtons();
    renderCalibCanvas();
  });

  const getCanvasCoords = (evt) => {
    const rect = canvas.getBoundingClientRect();
    return [
      Math.round((evt.clientX - rect.left) * (canvas.width / rect.width)),
      Math.round((evt.clientY - rect.top) * (canvas.height / rect.height)),
    ];
  };

  canvas.addEventListener("mousedown", async (evt) => {
    const [mx, my] = getCanvasCoords(evt);
    const hitRadius = Math.max(12, canvas.width * 0.012);

    for (let i = 0; i < state.calib.draftPoints.length; i++) {
      const [px, py] = state.calib.draftPoints[i];
      if (Math.hypot(mx - px, my - py) <= hitRadius) {
        state.calib.draggingHandle = { type: "draft", ptIdx: i };
        return;
      }
    }

    if (state.calib.mode === "edit_vertices") {
      for (let sIdx = 0; sIdx < state.calib.spots.length; sIdx++) {
        const poly = state.calib.spots[sIdx].polygon;
        for (let pIdx = 0; pIdx < poly.length; pIdx++) {
          const [px, py] = poly[pIdx];
          if (Math.hypot(mx - px, my - py) <= hitRadius) {
            state.calib.draggingHandle = { type: "spot", spotIdx: sIdx, ptIdx: pIdx };
            return;
          }
        }
      }
      return;
    }

    if (state.calib.mode === "perspective_row" && state.calib.draftPoints.length < 4) {
      state.calib.draftPoints.push([mx, my]);
      if (state.calib.draftPoints.length === 4) await recomputeRow();
    } else if (state.calib.mode === "irregular_poly") {
      state.calib.draftPoints.push([mx, my]);
    } else if (state.calib.mode === "elastic_strip" && state.calib.draftPoints.length < 4) {
      state.calib.draftPoints.push([mx, my]);
    }
    updateCalibButtons();
    renderCalibCanvas();
  });

  canvas.addEventListener("mousemove", async (evt) => {
    if (!state.calib.draggingHandle) return;
    const [mx, my] = getCanvasCoords(evt);
    const h = state.calib.draggingHandle;
    if (h.type === "draft") {
      state.calib.draftPoints[h.ptIdx] = [mx, my];
      if (state.calib.mode === "perspective_row" && state.calib.draftPoints.length === 4) {
        await recomputeRow();
        return;
      }
    } else if (h.type === "spot") {
      state.calib.spots[h.spotIdx].polygon[h.ptIdx] = [mx, my];
    }
    renderCalibCanvas();
  });

  window.addEventListener("mouseup", () => {
    state.calib.draggingHandle = null;
  });

  document.getElementById("btnClearAllSpots").addEventListener("click", () => {
    state.calib.spots = [];
    state.calib.draftPoints = [];
    state.calib.previewTrapezoids = [];
    updateCalibButtons();
    renderSpotListSidebar();
    renderCalibCanvas();
  });

  document.getElementById("btnSaveCalibration").addEventListener("click", async () => {
    const camId = document.getElementById("calibCameraSelect").value;
    const zoneId = document.getElementById("calibZoneSelect").value;
    const res = await api(`/api/cameras/${encodeURIComponent(camId)}/spots`, {
      method: "POST",
      body: JSON.stringify({
        zone_id: zoneId,
        image_width: state.calib.imgW,
        image_height: state.calib.imgH,
        spots: state.calib.spots,
      }),
    });
    state.calib.spots = res.spots;
    renderSpotListSidebar();
    renderCalibCanvas();
    document.getElementById(
      "calibSaveMsg"
    ).textContent = `已保存 ${res.spots.length} 个标定框至 ${camId}`;
    await refreshOverview();
  });
}

async function loadCameraSpotsForCalib(cameraId) {
  const data = await api(`/api/cameras/${encodeURIComponent(cameraId)}/spots`);
  state.calib.cameraId = cameraId;
  state.calib.zoneId = data.camera.zone_id;
  document.getElementById("calibZoneSelect").value = data.camera.zone_id;
  if (!state.calib.bgImage) {
    state.calib.imgW = data.camera.image_width || 1280;
    state.calib.imgH = data.camera.image_height || 720;
    const canvas = document.getElementById("calibCanvas");
    canvas.width = state.calib.imgW;
    canvas.height = state.calib.imgH;
  }
  state.calib.spots = data.spots || [];
  state.calib.draftPoints = [];
  state.calib.previewTrapezoids = [];
  updateCalibButtons();
  renderSpotListSidebar();
  renderCalibCanvas();
}

function updateCalibButtons() {
  document.getElementById("btnCommitRow").disabled = state.calib.previewTrapezoids.length === 0;
  document.getElementById("btnFinishIrregular").disabled = state.calib.draftPoints.length < 3;
  document.getElementById("btnFinishElastic").disabled = state.calib.draftPoints.length < 4;
}

function renderSpotListSidebar() {
  document.getElementById("calibSpotCount").textContent = state.calib.spots.length;
  const list = document.getElementById("calibSpotList");
  if (!state.calib.spots.length) {
    list.innerHTML = `<div class="locks-container">暂未框选区域，请在左侧画布点击标定</div>`;
    return;
  }
  list.innerHTML = state.calib.spots
    .map((sp, i) => {
      const badge =
        sp.spot_type === "elastic_strip"
          ? `弹性带 ${sp.total_meters}m`
          : sp.spot_type === "irregular"
          ? sp.compact_only
            ? "异形小车"
            : "异形框"
          : "透视梯形";
      return `
        <div class="calib-spot-item">
          <div>
            <strong>${escapeHtml(sp.label)}</strong>
            <small>${badge}</small>
          </div>
          <button class="link-btn danger-link" type="button" onclick="removeCalibSpot(${i})">移除</button>
        </div>
      `;
    })
    .join("");
}

window.removeCalibSpot = function (idx) {
  state.calib.spots.splice(idx, 1);
  renderSpotListSidebar();
  renderCalibCanvas();
};

function drawSyntheticDemoLotOnCalibCanvas(ctxTarget, w = 1280, h = 720) {
  const canvas = ctxTarget || document.getElementById("calibCanvas");
  const ctx = canvas.getContext("2d");

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#2C2C2E");
  grad.addColorStop(0.4, "#3A3A3C");
  grad.addColorStop(1, "#242426");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);

  // Subtle greenery belt
  ctx.fillStyle = "#244232";
  ctx.beginPath();
  ctx.moveTo(0, 310);
  ctx.lineTo(w, 270);
  ctx.lineTo(w, 355);
  ctx.lineTo(0, 390);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = "rgba(255,255,255,0.28)";
  ctx.lineWidth = 2.5;
  const demoOuter = [[140, 420], [1120, 390], [1030, 610], [210, 650]];
  ctx.beginPath();
  demoOuter.forEach(([x, y], idx) => (idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
  ctx.closePath();
  ctx.stroke();

  ctx.fillStyle = "rgba(255,255,255,0.68)";
  ctx.font = "500 17px -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.fillText("窗台俯拍透视标定示意（点击右上「载入基准照片」可导入任意 AI 生成图像）", 28, 40);
}

function renderCalibCanvas() {
  const canvas = document.getElementById("calibCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (state.calib.bgImage) {
    ctx.drawImage(state.calib.bgImage, 0, 0, canvas.width, canvas.height);
  } else {
    drawSyntheticDemoLotOnCalibCanvas(canvas, canvas.width, canvas.height);
  }

  state.calib.spots.forEach((sp) => {
    const poly = sp.polygon;
    if (!poly || poly.length < 3) return;
    let stroke = "#34C759";
    let fill = "rgba(52, 199, 89, 0.22)";
    if (sp.spot_type === "irregular") {
      stroke = "#FF9500";
      fill = "rgba(255, 149, 0, 0.22)";
    } else if (sp.spot_type === "elastic_strip") {
      stroke = "#007AFF";
      fill = "rgba(0, 122, 255, 0.22)";
    }
    drawPolygon(ctx, poly, stroke, fill, 2.5);

    const cx = poly.reduce((s, p) => s + p[0], 0) / poly.length;
    const cy = poly.reduce((s, p) => s + p[1], 0) / poly.length;
    drawPillLabel(ctx, cx, cy, sp.label);

    if (state.calib.mode === "edit_vertices") {
      poly.forEach(([x, y]) => drawHandle(ctx, x, y, "#FF9500"));
    }
  });

  if (state.calib.previewTrapezoids.length > 0) {
    state.calib.previewTrapezoids.forEach((trap, idx) => {
      drawPolygon(ctx, trap, "#0A84FF", "rgba(10, 132, 255, 0.28)", 2.4);
      const cx = trap.reduce((s, p) => s + p[0], 0) / 4;
      const cy = trap.reduce((s, p) => s + p[1], 0) / 4;
      drawPillLabel(ctx, cx, cy, `#${idx + 1}`);
    });
  }

  if (state.calib.draftPoints.length > 0) {
    ctx.strokeStyle = "#FF375F";
    ctx.lineWidth = 2.5;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    state.calib.draftPoints.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
    if (state.calib.draftPoints.length >= 3) ctx.closePath();
    ctx.stroke();
    ctx.setLineDash([]);

    state.calib.draftPoints.forEach(([x, y], idx) => {
      drawHandle(ctx, x, y, "#FF375F", String(idx + 1));
    });
  }
}

function drawPolygon(ctx, poly, stroke, fill, lineW = 2) {
  ctx.beginPath();
  poly.forEach(([x, y], idx) => (idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.strokeStyle = stroke;
  ctx.lineWidth = lineW;
  ctx.stroke();
}

function drawPillLabel(ctx, cx, cy, text) {
  ctx.font = "600 13px -apple-system, BlinkMacSystemFont, sans-serif";
  const tw = ctx.measureText(text).width;
  const w = tw + 18;
  const h = 24;
  ctx.fillStyle = "rgba(28, 28, 30, 0.84)";
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(cx - w / 2, cy - h / 2, w, h, 7) : ctx.rect(cx - w / 2, cy - h / 2, w, h);
  ctx.fill();
  ctx.fillStyle = "#FFFFFF";
  ctx.textAlign = "center";
  ctx.fillText(text, cx, cy + 4.5);
  ctx.textAlign = "start";
}

function drawHandle(ctx, x, y, color, label = "") {
  ctx.beginPath();
  ctx.arc(x, y, 7.5, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = "#FFFFFF";
  ctx.lineWidth = 2.2;
  ctx.stroke();
  if (label) {
    ctx.fillStyle = "#FFFFFF";
    ctx.font = "600 12px -apple-system, sans-serif";
    ctx.fillText(label, x + 10, y - 8);
  }
}

/* ==================== TAB 3: SNAPSHOT TEST LAB ==================== */
function bindTestLab() {
  const dropzone = document.getElementById("labDropzone");
  const fileInput = document.getElementById("labImageInput");

  document.getElementById("btnPickLabImage").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files[0]) uploadSnapshotFile(e.target.files[0]);
    fileInput.value = "";
  });

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files[0]) uploadSnapshotFile(e.dataTransfer.files[0]);
  });

  document.querySelectorAll(".synth-btn").forEach((btn) => {
    btn.addEventListener("click", () => generateAndSendSyntheticSnapshot(btn.dataset.scene));
  });

  document.getElementById("btnResetSeed").addEventListener("click", async () => {
    await api("/api/reset", { method: "POST" });
    await refreshOverview();
    await loadCameraSpotsForCalib(state.calib.cameraId);
  });
}

function uploadSnapshotFile(file) {
  const reader = new FileReader();
  reader.onload = async (ev) => {
    await submitSnapshotDataUrl(ev.target.result, `上传图像 (${file.name})`);
  };
  reader.readAsDataURL(file);
}

async function generateAndSendSyntheticSnapshot(sceneType) {
  const camId = document.getElementById("labCameraSelect").value;
  const spotData = await api(`/api/cameras/${encodeURIComponent(camId)}/spots`);
  const w = spotData.camera.image_width || 1280;
  const h = spotData.camera.image_height || 720;
  const off = document.createElement("canvas");
  off.width = w;
  off.height = h;
  const ctx = off.getContext("2d");

  ctx.fillStyle = "#48484A";
  ctx.fillRect(0, 0, w, h);

  spotData.spots.forEach((sp, idx) => {
    const poly = sp.polygon;
    ctx.strokeStyle = "rgba(255,255,255,0.65)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    poly.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
    ctx.closePath();
    ctx.stroke();

    const cx = poly.reduce((s, p) => s + p[0], 0) / poly.length;
    const cy = poly.reduce((s, p) => s + p[1], 0) / poly.length;

    if (sceneType === "mixed_cars_cone") {
      if (idx === 0 || idx === 2) {
        drawSyntheticCarAt(ctx, cx, cy, idx === 0 ? "#0071E3" : "#E5E5EA");
      } else if (idx === 1) {
        ctx.fillStyle = "#FF6B00";
        ctx.beginPath();
        ctx.arc(cx, cy, 20, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "#FFFFFF";
        ctx.lineWidth = 4;
        ctx.stroke();
      }
    } else if (sceneType === "passing_car" && idx === 0) {
      drawSyntheticCarAt(ctx, cx, cy, "#FF3B30");
    }
  });

  const dataUrl = off.toDataURL("image/jpeg", 0.9);
  await submitSnapshotDataUrl(dataUrl, `内置测试帧 (${sceneType})`);
}

function drawSyntheticCarAt(ctx, cx, cy, bodyColor) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.fillStyle = bodyColor;
  ctx.fillRect(-44, -68, 88, 136);
  ctx.fillStyle = "#1C1C1E";
  ctx.fillRect(-34, -42, 68, 28);
  ctx.fillRect(-32, 20, 64, 24);
  ctx.fillStyle = "#F5F5F7";
  ctx.fillRect(-26, -10, 52, 26);
  ctx.fillStyle = "#FFE680";
  ctx.fillRect(-36, -68, 16, 8);
  ctx.fillRect(20, -68, 16, 8);
  ctx.restore();
}

async function submitSnapshotDataUrl(dataUrl, sourceLabel) {
  const camId = document.getElementById("labCameraSelect").value;
  const instantConfirm = document.getElementById("labDebounceMode").value === "instant";
  const simOffset = document.getElementById("labSimTimeOffset").checked ? 10.5 : 0.0;
  const forceReanalyze = document.getElementById("labForceReanalyze").checked;

  const spotData = await api(`/api/cameras/${encodeURIComponent(camId)}/spots`);
  const res = await api(`/api/cameras/${encodeURIComponent(camId)}/snapshot`, {
    method: "POST",
    body: JSON.stringify({
      image_base64: dataUrl,
      instant_confirm: instantConfirm,
      time_offset_seconds: simOffset,
      force_reanalyze: forceReanalyze,
      source_label: sourceLabel,
    }),
  });

  renderLabOverlayAndReport(dataUrl, spotData, res);
  await refreshOverview();
}

function renderLabOverlayAndReport(dataUrl, spotData, res) {
  document.getElementById("labPreviewWrap").classList.remove("hidden");
  document.getElementById("labReportsWrap").classList.remove("hidden");
  document.getElementById("labTimingBadge").textContent = `${res.elapsed_ms} ms · 0 字节落盘 · ${
    res.instant_confirm_mode ? "单帧直通" : `双帧防抖 (${res.debounce_seconds}s)`
  }`;

  const canvas = document.getElementById("labResultCanvas");
  const ctx = canvas.getContext("2d");
  const img = new Image();
  img.onload = () => {
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    ctx.drawImage(img, 0, 0);

    const calW = spotData.camera.image_width || img.naturalWidth;
    const calH = spotData.camera.image_height || img.naturalHeight;
    const sx = img.naturalWidth / calW;
    const sy = img.naturalHeight / calH;

    const repMap = {};
    res.reports.forEach((r) => (repMap[r.spot_id] = r));

    spotData.spots.forEach((sp) => {
      const r = repMap[sp.spot_id];
      const scaled = sp.polygon.map(([x, y]) => [x * sx, y * sy]);
      const st = r ? r.fsm.committed_status : sp.status;
      const pend = r ? r.fsm.pending_status : null;

      let stroke = "#34C759";
      let fill = "rgba(52, 199, 89, 0.24)";
      if (st === "OCCUPIED") {
        stroke = "#FF3B30";
        fill = "rgba(255, 59, 48, 0.26)";
      } else if (st === "BLOCKED") {
        stroke = "#FF9500";
        fill = "rgba(255, 149, 0, 0.28)";
      }
      if (pend) stroke = "#5856D6";

      drawPolygon(ctx, scaled, stroke, fill, 3);
      const cx = scaled.reduce((s, p) => s + p[0], 0) / scaled.length;
      const cy = scaled.reduce((s, p) => s + p[1], 0) / scaled.length;
      const tag = pend ? `${sp.label} · 待确认(${pend})` : `${sp.label} · ${st}`;
      drawPillLabel(ctx, cx, cy, tag);
    });
  };
  img.src = dataUrl;

  const tbody = document.getElementById("labReportTbody");
  tbody.innerHTML = res.reports
    .map(
      (r) => `
      <tr>
        <td><strong>${escapeHtml(r.label)}</strong></td>
        <td>${escapeHtml(r.spot_type)}</td>
        <td>${(r.diff_score * 100).toFixed(1)}%</td>
        <td><code>${escapeHtml(r.stage_used)}</code></td>
        <td><span class="spot-chip chip-${r.observed_status}">${r.observed_status}</span> (${Math.round(
        r.confidence * 100
      )}%)</td>
        <td>
          <span class="spot-chip chip-${r.fsm.committed_status}">${r.fsm.committed_status}</span>
          ${r.fsm.pending_status ? ` → <strong>待确认 ${r.fsm.pending_status}</strong>` : ""}
          <div style="color:#6E6E73;font-size:11.5px;margin-top:2px">${escapeHtml(r.fsm.note)}</div>
        </td>
        <td>${escapeHtml(r.reason)}</td>
      </tr>
    `
    )
    .join("");
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
