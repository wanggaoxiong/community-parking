/* Community Parking Welfare System - Cupertino HIG Frontend Controller */
const VEHICLE_MODELS_CLIENT = {
  sedan: { key: "sedan", label: "标准竖停轿车", code: "V", w: 1.75, h: 3.25, compact_only: 0, angleOffset: 0 },
  parallel: { key: "parallel", label: "横向顺停轿车", code: "H", w: 3.75, h: 1.85, compact_only: 0, angleOffset: 0 },
  suv: { key: "suv", label: "SUV宽体车位", code: "V", w: 2.05, h: 3.65, compact_only: 0, angleOffset: 0 },
  compact: { key: "compact", label: "紧凑小车位", code: "V", w: 1.55, h: 2.85, compact_only: 1, angleOffset: 0 },
  angled: { key: "angled", label: "45°斜列式车位", code: "V", w: 1.80, h: 3.30, compact_only: 0, angleOffset: 45 },
};

function parseSingleGroupTokenClient(rawToken) {
  const token = String(rawToken || "").trim();
  if (!token) return null;
  let cleaned = token.replace(/号楼|栋|楼/g, "#");
  cleaned = cleaned
    .replace(/左侧|左边/g, "-L-")
    .replace(/右侧|右边/g, "-R-")
    .replace(/前面|前侧|前排/g, "-F-")
    .replace(/后面|后侧|后排|背面/g, "-B-")
    .replace(/侧面|侧边|旁侧/g, "-S-")
    .replace(/东侧/g, "-E-")
    .replace(/西侧/g, "-W-")
    .replace(/南侧/g, "-F-")
    .replace(/北侧/g, "-B-")
    .replace(/竖着|竖向|垂直|竖排|倒库/g, "-V-")
    .replace(/横着|横向|平行|顺停|侧方/g, "-H-")
    .replace(/个停车位|个车位|车位|位/g, "");
  const m = cleaned.match(
    /^([A-Za-z0-9]+)[\s#_\-]*?(left|right|front|backend|back|rear|side|east|west|south|north|[LRFBSEWN])[\s_\-]*?(vertical|vert|perp|perpendicular|horizontal|horiz|parallel|[VH])[\s_\-]*?(\d{1,2})$/i
  );
  if (!m) return null;
  const bldg = m[1].toUpperCase();
  const dirRaw = m[2].toLowerCase();
  const oriRaw = m[3].toLowerCase();
  const count = Math.max(1, Math.min(60, parseInt(m[4], 10) || 1));
  const dirMap = {
    l: ["L", "左侧"], left: ["L", "左侧"],
    r: ["R", "右侧"], right: ["R", "右侧"],
    f: ["F", "前面"], front: ["F", "前面"], south: ["F", "南侧"],
    b: ["B", "后面"], backend: ["B", "后面"], back: ["B", "后面"], rear: ["B", "后面"], north: ["B", "北侧"],
    s: ["S", "侧面"], side: ["S", "侧面"],
    e: ["E", "东侧"], east: ["E", "东侧"],
    w: ["W", "西侧"], west: ["W", "西侧"],
  };
  const oriMap = {
    v: ["V", "竖着", "sedan"], vertical: ["V", "竖着", "sedan"], vert: ["V", "竖着", "sedan"], perp: ["V", "竖着", "sedan"], perpendicular: ["V", "竖着", "sedan"],
    h: ["H", "横着", "parallel"], horizontal: ["H", "横着", "parallel"], horiz: ["H", "横着", "parallel"], parallel: ["H", "横着", "parallel"],
  };
  const [dCode, dCn] = dirMap[dirRaw] || ["S", "侧面"];
  const [oCode, oCn, defModel] = oriMap[oriRaw] || ["V", "竖着", "sedan"];
  const code = `${bldg}${dCode}${oCode}${count}`;
  const bldgCn = /^\d+$/.test(bldg) ? `${bldg}号楼` : `${bldg}栋`;
  return {
    code,
    building: bldg,
    building_cn: bldgCn,
    direction_code: dCode,
    direction_cn: dCn,
    orientation_code: oCode,
    orientation_cn: oCn,
    count,
    default_model: defModel,
    short_cn: `${bldgCn}${dCn}${oCn}${count}个`,
    full_cn: `${bldgCn}${dCn}${oCn}${count}个停车位`,
    description_cn: `${bldgCn}${dCn}${oCn}${count}个停车位`,
  };
}

function parseCameraGroupCodesClient(rawCode, mountAddress = "") {
  const parts = String(rawCode || "")
    .split(/[+,，、;/&\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const groups = [];
  parts.forEach((p) => {
    const g = parseSingleGroupTokenClient(p);
    if (g) groups.push(g);
  });
  const addrClean = String(mountAddress || "").trim().replace(/^[（(]|[）)]$/g, "");
  if (!groups.length) {
    return {
      valid: false,
      group_code: String(rawCode || "").trim(),
      groups: [],
      total_spots: 0,
      display_name: addrClean ? `自定义机位（${addrClean}）` : "",
      suggested_camera_id: "",
    };
  }
  const canonicalCode = groups.map((g) => g.code).join("+");
  const totalSpots = groups.reduce((a, g) => a + g.count, 0);
  const sameBldgDir = new Set(groups.map((g) => `${g.building}_${g.direction_code}`)).size === 1;
  let baseDesc = "";
  if (groups.length === 1) {
    baseDesc = groups[0].full_cn;
  } else if (sameBldgDir) {
    const g0 = groups[0];
    baseDesc = `${g0.building_cn}${g0.direction_cn}${groups.map((g) => `${g.orientation_cn}${g.count}个`).join("+")}停车位`;
  } else {
    baseDesc = `${groups.map((g) => g.short_cn).join("+")}停车位`;
  }
  const displayName = addrClean ? `${baseDesc}（${addrClean}）` : baseDesc;
  const suggestedCameraId = `CAM_${groups.map((g) => g.code).join("_")}`;
  return {
    valid: true,
    group_code: canonicalCode,
    groups,
    total_spots: totalSpots,
    base_name: baseDesc,
    mount_address: addrClean,
    display_name: displayName,
    suggested_name: displayName,
    normalized_group_code: canonicalCode,
    suggested_camera_id: suggestedCameraId,
  };
}

const state = {
  overview: null,
  lastRecommendedZoneId: null,
  lastRecommendedGroupCode: null,
  lastRecommendedRoute: null,
  lastDoorWalkPath: null,
  govViewMode: "building",
  govSearchQuery: "",
  siteMap: {
    driverCollapsed: false,
    driverAutoCollapsedByEdit: false,
    compareSplit: false,
    userToggledCompare: false,
    inspectorOpen: true,
    refWireframe: true,
    syncCursorWorld: null,
    savedBrowseBackdropAlpha: 0.58,
    showBackdrop: false,
    userToggledBackdrop: false,
    backdropAlpha: 0.58,
    backdropDataUrl: "",
    backdropUpdatedAt: 0,
    zoom: 1.0,
    camCenterX: null,
    camCenterY: null,
    spacePressed: false,
    editMode: false,
    marqueeMode: true,
    subTool: "spots", // spots | routes | roads | nodes | pan
    paletteModel: "sedan",
    placeMode: null, // null | "spots" | "roads" | "routes"
    placeReverseDir: false,
    pendingCameraPlacement: null, // { cameraId, baseCode, count, orientation, angleOffset, groupName, nearUnitId, zoneId, spotStatuses }
    draftSpots: null,
    draftZones: null,
    draftUnits: null,
    draftRoads: null,
    draftRoutes: null,
    draftWalkways: null,
    draftSpanMeters: 185,
    draftGates: null,
    draftGreens: null,
    deletedSpotIds: [],
    deletedUnitIds: [],
    selectedSpotId: null,
    selectedSpotIds: [],
    selectedGroupCamId: null,
    spotPlaceCountMode: "auto", // auto (span length determines count) | fixed (exact N cars)
    selectedRouteIdx: -1,
    selectedRoutePtIdx: -1,
    drawingRoute: false,
    newRoutePts: [],
    selectedRoadIdx: -1,
    selectedRoadPtIdx: -1,
    selectedWalkwayIdx: -1,
    selectedWalkwayPtIdx: -1,
    drawingWalkway: false,
    newWalkwayPts: [],
    walkwaySnapTarget: null,
    selectedNodeKind: null, // unit | gate
    selectedNodeId: null,
    drawingRoad: false,
    newRoadPts: [],
    cursorWorld: null,
    dragging: null,
    tagFilter: "all",
    pendingSpotTagClick: null,
    undoStack: [],
    redoStack: [],
  },
  tagPopover: {
    open: false,
    spotIds: [],
    draftTags: [],
    anchorX: 0,
    anchorY: 0,
  },
  calib: {
    cameraId: "CAM_3F_WIN",
    zoneId: "Z_BLDG3_EAST",
    mode: "smart_edit", // smart_edit | curved_row | perspective_row | irregular_poly | elastic_strip
    bgImage: null, // active display image mirror
    bgDataUrl: null,
    groundBgImage: null,
    groundBgDataUrl: null,
    groundBgIsServerPreview: false,
    templateMeta: null,
    vehicleBgImage: null,
    vehicleBgDataUrl: null,
    vehicleBgIsServerPreview: false,
    vehicleTemplateMeta: null,
    cameraMeta: null,
    showGroundGhost: false,
    camFormMode: "edit", // create | edit
    imgW: 1280,
    imgH: 720,
    spots: [],
    selectedSpotIdx: -1,
    hoveredTarget: null,
    magneticWeld: true,
    groundMagneticWeld: true,
    vehicleMagneticWeld: false,
    layerMode: "dual", // ground | vehicle | dual (defaults to dual so 3D spatial calibration is visible immediately)
    cropRoi: { enabled: false, x: 0, y: 0, w: 1, h: 1, raw_width: 0, raw_height: 0 },
    cropDraft: { x: 0.0, y: 0.25, w: 1.0, h: 0.50 },
    fullFrameImg: null,
    fullFrameDataUrl: null,
    fullFrameW: 0,
    fullFrameH: 0,
    calibSchedule: {
      enabled: false,
      mode: "nightly",
      night_time: "03:00",
      interval_hours: 12,
      auto_compensate_shift: true,
    },
    calibHealth: { status: "unverified" },
    vehHeightScale: 0.32,
    vehSlantBias: 0.0,
    fillAlpha: 0.16,
    zoom: 1.0,
    spacePressed: false,
    panDrag: null,
    draftPoints: [],
    curvePhase: "front", // front | back
    curveFrontPts: [],
    curveBackPts: [],
    previewTrapezoids: [],
    draggingHandle: null,
    undoStack: [],
    redoStack: [],
    dirty: false,
  },
  lab: {
    userSelectedCam: false,
    calibUserSelectedCam: false,
    lastDataUrl: "",
    lastSourceLabel: "",
    busy: false,
  },
  workspaceId: (function () {
    const m = (window.location.pathname || "").match(/^\/w\/([a-zA-Z0-9_-]{1,40})(?:\/|$)/);
    if (m && m[1]) return m[1];
    try {
      const p = new URLSearchParams(window.location.search);
      const qw = (p.get("ws") || p.get("workspace") || "").trim();
      if (qw) return qw;
      return localStorage.getItem("parking_admin_ws_id") || "default";
    } catch (_) {
      return "default";
    }
  })(),
  workspace: null,
  workspacesList: [],
  scenePresets: {},
  broadcastTemplates: {},
  wsModalMode: "edit",
  adminVoiceUri: (function () {
    try {
      return localStorage.getItem("parking_admin_voice_uri") || "";
    } catch (_) {
      return "";
    }
  })(),
  vlmPresets: {},
};

function getSavedWsAdminToken(wsId) {
  try {
    return localStorage.getItem(`parking_ws_adm_tok_${wsId || "default"}`) || "";
  } catch (_) {
    return "";
  }
}

function saveWsAdminToken(wsId, tok) {
  try {
    localStorage.setItem(`parking_ws_adm_tok_${wsId || "default"}`, tok || "");
  } catch (_) {}
}

async function api(path, options = {}) {
  const { headers: optHeaders, _retriedAdminAuth, ...restOpts } = options;
  const targetWs = state.workspaceId || "default";
  const admTok = getSavedWsAdminToken(targetWs);
  const res = await fetch(path, {
    cache: "no-store",
    ...restOpts,
    headers: {
      "Content-Type": "application/json",
      "X-Parking-Client": "20260420e",
      "X-Parking-Workspace": targetWs,
      ...(admTok ? { "X-Workspace-Admin-Token": admTok } : {}),
      ...(optHeaders || {}),
    },
  });
  const raw = await res.text();
  let data = {};
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch (_) {
      data = { error: raw.slice(0, 180) };
    }
  }
  if (res.status === 401 && data && data.admin_auth_required && !_retriedAdminAuth) {
    const entered = window.prompt(
      `请输入工作空间「${data.workspace_name || targetWs}」的物业管理口令：`
    );
    if (entered != null && entered.trim()) {
      saveWsAdminToken(targetWs, entered.trim());
      return api(path, { ...options, _retriedAdminAuth: true });
    }
  }
  if (!res.ok) {
    const msg = data.error || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.payload = data;
    throw err;
  }
  return data;
}

/* ==================== HELPERS ==================== */
function getEffectiveSpotStatusClient(sp) {
  if (!sp) return "UNKNOWN";
  if (sp.effective_status) return sp.effective_status;
  const raw = sp.status || "UNKNOWN";
  const pend = sp.pending_status || null;
  if (raw === "FREE" && (pend === "OCCUPIED" || pend === "BLOCKED")) {
    return pend;
  }
  return raw;
}
window.getEffectiveSpotStatusClient = getEffectiveSpotStatusClient;

/* ==================== BOOTSTRAP ==================== */
document.addEventListener("DOMContentLoaded", async () => {
  bindTabs();
  bindModelModal();
  bindWorkspaceModal();
  bindAdminVoiceSelector();
  bindDriverActions();
  bindSpatialGovernanceAndModals();
  bindSiteMapControls();
  bindSpotTagPopoverAndFilters();
  bindCalibrationConsole();
  bindTestLab();
  bindCameraManagementHub();
  syncMapWorkspaceLayout();
  drawSyntheticDemoLotOnCalibCanvas();
  try {
    await refreshWorkspacesCatalog();
    await refreshOverview();
  } catch (err) {
    console.error("Initial overview refresh failed:", err);
  }
  const firstCamId =
    state.calib.cameraId ||
    (state.overview?.cameras && state.overview.cameras[0]?.camera_id) ||
    "";
  if (firstCamId) {
    await loadCameraSpotsForCalib(firstCamId);
  }
});

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchMainTab(btn.dataset.tab);
    });
  });
}

function switchMainTab(tabName) {
  const resolved = tabName === "overview" ? "driver" : tabName;
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === resolved);
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.toggle("active", p.id === `tab-${resolved}`);
  });
  syncMapWorkspaceLayout();
  if (resolved === "cameras") {
    ensureCameraPresetsLoaded().then(() => renderCameraManagementHub(state.overview));
  }
  if (resolved === "calibrate") renderCalibCanvas();
  if (resolved === "lab" && !state.lab.busy) refreshLabCameraHint();
}

function showAppleToast(message, tone = "success", durationMs = 3800) {
  if (!message) return;
  let host = document.getElementById("appleToastHost");
  if (!host) {
    host = document.createElement("div");
    host.id = "appleToastHost";
    host.className = "apple-toast-host";
    document.body.appendChild(host);
  }
  const el = document.createElement("div");
  el.className = `apple-toast${tone === "warn" ? " is-warn" : tone === "error" ? " is-error" : ""}`;
  el.textContent = message;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("is-visible"));
  setTimeout(() => {
    el.classList.remove("is-visible");
    setTimeout(() => el.remove(), 240);
  }, durationMs);
}

async function refreshOverview() {
  const vtype = document.getElementById("vehicleTypeSelect")?.value || "sedan";
  const uid = document.getElementById("unitSelect")?.value || "";
  const data = await api(
    `/api/overview?vehicle_type=${encodeURIComponent(vtype)}&unit_id=${encodeURIComponent(uid)}`
  );
  const pageVer = document
    .querySelector('meta[name="parking-frontend-version"]')
    ?.getAttribute("content");
  if (
    pageVer &&
    data.frontend_version &&
    pageVer !== data.frontend_version &&
    !state.siteMap?.editMode &&
    !state.calib?.dirty &&
    !state.lab?.busy
  ) {
    try {
      if (sessionStorage.getItem("parking_reloaded_ver") !== data.frontend_version) {
        sessionStorage.setItem("parking_reloaded_ver", data.frontend_version);
        window.location.reload();
        return;
      }
    } catch (_) {}
  }
  state.overview = data;
  if (data.workspace) {
    state.workspace = data.workspace;
  }
  state.vlmPresets = data.config?.vlm_presets || {};

  document.getElementById("communityTitle").textContent = data.community_name;
  document.getElementById("statDisk").textContent = data.privacy_stats.raw_images_written_to_disk;
  document.getElementById("statRam").textContent = data.privacy_stats.frames_processed_in_ram;
  const ps = data.privacy_stats || {};
  const usedKb = Number(ps.cache_used_kb ?? ps.signature_cache_kb ?? 0);
  const budgetMb = Number(ps.cache_budget_mb || 64);
  const cacheTextEl = document.getElementById("statCacheText");
  if (cacheTextEl) {
    const humanUsed =
      usedKb >= 1024 ? `${(usedKb / 1024).toFixed(2)} MB` : `${usedKb.toFixed(1)} KB`;
    const hits = Number(ps.vlm_cache_hits || 0) + Number(ps.template_cache_hits || 0);
    cacheTextEl.textContent =
      hits > 0 ? `${humanUsed}/${budgetMb}MB (命中${hits})` : `${humanUsed}/${budgetMb}MB`;
  }
  const capsuleEl = document.getElementById("telemetryCapsule");
  if (capsuleEl) {
    capsuleEl.title = `多级热快取上限 ${budgetMb} MB：模板与ORB特征 ${
      ps.template_cache_entries || 0
    } 组（命中 ${ps.template_cache_hits || 0} 次）· VLM视觉结果快取 ${
      ps.vlm_cache_entries || 0
    } 项（命中 ${ps.vlm_cache_hits || 0} 次）· 帧差签名 ${
      ps.signature_cache_entries || 0
    } 项`;
  }
  document.getElementById("statTotalFree").textContent = data.total_effective_free;
  const totalCapEl = document.getElementById("statTotalCap");
  if (totalCapEl) {
    totalCapEl.textContent = data.total_capacity;
  }
  const railBadgeEl = document.getElementById("railFreeBadge");
  if (railBadgeEl) {
    railBadgeEl.textContent = `净余 ${data.total_effective_free}/${data.total_capacity} 位`;
  }
  document.getElementById("ftpInfoHost").textContent = `${location.hostname || "127.0.0.1"}:${data.config.ftp_port || 2121}`;

  await syncSiteMapBackdropIfNeeded(data.site_map);
  updateSiteMapHeaderUI(data.site_map);
  syncSpotTagFilterCounts(data);
  populateSelectors(data);
  renderUnboundCamerasBanner(data);
  renderSoftLocks(data.active_locks);
  try {
    renderAbstractSvgMap(data);
  } catch (err) {
    console.error("renderAbstractSvgMap error:", err);
  }
  try {
    renderBuildingClusters(data);
  } catch (err) {
    console.error("renderBuildingClusters error:", err);
  }
  try {
    renderConnectedCameras(data);
  } catch (err) {
    console.error("renderConnectedCameras error:", err);
  }
  try {
    renderCameraManagementHub(data);
  } catch (err) {
    console.error("renderCameraManagementHub error:", err);
  }
  try {
    renderZoneCards(data.zones);
  } catch (err) {
    console.error("renderZoneCards error:", err);
  }
  updateGovernanceSummaryAndNav(data);
  syncGovernanceViewVisibility();
  populateConfigUI(data.config);
}

function populateSelectors(data) {
  const unitSel = document.getElementById("unitSelect");
  const curUnit = unitSel.value || "3-1";
  const unitOptionsHtml = (data.units || [])
    .map(
      (u) =>
        `<option value="${escapeHtml(u.unit_id)}">${escapeHtml(u.name)} (${escapeHtml(
          u.unit_id
        )})</option>`
    )
    .join("");
  unitSel.innerHTML = unitOptionsHtml;
  if ((data.units || []).some((u) => u.unit_id === curUnit)) unitSel.value = curUnit;
  updateSiriHint();

  const nearUnitOptionsHtml =
    `<option value="">🏢 自动按最近单元门归属</option>` +
    (data.units || [])
      .map(
        (u) =>
          `<option value="${escapeHtml(u.unit_id)}">🚪 ${escapeHtml(u.name)} (${escapeHtml(
            u.unit_id
          )})</option>`
      )
      .join("");
  ["mapSpotNearUnitSelect", "grpModalNearUnitSelect"].forEach((selId) => {
    const el = document.getElementById(selId);
    if (el) {
      const prev = el.value || "";
      el.innerHTML = nearUnitOptionsHtml;
      if ([...el.options].some((o) => o.value === prev)) el.value = prev;
    }
  });

  const gatesList = data.site_map?.topology?.gates?.length
    ? data.site_map.topology.gates
    : [
        { gate_id: "south", name: "南门入口" },
        { gate_id: "east", name: "东门入口" },
      ];
  const entryGateSel = document.getElementById("entryGateSelect");
  if (entryGateSel) {
    const prevEntryGate = entryGateSel.value || "auto";
    entryGateSel.innerHTML =
      `<option value="auto">✨ 自动匹配顺路车行大门</option>` +
      gatesList
        .map(
          (g) =>
            `<option value="${escapeHtml(g.gate_id)}">🚗 从${escapeHtml(
              String(g.name || g.gate_id).replace(/入口$/, "")
            )}驶入</option>`
        )
        .join("");
    entryGateSel.value = [...entryGateSel.options].some((o) => o.value === prevEntryGate)
      ? prevEntryGate
      : "auto";
  }
  ["newUnitGateSelect", "newZoneGateSelect"].forEach((gId) => {
    const gateSel = document.getElementById(gId);
    if (gateSel) {
      const isZoneMulti = gId === "newZoneGateSelect";
      const prevG = gateSel.value || "auto";
      const allGateIds = gatesList.map((g) => g.gate_id).filter(Boolean);
      const allGateNames = gatesList
        .map((g) => String(g.name || g.gate_id).replace(/入口$/, ""))
        .join(" / ");
      const multiOpt =
        isZoneMulti && allGateIds.length >= 2
          ? `<option value="${escapeHtml(allGateIds.join(","))}">${escapeHtml(
              allGateNames
            )}均可</option>`
          : "";
      gateSel.innerHTML =
        `<option value="auto">✨ 自动匹配车行门（推荐）</option>` +
        multiOpt +
        gatesList
          .map(
            (g) =>
              `<option value="${escapeHtml(g.gate_id)}">${escapeHtml(
                g.name || g.gate_id
              )}${isZoneMulti ? "顺路" : ""}</option>`
          )
          .join("");
      gateSel.value = [...gateSel.options].some((o) => o.value === prevG) ? prevG : "auto";
    }
  });

  const camOptions = data.cameras
    .map((c) => {
      const codeTag = c.group_code ? `[${c.group_code}] ` : "";
      const spotCount = Number(c.spot_count || 0);
      const calibCount = Number(c.calibrated_spot_count || 0);
      const spotTag =
        spotCount > 0
          ? calibCount > 0
            ? ` · ${spotCount}车位`
            : ` · 已绑${spotCount}位(待实景对框)`
          : " · 0车位(未绑)";
      const tplTag =
        c.has_template && c.has_vehicle_template
          ? " · 地面+车辆双模板"
          : c.has_template
          ? " · 已设地面模板"
          : c.has_vehicle_template
          ? " · 已设有车模板"
          : "";
      return `<option value="${escapeHtml(c.camera_id)}">${
        c.has_template || c.has_vehicle_template ? "★ " : ""
      }${escapeHtml(codeTag + c.name)} (${escapeHtml(c.camera_id)})${spotTag}${tplTag}</option>`;
    })
    .join("");
  const calibCamSel = document.getElementById("calibCameraSelect");
  const labCamSel = document.getElementById("labCameraSelect");
  const preferredCam =
    data.cameras.find((c) => Number(c.calibrated_spot_count || 0) > 0) ||
    data.cameras.find((c) => Number(c.spot_count || 0) > 0) ||
    data.cameras[0];
  const prevCalibCam = state.lab.calibUserSelectedCam
    ? calibCamSel.value || state.calib.cameraId
    : preferredCam?.camera_id || calibCamSel.value || state.calib.cameraId;
  const prevLabCam = state.lab.userSelectedCam
    ? labCamSel.value
    : preferredCam?.camera_id || labCamSel.value || data.cameras?.[0]?.camera_id || "CAM_3F_WIN";
  calibCamSel.innerHTML = camOptions;
  labCamSel.innerHTML = camOptions;
  if (data.cameras.some((c) => c.camera_id === prevCalibCam)) {
    calibCamSel.value = prevCalibCam;
    state.calib.cameraId = prevCalibCam;
  } else if (preferredCam) {
    calibCamSel.value = preferredCam.camera_id;
    state.calib.cameraId = preferredCam.camera_id;
  }
  if (data.cameras.some((c) => c.camera_id === prevLabCam)) {
    labCamSel.value = prevLabCam;
  } else if (preferredCam) {
    labCamSel.value = preferredCam.camera_id;
  }
  if (!state.lab.busy && typeof refreshLabCameraHint === "function") {
    refreshLabCameraHint(true);
  }

  // Also populate floor-plan camera binding selects (including UNASSIGNED deferred option)
  const bindOptions =
    `<option value="UNASSIGNED">⏳ 待接入机位 (先摆图后接相机)</option>` +
    data.cameras
      .map(
        (c) =>
          `<option value="${escapeHtml(c.camera_id)}">📷 ${escapeHtml(
            c.group_code ? `[${c.group_code}] ${c.name}` : c.name
          )}</option>`
      )
      .join("");
  const mapCamBind = document.getElementById("mapSpotCameraBindSelect");
  const routeCamSel = document.getElementById("routeGenCamSelect");
  if (mapCamBind) {
    const prev = mapCamBind.value || "UNASSIGNED";
    mapCamBind.innerHTML = bindOptions;
    if ([...mapCamBind.options].some((o) => o.value === prev)) mapCamBind.value = prev;
  }
  if (routeCamSel) {
    const prevR = routeCamSel.value || "UNASSIGNED";
    routeCamSel.innerHTML = bindOptions;
    if ([...routeCamSel.options].some((o) => o.value === prevR)) routeCamSel.value = prevR;
  }
  const grpModalCamSel = document.getElementById("grpModalCameraSelect");
  if (grpModalCamSel) {
    const prevM = grpModalCamSel.value || "UNASSIGNED";
    grpModalCamSel.innerHTML = bindOptions;
    if ([...grpModalCamSel.options].some((o) => o.value === prevM)) grpModalCamSel.value = prevM;
  }

  const zoneOptionsHtml = (data.zones || [])
    .map(
      (z) =>
        `<option value="${escapeHtml(z.zone_id)}">${escapeHtml(z.name)} (${escapeHtml(
          z.zone_id
        )})</option>`
    )
    .join("");
  const zoneSel = document.getElementById("calibZoneSelect");
  if (zoneSel) {
    const prevZone = zoneSel.value || state.calib.zoneId;
    zoneSel.innerHTML = zoneOptionsHtml;
    if ((data.zones || []).some((z) => z.zone_id === prevZone)) zoneSel.value = prevZone;
  }
  const grpZoneSel = document.getElementById("grpModalZoneSelect");
  if (grpZoneSel) {
    const prevGZ = grpZoneSel.value || "";
    grpZoneSel.innerHTML = `<option value="">保持当前所属片区</option>` + zoneOptionsHtml;
    if ([...grpZoneSel.options].some((o) => o.value === prevGZ)) grpZoneSel.value = prevGZ;
  }

  renderCameraMetaSummaryCard();
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
    const firstFrameEl = document.getElementById("cfgFirstFrameVlmAnchor");
    const dynRoiEl = document.getElementById("cfgDynamicRoiEnabled");
    const hybridPctEl = document.getElementById("cfgHybridConfPct");
    const rawPct = Number(hybridPctEl?.value || 85);
    const clampedThr = Math.max(0.5, Math.min(0.99, (Number.isFinite(rawPct) ? rawPct : 85) / 100));
    const payload = {
      debounce_seconds: Number(document.getElementById("cfgDebounceSec").value || 3),
      soft_lock_ttl_seconds: Number(document.getElementById("cfgSoftLockSec").value || 40),
      hybrid_vlm_confidence_threshold: Number(clampedThr.toFixed(2)),
      first_frame_vlm_anchor: firstFrameEl ? Boolean(firstFrameEl.checked) : false,
      dynamic_roi_enabled: dynRoiEl ? Boolean(dynRoiEl.checked) : true,
      vlm: collectVlmFormValues(),
      voice_gateway: collectVoiceGatewayFormValues(),
    };
    const updated = await api("/api/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    document.getElementById("vlmApiKey").value = "";
    const vgTokEl = document.getElementById("vgCfgAccessToken");
    if (vgTokEl) vgTokEl.value = "";
    const vgClrEl = document.getElementById("vgCfgClearAccessToken");
    if (vgClrEl) vgClrEl.checked = false;
    populateConfigUI(updated);
    document.getElementById("cfgSaveMsg").textContent = "设置已保存并立即生效";
    setTimeout(() => {
      closeModelModal();
    }, 650);
  });
}

function collectVoiceGatewayFormValues() {
  return {
    access_token: document.getElementById("vgCfgAccessToken")?.value?.trim() || "",
    clear_access_token: Boolean(document.getElementById("vgCfgClearAccessToken")?.checked),
    default_unit_id: document.getElementById("vgCfgDefaultUnit")?.value?.trim() || "8-1",
    daily_llm_limit_per_user: Number(document.getElementById("vgCfgDailyLimit")?.value || 60),
    public_mobile_url: document.getElementById("vgCfgPublicMobileUrl")?.value?.trim() || "",
    public_voice_url: document.getElementById("vgCfgPublicVoiceUrl")?.value?.trim() || "",
    arrival_place: document.getElementById("vgCfgArrivalPlace")?.value?.trim() || "",
    icloud_shortcut_url: document.getElementById("vgCfgIcloudShortcutUrl")?.value?.trim() || "",
    llm_qa_enabled: Boolean(document.getElementById("vgCfgLlmQaEnabled")?.checked),
    weixin_enabled: false,
  };
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
  document.getElementById("cfgDebounceSec").value = cfg.debounce_seconds ?? 3;
  document.getElementById("cfgSoftLockSec").value = cfg.soft_lock_ttl_seconds ?? 40;
  const hybridPctEl = document.getElementById("cfgHybridConfPct");
  const hybridPct = Math.round(Number(cfg.hybrid_vlm_confidence_threshold ?? 0.85) * 100);
  if (hybridPctEl) hybridPctEl.value = hybridPct;
  const firstFrameEl = document.getElementById("cfgFirstFrameVlmAnchor");
  if (firstFrameEl) firstFrameEl.checked = Boolean(cfg.first_frame_vlm_anchor);
  const dynRoiEl = document.getElementById("cfgDynamicRoiEnabled");
  if (dynRoiEl) dynRoiEl.checked = cfg.dynamic_roi_enabled !== false;
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

  // Populate 18791 Voice Gateway config fields
  const vg = cfg.voice_gateway || {};
  const vgTokStatus = document.getElementById("vgTokenSavedPill");
  if (vgTokStatus) {
    vgTokStatus.textContent = vg.has_access_token
      ? `已设口令 (${vg.access_token_masked || "***"})`
      : "免口令(内网)";
  }
  const vgDefUnit = document.getElementById("vgCfgDefaultUnit");
  if (vgDefUnit) vgDefUnit.value = vg.default_unit_id || "8-1";
  const vgLim = document.getElementById("vgCfgDailyLimit");
  if (vgLim) vgLim.value = vg.daily_llm_limit_per_user ?? 60;
  const vgMobUrl = document.getElementById("vgCfgPublicMobileUrl");
  if (vgMobUrl) vgMobUrl.value = vg.public_mobile_url || "";
  const vgVoiceUrl = document.getElementById("vgCfgPublicVoiceUrl");
  if (vgVoiceUrl) vgVoiceUrl.value = vg.public_voice_url || "";
  const vgArrivalPlace = document.getElementById("vgCfgArrivalPlace");
  if (vgArrivalPlace) vgArrivalPlace.value = vg.arrival_place || "";
  const vgIcloudUrl = document.getElementById("vgCfgIcloudShortcutUrl");
  if (vgIcloudUrl) vgIcloudUrl.value = vg.icloud_shortcut_url || "";
  const vgLlmChk = document.getElementById("vgCfgLlmQaEnabled");
  if (vgLlmChk) vgLlmChk.checked = vg.llm_qa_enabled !== false;
  const vgOpenLink = document.getElementById("vgOpenPortalLink");
  if (vgOpenLink) {
    const voicePort = cfg.voice_http_port || 18791;
    vgOpenLink.href = vg.public_voice_url || `${location.protocol}//${location.hostname}:${voicePort}`;
  }

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
    const gateNote =
      v.mode === "hybrid"
        ? `（OpenCV ≥${hybridPct}% 秒级直出 · &lt;${hybridPct}%/夜间并行复核）`
        : "";
    document.getElementById("labModelSummaryText").innerHTML =
      `已接入模型 <strong>${escapeHtml(v.model || "")}</strong>（策略：${
        modeLabels[v.mode]
      }${gateNote}），慢路调用累计 <strong>${
        state.overview?.privacy_stats?.slow_lane_vlm_calls || 0
      }</strong> 次。`;
  } else {
    dot.className = "status-dot dot-idle";
    pillTxt.textContent = v.mode === "local_only" ? "本地视觉模式" : "配置 Azure 模型";
    document.getElementById("labModelSummaryText").textContent =
      "当前运行本地 OpenCV 几何与色彩引擎。点击下方按钮填入 Azure AI Foundry 部署信息即可启用多模态识别。";
  }
}

/* ==================== TAB 1: DRIVER GUIDANCE & ARCHITECTURAL MAP ==================== */
function bindDriverActions() {
  document.getElementById("unitSelect").addEventListener("change", async () => {
    state.lastDoorWalkPath = null;
    updateSiriHint();
    await refreshOverview();
  });
  document.getElementById("vehicleTypeSelect").addEventListener("change", (e) => {
    const vt = e.target.value;
    const tagSel = document.getElementById("drvTagFilterSelect");
    if (vt === "ev" && tagSel && !tagSel.value) {
      tagSel.value = "ev_charging";
      state.siteMap.tagFilter = "ev_charging";
    } else if (vt !== "ev" && tagSel && tagSel.value === "ev_charging") {
      tagSel.value = "";
      state.siteMap.tagFilter = "all";
    }
    updateSiriHint();
    refreshOverview();
  });
  document.getElementById("drvTagFilterSelect")?.addEventListener("change", (e) => {
    const tv = e.target.value || "";
    state.siteMap.tagFilter = tv || "all";
    syncSpotTagFilterCounts(state.overview);
    updateSiriHint();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });
  document.getElementById("entryGateSelect")?.addEventListener("change", () => {
    updateSiriHint();
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
    state.lastRecommendedGroupCode = null;
    state.lastRecommendedRoute = null;
    state.lastDoorWalkPath = null;
    await refreshOverview();
  });
  document.getElementById("btnCopySiri").addEventListener("click", async () => {
    const eg = document.getElementById("entryGateSelect")?.value || "auto";
    const egParam = eg && eg !== "auto" ? `&entry_gate_id=${encodeURIComponent(eg)}` : "";
    const url = `${location.origin}/api/recommend/voice?unit_id=${
      document.getElementById("unitSelect").value
    }&vehicle_type=${document.getElementById("vehicleTypeSelect").value}${egParam}`;
    let ok = false;
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(url);
        ok = true;
      } catch (_) {}
    }
    if (!ok) {
      const ta = document.createElement("textarea");
      ta.value = url;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        ok = document.execCommand("copy");
      } catch (_) {}
      document.body.removeChild(ta);
    }
    if (ok) {
      document.getElementById("btnCopySiri").textContent = "已复制";
      setTimeout(() => (document.getElementById("btnCopySiri").textContent = "复制链接"), 1800);
    }
  });
}

function updateSiriHint() {
  const uid = document.getElementById("unitSelect").value || "3-1";
  const vt = document.getElementById("vehicleTypeSelect").value || "sedan";
  const eg = document.getElementById("entryGateSelect")?.value || "auto";
  const tf = document.getElementById("drvTagFilterSelect")?.value || "";
  const egParam = eg && eg !== "auto" ? `&entry_gate_id=${encodeURIComponent(eg)}` : "";
  const tfParam = tf ? `&tag=${encodeURIComponent(tf)}` : "";
  document.getElementById(
    "siriEndpointCode"
  ).textContent = `GET ${location.origin}/api/recommend/voice?unit_id=${uid}&vehicle_type=${vt}${egParam}${tfParam}`;
  const portalLink = document.getElementById("drvOpenVoicePortalLink");
  if (portalLink) {
    const wsSeg =
      state.workspaceId && state.workspaceId !== "default"
        ? `/w/${encodeURIComponent(state.workspaceId)}`
        : "";
    portalLink.href = `${location.protocol}//${location.hostname}:18791${wsSeg}/?unit=${encodeURIComponent(uid)}`;
  }
}

async function triggerRecommendation(driverTag) {
  const unit_id = document.getElementById("unitSelect").value;
  const vehicle_type = document.getElementById("vehicleTypeSelect").value;
  const entry_gate_id = document.getElementById("entryGateSelect")?.value || "auto";
  const tag_filter = document.getElementById("drvTagFilterSelect")?.value || "";
  const res = await api("/api/recommend", {
    method: "POST",
    body: JSON.stringify({
      unit_id,
      vehicle_type,
      entry_gate_id,
      tag_filter: tag_filter || undefined,
      driver_tag: driverTag,
      acquire_lock: tag_filter !== "private",
    }),
  });

  state.lastRecommendedZoneId = res.primary ? res.primary.zone_id : null;
  state.lastRecommendedGroupCode = res.primary ? res.primary.group_code || null : null;
  state.lastRecommendedRoute = res.recommended_route || null;
  state.lastDoorWalkPath = res.door_walk_path || null;
  const box = document.getElementById("voiceResultBox");
  box.classList.remove("hidden");
  document.getElementById("voiceText").textContent = res.voice_text;

  if (res.primary) {
    const lockTtl = state.overview?.config?.soft_lock_ttl_seconds || 40;
    const doorStepHint = res.door_walk_path?.walk_meters
      ? ` · 🚪距单元门 ${res.door_walk_path.walk_meters}m`
      : ` · 步行 ${res.primary.walk_meters}m`;
    const backupMeta = res.backup
      ? ` · 备选：${escapeHtml(res.backup.zone_name)}（余 ${res.backup.effective_free} 位 · ${res.backup.walk_meters}m）`
      : "";
    document.getElementById("recMeta").innerHTML =
      `首选：<strong>${escapeHtml(res.primary.zone_name)}</strong>（净余 ${
        res.primary.effective_free
      } 位 · ${doorStepHint.replace(/^ · /, "")} · 代价分 ${res.primary.score}）` +
      backupMeta +
      (res.lock_created
        ? ` · 已锁定 ${lockTtl} 秒在途名额 (#${res.lock_created.lock_id})`
        : "");
  } else {
    document.getElementById("recMeta").textContent = "全区满位或在途车辆已预占剩余孤位";
  }

  speakChinese(res.voice_text);
  await refreshOverview();
}

/* ==================== SPATIAL GOVERNANCE: UNITS CRUD, GROUP BINDING & BUILDING CLUSTERS ==================== */
function formatAutoUnitNameClient(rawId) {
  const s = String(rawId || "").trim();
  const m = s.match(/^([A-Za-z0-9]+)[-_#](\d+)$/);
  if (m) {
    const b = /^\d+$/.test(m[1]) ? `${m[1]}号楼` : `${m[1].toUpperCase()}栋`;
    return `${b}${m[2]}单元`;
  }
  if (/^\d+$/.test(s)) return `${s}号楼1单元`;
  return s ? `${s}单元` : "";
}

function formatCompactUnitBadgeLabel(u, allUnits = []) {
  const uid = String(u?.unit_id || "").trim();
  const m = uid.match(/^([A-Za-z0-9]+)[-_#](\d+)$/);
  if (!m) return `${uid}栋`;
  const bldg = m[1];
  const unitNo = Number(m[2]);
  const siblingCount = (allUnits || []).filter((x) => {
    const xm = String(x?.unit_id || "").match(/^([A-Za-z0-9]+)[-_#](\d+)$/);
    return xm && xm[1].toUpperCase() === bldg.toUpperCase();
  }).length;
  if (siblingCount >= 2 || unitNo >= 2) {
    return `${bldg}栋${unitNo}单元`;
  }
  return `${bldg}栋`;
}

function projectPointToSegmentsClient(x, y, roads) {
  let best = null;
  (roads || []).forEach((r) => {
    const pts = Array.isArray(r?.points) ? r.points : [];
    for (let i = 0; i + 1 < pts.length; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len2 = dx * dx + dy * dy;
      const t = len2 > 1e-6 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / len2)) : 0;
      const px = x1 + t * dx;
      const py = y1 + t * dy;
      const d = Math.hypot(x - px, y - py);
      if (!best || d < best.d) best = { px, py, d };
    }
  });
  if (!best || best.d < 0.8) {
    return [+x.toFixed(2), +Math.min(98, y + 2.4).toFixed(2)];
  }
  const vx = best.px - x;
  const vy = best.py - y;
  const len = Math.max(0.001, Math.hypot(vx, vy));
  const step = Math.min(3.4, Math.max(2.0, best.d * 0.55));
  return [
    +Math.min(98.5, Math.max(1.5, x + (vx / len) * step)).toFixed(2),
    +Math.min(98.5, Math.max(1.5, y + (vy / len) * step)).toFixed(2),
  ];
}

function setModalFeedback(elId, isOk, text) {
  const aliasMap = {
    unitsModalMsg: "unitCrudFeedback",
    grpModalMsg: "grpModalFeedback",
    zonesModalMsg: "zoneCrudFeedback",
  };
  const el = document.getElementById(elId) || document.getElementById(aliasMap[elId] || "");
  if (!el) return;
  if (!text) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.className = `modal-feedback ${isOk ? "is-ok" : "is-err"}`;
  el.textContent = text;
}

function findSpotGroupClient(groupCode, fallbackCameraId = "") {
  const groups = state.overview?.spot_groups || [];
  if (!groups.length) return null;
  const raw = String(groupCode || "").trim();
  const up = raw.toUpperCase();
  const stripped = up.replace(/^(CAM_)+/, "");
  return (
    groups.find((g) => g.group_code === raw) ||
    groups.find((g) => String(g.group_code || "").toUpperCase() === up) ||
    (stripped
      ? groups.find(
          (g) => String(g.group_code || "").toUpperCase().replace(/^(CAM_)+/, "") === stripped
        )
      : null) ||
    (fallbackCameraId ? groups.find((g) => g.camera_id === fallbackCameraId) : null) ||
    null
  );
}

function jumpToCalibrateCamera(cameraId, openEditForm = false, presetZoneId = "") {
  switchMainTab("calibrate");
  if (presetZoneId) {
    const zSel = document.getElementById("calibZoneSelect");
    if (zSel && [...zSel.options].some((o) => o.value === presetZoneId)) {
      zSel.value = presetZoneId;
      state.calib.zoneId = presetZoneId;
    }
  }
  if (!cameraId) {
    openCameraCrudForm("create");
    return;
  }
  state.lab.calibUserSelectedCam = true;
  state.calib.cameraId = cameraId;
  const camSel = document.getElementById("calibCameraSelect");
  if (camSel && [...camSel.options].some((o) => o.value === cameraId)) {
    camSel.value = cameraId;
  }
  renderCameraMetaSummaryCard();
  loadCameraSpotsForCalib(cameraId).then(() => {
    if (openEditForm) {
      openCameraCrudForm("edit");
    } else {
      document.getElementById("camCrudFormPanel")?.classList.add("hidden");
    }
  });
}

function jumpToLabCamera(cameraId) {
  switchMainTab("lab");
  if (!cameraId) return;
  const labSel = document.getElementById("labCameraSelect");
  if (labSel && [...labSel.options].some((o) => o.value === cameraId)) {
    labSel.value = cameraId;
    labSel.dispatchEvent(new Event("change"));
  }
}

function openUnitsModal() {
  setModalFeedback("unitCrudFeedback", true, "");
  renderUnitsManageTable();
  document.getElementById("unitsModal")?.classList.remove("hidden");
}

function closeUnitsModal() {
  document.getElementById("unitsModal")?.classList.add("hidden");
}

function renderUnitsManageTable() {
  const tbody = document.getElementById("unitsManageTbody");
  if (!tbody || !state.overview) return;
  const units = state.overview.units || [];
  const gates = state.overview.site_map?.topology?.gates?.length
    ? state.overview.site_map.topology.gates
    : [
        { gate_id: "south", name: "南门入口" },
        { gate_id: "east", name: "东门入口" },
      ];
  const validGateIds = new Set(gates.map((g) => g.gate_id).filter(Boolean));
  tbody.innerHTML = units
    .map((u) => {
      const dx = Number(u.door_x ?? u.map_x ?? 50).toFixed(1);
      const dy = Number(u.door_y ?? u.map_y ?? 50).toFixed(1);
      const doorTag = u.door_custom ? "🎯 已手调门位" : "🧭 自动贴路门位";
      const curUnitGate = validGateIds.has(u.default_gate) ? u.default_gate : "auto";
      const gateOpts = [
        `<option value="auto" ${curUnitGate === "auto" ? "selected" : ""}>✨ 自动顺路车门</option>`,
        ...gates.map(
          (g) =>
            `<option value="${escapeHtml(g.gate_id)}" ${
              g.gate_id === curUnitGate ? "selected" : ""
            }>🚗 ${escapeHtml(g.name || g.gate_id)}</option>`
        ),
      ].join("");
      return `
        <tr data-unit-row="${escapeHtml(u.unit_id)}">
          <td><code>${escapeHtml(u.unit_id)}</code></td>
          <td>
            <input type="text" class="unit-inline-input js-unit-name-edit"
                   data-unit-id="${escapeHtml(u.unit_id)}"
                   value="${escapeHtml(u.name || "")}" />
          </td>
          <td>
            <select class="unit-inline-select js-unit-gate-edit" data-unit-id="${escapeHtml(
              u.unit_id
            )}">${gateOpts}</select>
          </td>
          <td>
            <span class="subtle-text">🚪 (${dx}, ${dy}) · ${doorTag}</span>
          </td>
          <td>
            <div style="display:flex;gap:5px;flex-wrap:wrap">
              <button type="button" class="btn-apple btn-apple-secondary btn-apple-xs js-unit-save-row" data-unit-id="${escapeHtml(
                u.unit_id
              )}">保存</button>
              <button type="button" class="btn-apple btn-apple-ghost btn-apple-xs js-unit-locate-door" data-unit-id="${escapeHtml(
                u.unit_id
              )}" title="进入平面图微调并拖动该单元门位置">📍 拖门位</button>
              <button type="button" class="btn-apple btn-apple-secondary btn-apple-xs map-danger-btn js-unit-delete-row" data-unit-id="${escapeHtml(
                u.unit_id
              )}" ${units.length <= 1 ? "disabled" : ""}>删除</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function focusUnitDoorInStudio(unitId) {
  closeUnitsModal();
  switchMainTab("driver");
  if (!state.siteMap.editMode) {
    enterSiteMapEditMode();
  }
  setSiteMapSubTool("nodes");
  state.siteMap.selectedNodeKind = "unit";
  state.siteMap.selectedNodeId = unitId;
  const u = (state.siteMap.draftUnits || []).find((x) => x.unit_id === unitId);
  if (u && state.overview) {
    const metrics = getSiteMapMetrics(state.overview.site_map);
    state.siteMap.zoom = Math.max(state.siteMap.zoom || 1, 1.65);
    state.siteMap.camCenterX = metrics.px(u.door_x ?? u.map_x ?? 50);
    state.siteMap.camCenterY = metrics.py(u.door_y ?? u.map_y ?? 50);
  }
  syncSiteMapStudioControls();
  if (state.overview) renderAbstractSvgMap(state.overview);
}

/* ==================== ZONES CRUD SHEET (EDIT / SUPPLEMENT AI ZONES) ==================== */
function openZonesModal(focusZoneId = null) {
  setModalFeedback("zoneCrudFeedback", true, "");
  renderZonesManageTable(focusZoneId);
  document.getElementById("zonesModal")?.classList.remove("hidden");
  if (focusZoneId) {
    requestAnimationFrame(() => {
      const row = document.querySelector(`#zonesManageTbody tr[data-zone-row="${CSS.escape(focusZoneId)}"]`);
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "nearest" });
        const inp = row.querySelector(".js-zone-name-edit");
        inp?.focus();
        inp?.select();
      }
    });
  }
}

function closeZonesModal() {
  document.getElementById("zonesModal")?.classList.add("hidden");
}

function formatZoneGateLabelClient(gateAccess, gatesList) {
  const raw = String(gateAccess || "south,east")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const fallbackMap = { south: "南门", east: "东门", north: "北门", west: "西门" };
  const gateMap = Object.fromEntries(
    (gatesList || []).map((g) => [g.gate_id, String(g.name || g.gate_id).replace(/入口$/, "")])
  );
  if (!raw.length) return "南门 / 东门";
  return raw.map((gid) => gateMap[gid] || fallbackMap[gid] || gid).join(" / ");
}

function renderZonesManageTable(focusZoneId = null) {
  const tbody = document.getElementById("zonesManageTbody");
  if (!tbody || !state.overview) return;
  const zones = state.overview.zones || [];
  const cameras = state.overview.cameras || [];
  const gatesMeta = state.overview.site_map?.topology?.gates?.length
    ? state.overview.site_map.topology.gates
    : state.overview.gates?.length
    ? state.overview.gates
    : [
        { gate_id: "south", name: "南门" },
        { gate_id: "east", name: "东门" },
      ];
  const validZoneGateIds = new Set(gatesMeta.map((g) => g.gate_id).filter(Boolean));
  const allGateIdsStr = gatesMeta.map((g) => g.gate_id).filter(Boolean).join(",");
  const allGateNamesStr = gatesMeta
    .map((g) => String(g.name || g.gate_id).replace(/入口$/, ""))
    .join(" / ");
  const dynamicZoneGateOptions = [
    { value: "auto", label: "✨ 自动匹配车行门（推荐）" },
    ...(gatesMeta.length >= 2 && allGateIdsStr
      ? [{ value: allGateIdsStr, label: `🚗 ${allGateNamesStr}均可` }]
      : []),
    ...gatesMeta.map((g) => ({
      value: g.gate_id,
      label: `🚗 ${String(g.name || g.gate_id).replace(/入口$/, "")}顺路`,
    })),
  ];
  tbody.innerHTML = zones
    .map((z) => {
      const camCount =
        z.camera_count ?? cameras.filter((c) => c.zone_id === z.zone_id).length;
      const spotCount = z.total_capacity ?? z.spot_count ?? (z.spots || []).length;
      const isFocused = focusZoneId && z.zone_id === focusZoneId;
      const rawGate = String(z.gate_access || z.gate || "auto").trim();
      const rawTokens = rawGate
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const allTokensValid =
        rawGate === "auto" ||
        (rawTokens.length > 0 && rawTokens.every((tok) => validZoneGateIds.has(tok)));
      const curGate = allTokensValid ? rawGate : "auto";
      const isDeadEnd = Boolean(z.is_dead_end || Number(z.dead_end_penalty || 0) >= 15);
      const hasPreset = dynamicZoneGateOptions.some((o) => o.value === curGate);
      const customGateLabel = `🚗 ${formatZoneGateLabelClient(curGate, gatesMeta)}${
        curGate.includes(",") ? "均可" : "顺路"
      }`;
      const gateOpts = [
        ...(hasPreset ? [] : [{ value: curGate, label: customGateLabel }]),
        ...dynamicZoneGateOptions,
      ]
        .map(
          (g) =>
            `<option value="${escapeHtml(g.value)}" ${
              g.value === curGate ? "selected" : ""
            }>${escapeHtml(g.label)}</option>`
        )
        .join("");
      return `
        <tr data-zone-row="${escapeHtml(z.zone_id)}" style="${
        isFocused ? "background:rgba(0,113,227,0.06);" : ""
      }">
          <td>
            <input type="text" class="unit-inline-input js-zone-id-edit"
                   data-orig-zone-id="${escapeHtml(z.zone_id)}"
                   value="${escapeHtml(z.zone_id)}"
                   style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;" />
          </td>
          <td>
            <input type="text" class="unit-inline-input js-zone-name-edit"
                   data-zone-id="${escapeHtml(z.zone_id)}"
                   value="${escapeHtml(z.name || "")}"
                   placeholder="如 10号楼北侧地面区" />
          </td>
          <td>
            <select class="unit-inline-select js-zone-gate-edit" data-zone-id="${escapeHtml(
              z.zone_id
            )}">${gateOpts}</select>
          </td>
          <td>
            <select class="unit-inline-select js-zone-deadend-edit" data-zone-id="${escapeHtml(
              z.zone_id
            )}">
              <option value="0" ${!isDeadEnd ? "selected" : ""}>顺畅贯通道</option>
              <option value="25" ${isDeadEnd ? "selected" : ""}>尽头折返支路</option>
            </select>
          </td>
          <td>
            <input type="text" class="unit-inline-input js-zone-desc-edit"
                   data-zone-id="${escapeHtml(z.zone_id)}"
                   value="${escapeHtml(z.description || "")}"
                   placeholder="靠近楼栋或备注说明（选填）" />
          </td>
          <td>
            <span class="subtle-text" style="white-space:nowrap;">📹 ${camCount}路 · 🅿️ ${spotCount}位</span>
          </td>
          <td>
            <div style="display:flex;gap:5px;flex-wrap:nowrap;">
              <button type="button" class="apple-mini-pill-btn is-accent js-zone-save-row" data-zone-id="${escapeHtml(
                z.zone_id
              )}">保存</button>
              <button type="button" class="apple-mini-pill-btn is-danger js-zone-delete-row" data-zone-id="${escapeHtml(
                z.zone_id
              )}" ${
        zones.length <= 1
          ? 'disabled title="至少保留 1 个停车片区"'
          : 'title="删除该片区并将其下机位与车位自动归并至最近片区"'
      }>归并删除</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

/* ==================== CONNECTED CAMERA SAFE DELETE / UNBIND SHEET ==================== */
const pendingCameraDeleteCtx = {
  cameraId: "",
  groupCode: "",
};

function syncCameraDeleteChoiceCards() {
  document.querySelectorAll("#delModalChoiceStack .apple-choice-card").forEach((card) => {
    const radio = card.querySelector('input[name="camDeleteMode"]');
    card.classList.toggle("is-selected", Boolean(radio?.checked));
  });
}

function openCameraDeleteModal(cameraId, groupCode = "") {
  if (!state.overview || !cameraId) return;
  const cam = (state.overview.cameras || []).find((c) => c.camera_id === cameraId);
  const boundGroups = (state.overview.spot_groups || []).filter(
    (g) => g.camera_id === cameraId
  );
  const resolvedGroup = groupCode || boundGroups[0]?.group_code || "";
  pendingCameraDeleteCtx.cameraId = cameraId;
  pendingCameraDeleteCtx.groupCode = resolvedGroup;

  const camHidden = document.getElementById("delModalCameraId");
  const grpHidden = document.getElementById("delModalGroupCode");
  if (camHidden) camHidden.value = cameraId;
  if (grpHidden) grpHidden.value = resolvedGroup;

  setModalFeedback("delModalFeedback", true, "");
  const titleEl = document.getElementById("cameraDeleteModalTitle");
  const subEl = document.getElementById("delModalSummaryText");
  const unbindWrap = document.getElementById("delChoiceUnbindOnlyWrap");
  if (titleEl) {
    titleEl.textContent = cam
      ? `调整或移除机位「${cam.name}」`
      : `移除机位「${cameraId}」`;
  }
  if (subEl) {
    const spotCnt =
      cam?.spot_count ??
      boundGroups.reduce((acc, g) => acc + Number(g.total_capacity || g.spot_count || 0), 0);
    subEl.textContent = `机位代号 ${cameraId}${
      resolvedGroup ? ` · 当前绑定平面车位组 ${resolvedGroup}` : ""
    } · 关联 ${spotCnt} 个车位。请选择安全的处理方式：`;
  }
  if (unbindWrap) {
    unbindWrap.classList.toggle("hidden", !resolvedGroup && boundGroups.length === 0);
  }
  const detachRadio = document.querySelector(
    'input[name="camDeleteMode"][value="detach"]'
  );
  if (detachRadio) detachRadio.checked = true;
  syncCameraDeleteChoiceCards();
  document.getElementById("cameraDeleteModal")?.classList.remove("hidden");
}

function closeCameraDeleteModal() {
  document.getElementById("cameraDeleteModal")?.classList.add("hidden");
}

/* ==================== GROUP BINDING, INTERACTIVE CAMERA PLACEMENT & DRAG-TO-MAP ==================== */
const CREATE_NEW_MAP_GROUP_VALUE = "__CREATE_ON_MAP__";

function inferCameraPlacementDefaults(cameraId) {
  if (!state.overview || !cameraId) return null;
  const camObj = (state.overview.cameras || []).find((c) => c.camera_id === cameraId);
  const ucObj = (state.overview.unbound_cameras || []).find((u) => u.camera_id === cameraId);
  const allCamSpots = (state.overview.zones || [])
    .flatMap((z) => z.spots || [])
    .filter((s) => s.camera_id === cameraId);

  const count = Math.max(
    1,
    Number(
      ucObj?.unplaced_spot_count ||
        allCamSpots.length ||
        camObj?.spot_count ||
        camObj?.spots_count ||
        2
    )
  );
  const rawGc = String(camObj?.group_code || "").trim();
  const baseCode =
    (rawGc ? rawGc.split(/[,;/+\s]+/)[0].trim().toUpperCase() : "") ||
    cameraId.replace(/^CAM_/i, "").toUpperCase();

  const textBlob = `${baseCode} ${camObj?.name || ""} ${camObj?.group_name || ""} ${
    camObj?.mount_address || ""
  }`;
  let isHoriz = /H\d+$/i.test(baseCode) || /横/.test(textBlob);
  if (!/H\d+$/i.test(baseCode) && (/V\d+$/i.test(baseCode) || /竖|纵/.test(textBlob))) {
    isHoriz = false;
  }

  let nearUnitId = "";
  const bMatch = baseCode.match(/^(\d{1,3})/) || String(camObj?.name || "").match(/(\d{1,3})\s*[号栋#]/);
  if (bMatch) {
    const bNum = bMatch[1];
    const hitUnit = (state.overview.units || []).find(
      (u) =>
        String(u.building || "") === bNum ||
        String(u.unit_id || "") === `${bNum}-1` ||
        String(u.unit_id || "").startsWith(`${bNum}-`)
    );
    if (hitUnit) nearUnitId = hitUnit.unit_id;
  }

  const parsedToken = parseSingleGroupTokenClient(baseCode);
  const groupName =
    (camObj?.group_name && camObj.group_name !== baseCode ? camObj.group_name : "") ||
    camObj?.mount_address ||
    parsedToken?.full_cn ||
    camObj?.name ||
    baseCode;

  const spotStatuses = [];
  if (allCamSpots.length) {
    allCamSpots.forEach((s) => spotStatuses.push(getEffectiveSpotStatusClient(s)));
  } else if (ucObj) {
    const occ = Number(ucObj.occupied || 0);
    const blk = Number(ucObj.blocked || 0);
    for (let i = 0; i < count; i++) {
      if (i < occ) spotStatuses.push("OCCUPIED");
      else if (i < occ + blk) spotStatuses.push("BLOCKED");
      else spotStatuses.push("FREE");
    }
  }

  return {
    cameraId,
    cameraName: camObj?.name || ucObj?.camera_name || cameraId,
    baseCode,
    count,
    orientation: isHoriz ? "horizontal" : "vertical",
    angleOffset: 0,
    groupName,
    nearUnitId,
    zoneId: camObj?.zone_id || ucObj?.zone_id || "",
    spotStatuses,
  };
}

function populateGroupBindModalForNewPlacement(cameraId) {
  const spec = inferCameraPlacementDefaults(cameraId);
  if (!spec || !state.overview) return;
  document.getElementById("grpModalOrigCode").value = CREATE_NEW_MAP_GROUP_VALUE;
  document.getElementById("grpModalCodeInput").value = spec.baseCode || "";
  document.getElementById("grpModalNameInput").value = spec.groupName || "";

  const nearSel = document.getElementById("grpModalNearUnitSelect");
  if (nearSel) {
    nearSel.value = [...nearSel.options].some((o) => o.value === spec.nearUnitId)
      ? spec.nearUnitId
      : "";
  }
  const camSel = document.getElementById("grpModalCameraSelect");
  if (camSel) {
    camSel.value = [...camSel.options].some((o) => o.value === cameraId)
      ? cameraId
      : "UNASSIGNED";
  }
  const grpZoneSel = document.getElementById("grpModalZoneSelect");
  if (grpZoneSel) {
    grpZoneSel.value = [...grpZoneSel.options].some((o) => o.value === spec.zoneId)
      ? spec.zoneId
      : "";
  }

  const strip = document.getElementById("grpModalLifecycleStrip");
  const statusEl = document.getElementById("grpModalBoundStatusText");
  const jumpBtn = document.getElementById("btnJumpCalibFromModal");
  const unbindBtn = document.getElementById("btnUnbindCamFromModal");
  const delCamBtn = document.getElementById("btnDeleteBoundCamFromModal");
  const delGrpBtn = document.getElementById("btnDeleteGroupFromModal");
  if (strip) {
    strip.classList.remove("hidden");
    if (statusEl) {
      statusEl.textContent = `✨ 将在平面图自动新建独立车位组（按「${spec.cameraName}」空间定标自动绘制 ${
        spec.orientation === "horizontal" ? "横向" : "纵向"
      } ${spec.count} 位，绝不覆盖任何已绑定机位）`;
    }
    jumpBtn?.classList.remove("hidden");
    unbindBtn?.classList.add("hidden");
    delCamBtn?.classList.remove("hidden");
    delGrpBtn?.classList.add("hidden");
  }
}

function populateGroupBindModalFields(grp, prefillCameraId = null) {
  if (!grp || !state.overview) return;
  document.getElementById("grpModalOrigCode").value = grp.group_code;
  document.getElementById("grpModalCodeInput").value = grp.group_code || "";
  document.getElementById("grpModalNameInput").value =
    grp.group_name && grp.group_name !== grp.group_code ? grp.group_name : "";
  const nearSel = document.getElementById("grpModalNearUnitSelect");
  if (nearSel) {
    nearSel.value = grp.near_unit_id || "";
  }
  const camSel = document.getElementById("grpModalCameraSelect");
  const targetCam = prefillCameraId || grp.camera_id || "UNASSIGNED";
  if (camSel) {
    camSel.value = [...camSel.options].some((o) => o.value === targetCam)
      ? targetCam
      : "UNASSIGNED";
  }
  const grpZoneSel = document.getElementById("grpModalZoneSelect");
  if (grpZoneSel) {
    const camObj = (state.overview.cameras || []).find((c) => c.camera_id === targetCam);
    const curZone = grp.zone_id || camObj?.zone_id || "";
    grpZoneSel.value = [...grpZoneSel.options].some((o) => o.value === curZone)
      ? curZone
      : "";
  }

  const strip = document.getElementById("grpModalLifecycleStrip");
  const statusEl = document.getElementById("grpModalBoundStatusText");
  const jumpBtn = document.getElementById("btnJumpCalibFromModal");
  const unbindBtn = document.getElementById("btnUnbindCamFromModal");
  const delCamBtn = document.getElementById("btnDeleteBoundCamFromModal");
  const delGrpBtn = document.getElementById("btnDeleteGroupFromModal");
  if (strip) {
    strip.classList.remove("hidden");
    const hasBoundCam = Boolean(grp.is_bound && grp.camera_id && grp.camera_id !== "UNASSIGNED");
    const isReplacingOtherCam =
      hasBoundCam && prefillCameraId && prefillCameraId !== grp.camera_id;
    if (statusEl) {
      statusEl.textContent = isReplacingOtherCam
        ? `⚠️ 注意：当前车位组「${grp.group_code}」已绑机位 ${
            grp.camera_name || grp.camera_id
          }！保存将换绑为 ${prefillCameraId}（如要新建独立车位组，请在上方下拉框选第一项「自动新建」）`
        : hasBoundCam
        ? `📹 当前车位组已绑机位：${grp.camera_name || grp.camera_id}（${
            grp.total_capacity || grp.spot_count
          } 位）`
        : `🅿️ 当前为平面待绑车位组（${grp.spot_count} 位，保存后将按实标车位数 1:1 绑定）`;
    }
    jumpBtn?.classList.toggle("hidden", !hasBoundCam);
    unbindBtn?.classList.toggle("hidden", !hasBoundCam);
    delCamBtn?.classList.toggle("hidden", !hasBoundCam);
    if (delGrpBtn) {
      delGrpBtn.classList.remove("hidden");
      delGrpBtn.dataset.groupCode = grp.group_code;
    }
  }
}

function openGroupBindModal(groupCode, prefillCameraId = null) {
  if (!state.overview) return;
  const allGroups = state.overview.spot_groups || [];
  let grp = groupCode ? findSpotGroupClient(groupCode) : null;

  // CRITICAL ISOLATION GUARD: When opening for an unbound camera (`prefillCameraId`),
  // NEVER default onto a group that is already bound to a DIFFERENT camera (e.g. 10BH2)!
  if (prefillCameraId && grp && grp.is_bound && grp.camera_id !== prefillCameraId) {
    grp = null;
  }
  if (!grp && prefillCameraId) {
    const camSpec = inferCameraPlacementDefaults(prefillCameraId);
    grp =
      allGroups.find(
        (g) =>
          (!g.is_bound || (g.camera_id === prefillCameraId && !g.is_calibrated)) &&
          camSpec &&
          String(g.group_code || "").toUpperCase() === camSpec.baseCode
      ) ||
      allGroups.find((g) => !g.is_bound || (g.camera_id === prefillCameraId && !g.is_calibrated)) ||
      null;
  }

  if (!grp && !prefillCameraId) {
    return;
  }

  const targetField = document.getElementById("grpModalTargetGroupField");
  const targetSel = document.getElementById("grpModalTargetGroupSelect");
  if (targetField && targetSel) {
    if (prefillCameraId) {
      const camSpec = inferCameraPlacementDefaults(prefillCameraId);
      const createOptLabel = `✨ 在平面图自动新建独立车位组（按实标绘制 ${
        camSpec?.orientation === "horizontal" ? "横向" : "纵向"
      } ${camSpec?.count || 2} 位 · 不覆盖已有绑定）`;
      const sortedGroups = [...allGroups].sort((a, b) => {
        const aFree = !a.is_bound || (a.camera_id === prefillCameraId && !a.is_calibrated) ? 0 : 1;
        const bFree = !b.is_bound || (b.camera_id === prefillCameraId && !b.is_calibrated) ? 0 : 1;
        if (aFree !== bFree) return aFree - bFree;
        return String(a.group_code).localeCompare(String(b.group_code), "zh-CN");
      });
      targetSel.dataset.prefillCameraId = prefillCameraId;
      targetSel.innerHTML = [
        `<option value="${CREATE_NEW_MAP_GROUP_VALUE}">${escapeHtml(createOptLabel)}</option>`,
        ...sortedGroups.map((g) => {
          const tag = !g.is_bound
            ? "⏳ 待绑空组"
            : g.camera_id === prefillCameraId
            ? "🎯 当前机位组"
            : `🔒 已绑 ${g.camera_id}`;
          const label = `${tag} · ${g.group_code}（${
            g.group_name || g.zone_name || "平面车位组"
          } · ${g.spot_count}位）`;
          return `<option value="${escapeHtml(g.group_code)}">${escapeHtml(label)}</option>`;
        }),
      ].join("");
      targetSel.value = grp ? grp.group_code : CREATE_NEW_MAP_GROUP_VALUE;
      targetField.classList.remove("hidden");
    } else {
      targetSel.dataset.prefillCameraId = "";
      targetField.classList.add("hidden");
    }
  }

  setModalFeedback("grpModalFeedback", true, "");
  if (grp) {
    populateGroupBindModalFields(grp, prefillCameraId);
  } else {
    populateGroupBindModalForNewPlacement(prefillCameraId);
  }
  document.getElementById("groupBindModal")?.classList.remove("hidden");
}

function getOrCreateGlobalDragGhost() {
  let el = document.getElementById("floatingVmodelDragGhost");
  if (!el) {
    el = document.createElement("div");
    el.id = "floatingVmodelDragGhost";
    el.className = "floating-vmodel-drag-ghost hidden";
    document.body.appendChild(el);
  }
  return el;
}

function clearCameraDropPreviewDom() {
  const g1 = document.getElementById("mainSpotPlacePreview");
  const g2 = document.getElementById("refSpotPlacePreview");
  if (g1) g1.innerHTML = "";
  if (g2) g2.innerHTML = "";
}

function renderCameraPlacementHud() {
  const hud = document.getElementById("camPlaceHudBar");
  const frame = document.getElementById("siteMapFrame");
  const spec = state.siteMap.pendingCameraPlacement;
  if (!hud) return;
  if (!spec) {
    hud.classList.add("hidden");
    hud.innerHTML = "";
    frame?.classList.remove("is-placing-camera");
    clearCameraDropPreviewDom();
    return;
  }
  frame?.classList.add("is-placing-camera");
  const orientLabel = spec.orientation === "horizontal" ? "横向顺停" : "纵向并排";
  hud.classList.remove("hidden");
  hud.innerHTML = `
    <span class="cam-place-hud-title">🎯 正在摆放「${escapeHtml(spec.baseCode)}」(${
    spec.count
  }位 · ${orientLabel})</span>
    <span>单击平面图空白处自动绘制并绑定（按住拖动可拉线定向），或单击已有车位组直接关联</span>
    <span class="cam-place-hud-actions">
      <button type="button" class="cam-place-hud-btn js-cam-hud-orient" title="切换横向/纵向排布 (快捷键 H)">↔ 横/竖切换</button>
      <button type="button" class="cam-place-hud-btn js-cam-hud-rot" title="旋转 90° (快捷键 R)">↻ 旋转90°</button>
      <button type="button" class="cam-place-hud-btn is-cancel js-cam-hud-cancel" title="取消摆位 (Esc)">✕ 取消</button>
    </span>
  `;
}

function cancelInteractiveCameraPlacement() {
  if (!state.siteMap.pendingCameraPlacement) return;
  state.siteMap.pendingCameraPlacement = null;
  renderCameraPlacementHud();
}

function startInteractiveCameraPlacement(cameraId) {
  const spec = inferCameraPlacementDefaults(cameraId);
  if (!spec) return;
  switchMainTab("driver");
  state.siteMap.pendingCameraPlacement = spec;
  renderCameraPlacementHud();
  const hintEl = document.getElementById("mapStudioHint");
  if (hintEl) {
    hintEl.textContent = `🎯 请在平面图上单击落位「${spec.cameraName}」（自动绘制 ${
      spec.orientation === "horizontal" ? "横向" : "纵向"
    } ${spec.count} 车位并立即绑定），或直接点击已有车位组关联`;
  }
  document.getElementById("siteMapCard")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function placeUnboundCameraOnFloorPlan(cameraId) {
  startInteractiveCameraPlacement(cameraId);
}

function findHoveredMapSpotGroup(worldPt, metrics) {
  if (!worldPt || !state.overview || !metrics) return null;
  const [wx, wy] = worldPt;
  const activeSpots = state.siteMap.editMode
    ? state.siteMap.draftSpots || []
    : (state.overview.zones || [])
        .flatMap((z) => z.spots || [])
        .filter((s) => s.map_placed !== false);

  let bestSpot = null;
  let bestDist = Infinity;
  activeSpots.forEach((sp) => {
    const sx = metrics.px(Number(sp.map_x || 50));
    const sy = metrics.py(Number(sp.map_y || 50));
    const hitRadius = Math.max(Number(sp.map_w || 2.2), Number(sp.map_h || 1.6)) * 0.85 + 0.9;
    const d = Math.hypot(wx - sx, wy - sy);
    if (d <= hitRadius && d < bestDist) {
      bestDist = d;
      bestSpot = sp;
    }
  });
  if (!bestSpot) return null;
  const gCode = String(bestSpot.group_code || "").trim();
  if (!gCode) return null;
  const groupSpots = activeSpots.filter((s) => String(s.group_code || "").trim() === gCode);
  const existingGrp = findSpotGroupClient(gCode);
  return {
    group_code: gCode,
    group_name: existingGrp?.group_name || bestSpot.group_name || gCode,
    camera_id: existingGrp?.camera_id || bestSpot.camera_id || "UNASSIGNED",
    is_bound: Boolean(
      existingGrp
        ? existingGrp.is_bound
        : bestSpot.camera_id && bestSpot.camera_id !== "UNASSIGNED"
    ),
    spot_count: groupSpots.length,
    spots: groupSpots,
  };
}

function computeCameraPreviewStallsWorld(spec, anchorWorld, endWorld, metrics) {
  if (!spec || !anchorWorld || !metrics) return [];
  const count = Math.max(1, Number(spec.count || 2));
  const isHoriz = spec.orientation === "horizontal";
  const w = isHoriz ? 2.74 : 1.2;
  const h = isHoriz ? 1.35 : 2.25;
  const defaultStep = isHoriz ? 3.12 : 1.52;

  let dirX = 1;
  let dirY = 0;
  let roadAngle = 0;
  let centers = [];

  if (
    endWorld &&
    Math.hypot(endWorld[0] - anchorWorld[0], endWorld[1] - anchorWorld[1]) >= 1.2
  ) {
    const dx = endWorld[0] - anchorWorld[0];
    const dy = endWorld[1] - anchorWorld[1];
    const span = Math.hypot(dx, dy);
    dirX = dx / span;
    dirY = dy / span;
    roadAngle = (Math.atan2(dirY, dirX) * 180) / Math.PI;
    const step = count > 1 ? Math.max(defaultStep * 0.72, span / (count - 1)) : 0;
    for (let i = 0; i < count; i++) {
      centers.push([anchorWorld[0] + dirX * step * i, anchorWorld[1] + dirY * step * i]);
    }
  } else {
    const road = findNearestRouteOrRoadTangent(anchorWorld[0], anchorWorld[1], metrics);
    if (road && road.dist <= 18.0 && road.segLen > 0.2) {
      let tx = road.vx / road.segLen;
      let ty = road.vy / road.segLen;
      if (tx < -0.25 || (Math.abs(tx) <= 0.25 && ty < 0)) {
        tx = -tx;
        ty = -ty;
      }
      dirX = tx;
      dirY = ty;
      roadAngle = (Math.atan2(dirY, dirX) * 180) / Math.PI;
    }
    const extraRot = Number(spec.angleOffset || 0);
    if (extraRot) {
      const rad = ((roadAngle + extraRot) * Math.PI) / 180;
      dirX = Math.cos(rad);
      dirY = Math.sin(rad);
      roadAngle = roadAngle + extraRot;
    }
    const startOffset = -((count - 1) * 0.5) * defaultStep;
    for (let i = 0; i < count; i++) {
      const d = startOffset + i * defaultStep;
      centers.push([anchorWorld[0] + dirX * d, anchorWorld[1] + dirY * d]);
    }
  }

  let stallAngle = ((roadAngle % 360) + 360) % 360;
  if (stallAngle > 180) stallAngle -= 360;
  if (stallAngle > 90) stallAngle -= 180;
  else if (stallAngle <= -90) stallAngle += 180;

  return centers.map(([cx, cy], idx) => ({
    wx: Math.min(metrics.worldW - 1.2, Math.max(1.2, cx)),
    wy: Math.min(metrics.worldH - 1.2, Math.max(1.2, cy)),
    w,
    h,
    angle: +stallAngle.toFixed(1),
    status: spec.spotStatuses?.[idx] || "FREE",
    label: `${spec.baseCode}-${String(idx + 1).padStart(2, "0")}`,
  }));
}

function renderCameraDropPreviewDom(spec, anchorWorld, endWorld = null, snapGroup = null) {
  const gMain = document.getElementById("mainSpotPlacePreview");
  const gRef = document.getElementById("refSpotPlacePreview");
  if (!spec || !anchorWorld || !state.overview) {
    if (gMain) gMain.innerHTML = "";
    if (gRef) gRef.innerHTML = "";
    return;
  }
  const metrics = getSiteMapMetrics(state.overview.site_map);
  let svgMarkup = "";

  if (snapGroup && snapGroup.spots?.length) {
    const xs = snapGroup.spots.map((s) => metrics.px(Number(s.map_x || 50)));
    const ys = snapGroup.spots.map((s) => metrics.py(Number(s.map_y || 50)));
    const pad = 2.1;
    const minX = Math.min(...xs) - pad;
    const maxX = Math.max(...xs) + pad;
    const minY = Math.min(...ys) - pad;
    const maxY = Math.max(...ys) + pad;
    const cx = (minX + maxX) * 0.5;
    const badgeColor =
      snapGroup.is_bound && snapGroup.camera_id !== spec.cameraId ? "#D97706" : "#0071E3";
    const actionTxt =
      snapGroup.is_bound && snapGroup.camera_id !== spec.cameraId
        ? `⇄ 换绑组 ${snapGroup.group_code} → ${spec.baseCode} (${spec.count}位)`
        : `🔗 关联组 ${snapGroup.group_code} → ${spec.baseCode} (按实标 ${spec.count} 位对齐)`;
    const bw = Math.max(24, actionTxt.length * 1.48);
    svgMarkup = `
      <rect x="${minX.toFixed(2)}" y="${minY.toFixed(2)}"
            width="${(maxX - minX).toFixed(2)}" height="${(maxY - minY).toFixed(2)}"
            rx="1.4" fill="rgba(0,113,227,0.12)" stroke="${badgeColor}"
            stroke-width="0.45" stroke-dasharray="1.1 0.7" />
      <g transform="translate(${cx.toFixed(2)}, ${(minY - 2.3).toFixed(2)})">
        <rect x="${(-bw / 2).toFixed(2)}" y="-1.7" width="${bw.toFixed(2)}" height="3.2"
              rx="1.6" fill="${badgeColor}" opacity="0.95" />
        <text x="0" y="0.4" text-anchor="middle" fill="#FFFFFF"
              font-size="1.55" font-weight="700">${escapeHtml(actionTxt)}</text>
      </g>
    `;
  } else {
    const stalls = computeCameraPreviewStallsWorld(spec, anchorWorld, endWorld, metrics);
    const carsMarkup = stalls
      .map((st, idx) => {
        const fill =
          st.status === "OCCUPIED"
            ? "rgba(255, 59, 48, 0.84)"
            : st.status === "BLOCKED"
            ? "rgba(255, 149, 0, 0.86)"
            : "rgba(52, 199, 89, 0.86)";
        return `
          <g transform="translate(${st.wx.toFixed(2)}, ${st.wy.toFixed(2)}) rotate(${st.angle})">
            <rect x="${(-st.w / 2).toFixed(2)}" y="${(-st.h / 2).toFixed(2)}"
                  width="${st.w.toFixed(2)}" height="${st.h.toFixed(2)}"
                  rx="0.42" fill="${fill}" stroke="#0071E3" stroke-width="0.32" />
            <text x="0" y="0.42" text-anchor="middle" fill="#FFFFFF"
                  font-size="0.92" font-weight="700">${String(idx + 1).padStart(2, "0")}</text>
          </g>
        `;
      })
      .join("");
    const badgeTxt = `✨ ${spec.baseCode} · 自动绘制${
      spec.orientation === "horizontal" ? "横向" : "纵向"
    } ${spec.count} 车位并绑定`;
    const bw = Math.max(24, badgeTxt.length * 1.48);
    svgMarkup = `
      ${
        endWorld
          ? `<line x1="${anchorWorld[0].toFixed(2)}" y1="${anchorWorld[1].toFixed(2)}"
                   x2="${endWorld[0].toFixed(2)}" y2="${endWorld[1].toFixed(2)}"
                   stroke="#0071E3" stroke-width="0.35" stroke-dasharray="0.9 0.6" />`
          : ""
      }
      ${carsMarkup}
      <g transform="translate(${anchorWorld[0].toFixed(2)}, ${(anchorWorld[1] - 3.3).toFixed(2)})">
        <rect x="${(-bw / 2).toFixed(2)}" y="-1.7" width="${bw.toFixed(2)}" height="3.2"
              rx="1.6" fill="#0071E3" opacity="0.95" />
        <text x="0" y="0.4" text-anchor="middle" fill="#FFFFFF"
              font-size="1.55" font-weight="700">${escapeHtml(badgeTxt)}</text>
      </g>
    `;
  }

  if (gMain) gMain.innerHTML = svgMarkup;
  if (gRef) gRef.innerHTML = svgMarkup;
}

async function persistSiteMapDraftInPlace({ selectGroupCode = "", selectCameraId = "" } = {}) {
  if (!state.siteMap.editMode) {
    await refreshOverview();
    return;
  }
  const spanM = Math.max(40, Math.min(800, Number(state.siteMap.draftSpanMeters || 185)));
  await api("/api/sitemap/layout", {
    method: "POST",
    body: JSON.stringify({
      spots: state.siteMap.draftSpots || [],
      units: state.siteMap.draftUnits || [],
      deleted_spot_ids: state.siteMap.deletedSpotIds || [],
      deleted_unit_ids: state.siteMap.deletedUnitIds || [],
      estimated_span_meters: spanM,
      topology: {
        roads: state.siteMap.draftRoads || [],
        routes: state.siteMap.draftRoutes || [],
        walkways: state.siteMap.draftWalkways || [],
        estimated_span_meters: spanM,
        gates: state.siteMap.draftGates || [],
        greens: state.siteMap.draftGreens || [],
      },
    }),
  });
  state.siteMap.deletedSpotIds = [];
  state.siteMap.deletedUnitIds = [];
  await refreshOverview();

  // Rehydrate draftSpots in-place so the workbench stays open with live reconciled spots!
  const flatSpots = [];
  (state.overview?.zones || []).forEach((z) => {
    (z.spots || []).forEach((sp) => {
      if (sp.map_placed === false) return;
      flatSpots.push(
        JSON.parse(
          JSON.stringify({
            ...sp,
            zone_id: sp.zone_id || z.zone_id,
            zone_name: sp.zone_name || z.name,
            group_name: sp.group_name || "",
            near_unit_id: sp.near_unit_id || "",
          })
        )
      );
    });
  });
  state.siteMap.draftSpots = flatSpots;

  const matched = flatSpots.filter(
    (sp) =>
      (selectGroupCode &&
        String(sp.group_code || "").toUpperCase() === String(selectGroupCode).toUpperCase()) ||
      (!selectGroupCode && selectCameraId && sp.camera_id === selectCameraId)
  );
  if (matched.length) {
    setSelectedSpotIds(
      matched.map((s) => s.spot_id),
      matched[0].spot_id
    );
    state.siteMap.selectedGroupCamId = getSpotGroupKey(matched[0]);
  } else {
    setSelectedSpotIds([]);
  }
  syncSiteMapStudioControls();
  renderAbstractSvgMap(state.overview);
}

async function commitCameraDropOrClickPlacement(spec, anchorWorld, endWorld = null, snapGroup = null) {
  if (!spec || !anchorWorld || !state.overview) return;
  const metrics = getSiteMapMetrics(state.overview.site_map);
  clearCameraDropPreviewDom();

  try {
    if (snapGroup && snapGroup.group_code) {
      if (snapGroup.is_bound && snapGroup.camera_id && snapGroup.camera_id !== spec.cameraId) {
        const ok = window.confirm(
          `平面车位组「${snapGroup.group_code}」当前已绑定机位「${snapGroup.camera_id}」。\n\n是否确认将其换绑为「${spec.cameraName} (${spec.cameraId})」？\n（提示：若想保留原车位组不变并为 ${spec.baseCode} 新建独立车位，请点“取消”并拖放到空白道路旁！）`
        );
        if (!ok) return;
      }
      if (state.siteMap.editMode) {
        await persistSiteMapDraftInPlace();
      }
      const targetCode =
        !snapGroup.is_bound &&
        (/^G\d+$/i.test(snapGroup.group_code) || /^GRP_/i.test(snapGroup.group_code))
          ? spec.baseCode
          : snapGroup.group_code;
      let bindTargetCode = snapGroup.group_code;
      if (targetCode !== snapGroup.group_code) {
        const ren = await api("/api/sitemap/rename-group", {
          method: "POST",
          body: JSON.stringify({
            group_code: snapGroup.group_code,
            new_group_code: targetCode,
            group_name: spec.groupName,
            near_unit_id: spec.nearUnitId,
            zone_id: spec.zoneId || undefined,
            for_camera_id: spec.cameraId,
          }),
        });
        bindTargetCode = ren.group_code || targetCode;
      }
      const res = await api("/api/sitemap/bind-group", {
        method: "POST",
        body: JSON.stringify({
          camera_id: spec.cameraId,
          group_code: bindTargetCode,
          group_name: spec.groupName || undefined,
          near_unit_id: spec.nearUnitId || undefined,
          zone_id: spec.zoneId || undefined,
        }),
      });
      cancelInteractiveCameraPlacement();
      if (state.siteMap.editMode) {
        await persistSiteMapDraftInPlace({
          selectGroupCode: res.group_code || bindTargetCode,
          selectCameraId: spec.cameraId,
        });
      } else {
        await refreshOverview();
      }
      showAppleToast(
        `✅ 已将「${spec.cameraName}」关联至平面组 ${
          res.group_code || bindTargetCode
        }（实标 ${res.spot_count || spec.count} 车位已同步）`
      );
      return;
    }

    // Empty space drop/click -> Auto-draw N calibrated horizontal/vertical stalls & persist immediately!
    if (state.siteMap.editMode) {
      await persistSiteMapDraftInPlace();
    }
    const payload = {
      camera_id: spec.cameraId,
      map_x: +metrics.nx(anchorWorld[0]).toFixed(2),
      map_y: +metrics.ny(anchorWorld[1]).toFixed(2),
      orientation: spec.orientation,
      group_code: spec.baseCode,
      group_name: spec.groupName,
      near_unit_id: spec.nearUnitId,
      zone_id: spec.zoneId || undefined,
    };
    if (
      endWorld &&
      Math.hypot(endWorld[0] - anchorWorld[0], endWorld[1] - anchorWorld[1]) >= 1.2
    ) {
      payload.end_map_x = +metrics.nx(endWorld[0]).toFixed(2);
      payload.end_map_y = +metrics.ny(endWorld[1]).toFixed(2);
    } else if (spec.angleOffset) {
      const road = findNearestRouteOrRoadTangent(anchorWorld[0], anchorWorld[1], metrics);
      const baseDeg = road && road.dist <= 18.0 ? road.angleDeg : 0;
      payload.map_angle = +(baseDeg + Number(spec.angleOffset || 0)).toFixed(1);
    }

    const res = await api("/api/sitemap/place-camera", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    cancelInteractiveCameraPlacement();
    if (state.siteMap.editMode) {
      await persistSiteMapDraftInPlace({
        selectGroupCode: res.group_code || spec.baseCode,
        selectCameraId: spec.cameraId,
      });
    } else {
      await refreshOverview();
    }
    showAppleToast(
      `✅ 已在平面图自动绘制并绑定「${res.group_code || spec.baseCode}」（${
        res.orientation === "horizontal" ? "横向" : "纵向"
      } ${res.spot_count || spec.count} 车位）`
    );
  } catch (err) {
    showAppleToast(`摆位绑定失败：${err.message}`, "error", 4800);
  }
}

function closeGroupBindModal() {
  document.getElementById("groupBindModal")?.classList.add("hidden");
}

function renderUnboundCamerasBanner(data) {
  const banner = document.getElementById("unboundCamerasBanner");
  if (!banner) return;
  const list = data?.unbound_cameras || [];
  if (!list.length) {
    banner.classList.add("hidden");
    banner.innerHTML = "";
    return;
  }
  banner.classList.remove("hidden");
  banner.innerHTML = `
    <div class="unbound-cam-head">
      <span>🔗 发现 ${list.length} 路已标定机位待摆放到平面图（可直接按住拖入下方平面图空白处自动绘车，或拖到已有车位组上关联）</span>
    </div>
    <div class="unbound-cam-list">
      ${list
        .map((uc) => {
          const spec = inferCameraPlacementDefaults(uc.camera_id);
          const orientTag = spec?.orientation === "horizontal" ? "横向" : "纵向";
          const sugCode = uc.suggested_group_code || uc.candidate_groups?.[0]?.group_code || "";
          const sugGrp = sugCode ? findSpotGroupClient(sugCode) : null;
          const validSugGrp = sugGrp && !sugGrp.is_bound ? sugGrp : null;
          const sugLabel = validSugGrp
            ? `${validSugGrp.group_code}（${
                validSugGrp.group_name || validSugGrp.building_name || "平面车位组"
              } · 平面${validSugGrp.spot_count}格 → 定标${uc.unplaced_spot_count}位）`
            : "";
          return `
            <div class="unbound-cam-item is-draggable-cam"
                 data-draggable-cam-id="${escapeHtml(uc.camera_id)}"
                 title="按住鼠标左键直接拖到下方平面图：落到空白处自动绘制${orientTag} ${
            uc.unplaced_spot_count
          } 车位并绑定；落到已有车位组上直接关联">
              <div class="unbound-cam-meta">
                <strong>📷 ${escapeHtml(uc.camera_name)}</strong>
                <code>${escapeHtml(uc.camera_id)}</code>
                <span class="unbound-drag-pill">✋ 按住拖入平面图（${orientTag} ${
            uc.unplaced_spot_count
          }位）</span>
                <span>实标 <strong>${uc.unplaced_spot_count}</strong> 车位（净余 ${
            uc.effective_free
          } · 占用 ${uc.occupied}${uc.blocked ? ` · 禁停/障碍 ${uc.blocked}` : ""}）</span>
              </div>
              <div class="unbound-cam-actions">
                ${
                  validSugGrp
                    ? `<button type="button" class="btn-apple btn-apple-primary btn-apple-xs js-quick-bind-cam"
                               data-cam-id="${escapeHtml(uc.camera_id)}"
                               data-group-code="${escapeHtml(validSugGrp.group_code)}">
                         🔗 一键关联 ${escapeHtml(sugLabel)}
                       </button>`
                    : ""
                }
                <button type="button" class="btn-apple ${
                  validSugGrp ? "btn-apple-secondary" : "btn-apple-primary"
                } btn-apple-xs js-place-cam-on-map"
                        data-cam-id="${escapeHtml(uc.camera_id)}">
                  🗺️ 去平面图摆位绑定（${orientTag}${uc.unplaced_spot_count}位）
                </button>
                <button type="button" class="btn-apple btn-apple-secondary btn-apple-xs js-open-bind-picker"
                        data-cam-id="${escapeHtml(uc.camera_id)}"
                        data-group-code="${escapeHtml(validSugGrp?.group_code || "")}">
                  选择车位组 / 自动新建...
                </button>
                <button type="button" class="btn-apple btn-apple-ghost btn-apple-xs js-unbound-edit-cam"
                        data-cam-id="${escapeHtml(uc.camera_id)}">✎ 编辑</button>
                <button type="button" class="btn-apple btn-apple-ghost btn-apple-xs danger-link js-unbound-del-cam"
                        data-cam-id="${escapeHtml(uc.camera_id)}">🗑 删除</button>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function updateGovernanceSummaryAndNav(data) {
  if (!data) return;
  const clusters = data.building_clusters || [];
  const units = data.units || [];
  const zones = data.zones || [];
  const cameras = data.cameras || [];
  const groups = data.spot_groups || [];
  const unboundCams = data.unbound_cameras || [];
  const boundGroups = groups.filter((g) => g.is_bound);
  const totFree = zones.reduce((acc, z) => acc + Number(z.effective_free || 0), 0);
  const totCap = zones.reduce((acc, z) => acc + Number(z.total_capacity || 0), 0);
  const deadEndCnt = zones.filter((z) => z.is_dead_end).length;

  const navBadge = document.getElementById("navGovCountBadge");
  if (navBadge) {
    if (unboundCams.length > 0) {
      navBadge.textContent = `${cameras.length}机位·${unboundCams.length}待绑`;
      navBadge.classList.add("is-alert");
    } else {
      navBadge.textContent = `${cameras.length}机位`;
      navBadge.classList.remove("is-alert");
    }
  }

  const dockText = document.getElementById("mapFooterSummaryText");
  if (dockText) {
    dockText.textContent = `已归档 ${clusters.length} 栋楼（${units.length} 个单元）· ${zones.length} 个小区片区 · 已接入 ${cameras.length} 路摄像头机位（当前净余 ${totFree} / ${totCap} 位）`;
  }

  const setTxt = (id, txt) => {
    const el = document.getElementById(id);
    if (el) el.textContent = txt;
  };
  setTxt("govKpiBuildings", `${clusters.length} 栋楼`);
  setTxt("govKpiUnitsSub", `${units.length} 个目的地单元 · 实测步行距`);
  setTxt("govKpiCameras", `${cameras.length} 路机位`);
  setTxt(
    "govKpiCamerasSub",
    unboundCams.length > 0
      ? `已绑 ${cameras.length - unboundCams.length} 路 · ${unboundCams.length} 路待绑平面组`
      : `全部已接入 · 支持编辑/换绑/片区改属/删除`
  );
  setTxt("govKpiZones", `${zones.length} 个片区`);
  setTxt(
    "govKpiZonesSub",
    deadEndCnt > 0
      ? `含 ${deadEndCnt} 个尽端窄路区 · 支持人工修正补充`
      : `支持人工改名、新增片区与归并`
  );
  setTxt("govKpiFreeRatio", `${totFree} / ${totCap}`);
  setTxt("govKpiGroupsSub", `共 ${groups.length} 组车位（已绑机位 ${boundGroups.length} 组）`);
}

function syncGovernanceViewVisibility() {
  const mode = ["building", "cameras", "zones"].includes(state.govViewMode)
    ? state.govViewMode
    : "building";
  state.govViewMode = mode;

  document.querySelectorAll(".gov-view-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.govView === mode);
  });
  document.querySelectorAll(".gov-kpi-tile[data-gov-kpi]").forEach((tile) => {
    tile.classList.toggle("is-active", tile.dataset.govKpi === mode);
  });
  document.getElementById("govPaneBuildings")?.classList.toggle("hidden", mode !== "building");
  document.getElementById("govPaneCameras")?.classList.toggle("hidden", mode !== "cameras");
  document.getElementById("govPaneZones")?.classList.toggle("hidden", mode !== "zones");
  document.getElementById("btnGovQuickCreateZone")?.classList.toggle("hidden", mode !== "zones");
}

function matchesGovSearch(...fields) {
  const q = String(state.govSearchQuery || "").trim().toLowerCase();
  if (!q) return true;
  return fields.some((f) => String(f || "").toLowerCase().includes(q));
}

function renderBuildingClusters(data) {
  const grid = document.getElementById("buildingClustersGrid");
  const subEl = document.getElementById("spatialGovSummaryText");
  if (!grid || !data) return;

  const clusters = data.building_clusters || [];
  const groups = data.spot_groups || [];
  const cameras = data.cameras || [];
  const zones = data.zones || [];
  const zoneNameById = Object.fromEntries(zones.map((z) => [z.zone_id, z.name]));
  const boundGroupCount = groups.filter((g) => g.is_bound).length;
  const selectedUnitId = document.getElementById("unitSelect")?.value || "";

  if (subEl) {
    subEl.textContent = `共 ${clusters.length} 栋楼 · ${(data.units || []).length} 个目的地单元 · ${
      zones.length
    } 个所属小区片区 · 已接入 ${cameras.length} 路机位（绑定 ${boundGroupCount}/${
      groups.length
    } 组平面车位）`;
  }

  if (!clusters.length) {
    grid.innerHTML = `<div class="bldg-empty-note">暂无楼栋与车位归类数据</div>`;
    return;
  }

  const filteredClusters = clusters
    .map((bc) => {
      if (!state.govSearchQuery.trim()) return bc;
      const bldgHit = matchesGovSearch(
        bc.building_name,
        bc.building_id,
        ...(bc.units || []).map((u) => `${u.unit_id} ${u.name}`)
      );
      if (bldgHit) return bc;
      const matchingGroups = (bc.groups || []).filter((grp) =>
        matchesGovSearch(
          grp.group_code,
          grp.group_name,
          grp.camera_id,
          grp.camera_name,
          zoneNameById[grp.zone_id] || grp.zone_id
        )
      );
      return matchingGroups.length ? { ...bc, groups: matchingGroups } : null;
    })
    .filter(Boolean);

  if (!filteredClusters.length) {
    grid.innerHTML = `<div class="bldg-empty-note">未找到匹配「${escapeHtml(
      state.govSearchQuery
    )}」的楼栋或车位组</div>`;
    return;
  }

  grid.innerHTML = filteredClusters
    .map((bc) => {
      const hasSelUnit = (bc.units || []).some((u) => u.unit_id === selectedUnitId);
      const freeColor =
        bc.effective_free >= 2 ? "#1D9E31" : bc.effective_free === 1 ? "#B26A00" : "#D70015";
      const capLabel =
        bc.bound_capacity > 0
          ? `净余 ${bc.effective_free} / 已接机位 ${bc.bound_capacity} 位${
              bc.unassigned_capacity > 0 ? ` (+待绑${bc.unassigned_capacity})` : ""
            }`
          : bc.total_capacity > 0
          ? `平面规划 ${bc.total_capacity} 位（待绑机位）`
          : "附近暂无规划车位";

      const unitChipsHtml = (bc.units || [])
        .map((u) => {
          const isAct = u.unit_id === selectedUnitId;
          return `<button type="button" class="bldg-door-chip ${
            isAct ? "is-active-unit" : ""
          } js-select-dest-unit" data-unit-id="${escapeHtml(
            u.unit_id
          )}" title="点击将「${escapeHtml(u.name)}」设为当前寻位目的地单元">
            🚪 ${escapeHtml(u.name)}${u.door_custom ? " · 已校门位" : ""}
          </button>`;
        })
        .join("");

      const groupsHtml = (bc.groups || []).length
        ? (bc.groups || [])
            .map((grp) => {
              const zoneLabel = zoneNameById[grp.zone_id] || grp.zone_id || "";
              const walkBadges = [
                zoneLabel
                  ? `<span class="js-open-zone-from-bldg" style="cursor:pointer;color:#0066CC" data-zone-id="${escapeHtml(
                      grp.zone_id || ""
                    )}" title="点击编辑或调整所属小区片区">📍 ${escapeHtml(zoneLabel)}</span>`
                  : "",
                ...(grp.unit_door_badges || []).map(
                  (wb) =>
                    `<span>🚶 距${escapeHtml(wb.label)} <strong>${wb.walk_meters}m</strong></span>`
                ),
              ]
                .filter(Boolean)
                .join("");

              const spotChips = (grp.spots || [])
                .map((sp, idx) => {
                  const effSt =
                    typeof getEffectiveSpotStatusClient === "function"
                      ? getEffectiveSpotStatusClient(sp)
                      : sp?.effective_status || sp?.status || "UNKNOWN";
                  const cls = !grp.is_bound
                    ? "unknown"
                    : effSt === "FREE"
                    ? "free"
                    : effSt === "OCCUPIED"
                    ? "occupied"
                    : effSt === "BLOCKED"
                    ? "blocked"
                    : "unknown";
                  const shortNo = shortenSpotLabel(sp.label, idx);
                  return `<span class="spot-chip ${cls}${
                    sp.pending_status ? " is-pending" : ""
                  }" title="${escapeHtml(sp.label)}: ${escapeHtml(
                    sp.last_reason || effSt
                  )}">${escapeHtml(shortNo)}</span>`;
                })
                .join("");

              const actionBtns = grp.is_bound
                ? `
                  <button type="button" class="apple-mini-pill-btn is-accent js-jump-cam-calib"
                          data-cam-id="${escapeHtml(grp.camera_id)}"
                          title="进入该已接入机位的双层空间标定台">🎯 标定</button>
                  <button type="button" class="apple-mini-pill-btn js-open-group-modal"
                          data-group-code="${escapeHtml(grp.group_code)}"
                          title="修改组名、调整归属楼栋/片区或更换绑定机位">✎ 调整</button>
                  <button type="button" class="apple-mini-pill-btn js-unbind-group-cam"
                          data-group-code="${escapeHtml(grp.group_code)}"
                          data-cam-id="${escapeHtml(grp.camera_id)}"
                          title="解除该车位组与摄像头的绑定，车位保留在平面图">⛓️‍💥 解绑</button>
                  <button type="button" class="apple-mini-pill-btn is-danger js-delete-bound-cam"
                          data-cam-id="${escapeHtml(grp.camera_id)}"
                          data-group-code="${escapeHtml(grp.group_code)}"
                          title="删除该已接入机位（可选保留平面图车位或一并删除）">🗑 删除</button>
                `
                : `
                  <button type="button" class="apple-mini-pill-btn is-accent js-open-group-modal"
                          data-group-code="${escapeHtml(grp.group_code)}">🔗 绑机位 / 调整</button>
                  <button type="button" class="apple-mini-pill-btn is-danger js-delete-spot-group"
                          data-group-code="${escapeHtml(grp.group_code)}"
                          title="从平面图移除该待绑车位组">🗑 删组</button>
                `;

              return `
                <div class="bldg-group-card ${grp.is_bound ? "" : "is-unassigned"}">
                  <div class="bldg-group-head">
                    <div class="bldg-group-title">
                      <span class="grp-code-pill">${escapeHtml(grp.group_code)}</span>
                      <span>${escapeHtml(grp.group_name || `${bc.building_name}附近车位`)}</span>
                      <span class="grp-cam-pill ${grp.is_bound ? "is-bound" : "is-unbound"}">
                        ${
                          grp.is_bound
                            ? `📷 ${escapeHtml(grp.camera_name || grp.camera_id)}（空${
                                grp.effective_free
                              }/${grp.total_capacity}）`
                            : `⏳ 待绑机位（${grp.spot_count}位）`
                        }
                      </span>
                    </div>
                    <div class="bldg-group-actions">${actionBtns}</div>
                  </div>
                  ${walkBadges ? `<div class="bldg-group-walks">${walkBadges}</div>` : ""}
                  <div class="spot-chip-row">${spotChips}</div>
                </div>
              `;
            })
            .join("")
        : `<div class="bldg-empty-note">暂未在此楼栋附近规划车位组（可在「寻位导览 → 平面图微调」拖放添加，或直接新增机位）</div>`;

      return `
        <div class="bldg-cluster-card ${hasSelUnit ? "is-selected-bldg" : ""}">
          <div class="bldg-cluster-head">
            <div class="bldg-cluster-title">
              <span>🏢 ${escapeHtml(bc.building_name)}</span>
              ${
                bc.unit_count >= 2
                  ? `<span class="badge-pill">${bc.unit_count}个单元</span>`
                  : ""
              }
            </div>
            <span class="bldg-cluster-stat" style="color:${freeColor}">${escapeHtml(
        capLabel
      )}</span>
          </div>
          <div class="bldg-unit-doors-row">
            ${unitChipsHtml}
            <button type="button" class="btn-apple btn-apple-ghost btn-apple-xs js-bldg-add-unit"
                    data-bldg-id="${escapeHtml(bc.building_id)}"
                    title="为该楼栋快速新增单元（如 9-2）">+ 加单元</button>
          </div>
          <div class="bldg-groups-stack">${groupsHtml}</div>
        </div>
      `;
    })
    .join("");
}

function renderConnectedCameras(data) {
  const wrap = document.getElementById("connectedCamerasGrid");
  if (!wrap || !data) return;
  const cameras = data.cameras || [];
  const zones = data.zones || [];
  const spotGroups = data.spot_groups || [];
  const unboundSet = new Set((data.unbound_cameras || []).map((u) => u.camera_id));

  if (!cameras.length) {
    wrap.innerHTML = `
      <div class="bldg-empty-note" style="grid-column:1/-1;padding:28px;text-align:center;">
        当前暂无已接入摄像头机位。点击右上角「＋ 新增摄像头机位」即可接入并自动生成空间标定。
      </div>`;
    return;
  }

  const filtered = cameras.filter((cam) => {
    const zName = zones.find((z) => z.zone_id === cam.zone_id)?.name || "";
    const boundCodes = spotGroups
      .filter((g) => g.camera_id === cam.camera_id)
      .map((g) => `${g.group_code} ${g.group_name}`)
      .join(" ");
    return matchesGovSearch(
      cam.camera_id,
      cam.name,
      cam.group_code,
      cam.mount_address,
      cam.zone_id,
      zName,
      boundCodes
    );
  });

  if (!filtered.length) {
    wrap.innerHTML = `<div class="bldg-empty-note" style="grid-column:1/-1;">未找到匹配「${escapeHtml(
      state.govSearchQuery
    )}」的摄像头机位</div>`;
    return;
  }

  wrap.innerHTML = filtered
    .map((cam) => {
      const boundGroups = spotGroups.filter((g) => g.camera_id === cam.camera_id);
      const isBound = boundGroups.length > 0 && !unboundSet.has(cam.camera_id);
      const zoneOpts = zones
        .map(
          (z) =>
            `<option value="${escapeHtml(z.zone_id)}" ${
              z.zone_id === cam.zone_id ? "selected" : ""
            }>${escapeHtml(z.name)} (${escapeHtml(z.zone_id)})</option>`
        )
        .join("");

      const allCamSpots = zones
        .flatMap((z) => z.spots || [])
        .filter((s) => s.camera_id === cam.camera_id);
      const totalSpots =
        allCamSpots.length ||
        boundGroups.reduce((a, g) => a + Number(g.total_capacity || g.spot_count || 0), 0) ||
        Number(cam.spot_count || 0);
      const freeSpots = allCamSpots.filter(
        (s) => (s.effective_status || s.status) === "FREE"
      ).length;

      const hasTpl = Boolean(
        cam.has_template || cam.has_vehicle_template || cam.has_reference_template
      );
      const hasCrop = Boolean(
        cam.crop_roi && cam.crop_roi.enabled !== false && Number(cam.crop_roi.w) > 0
      );
      const anchorList = Array.isArray(cam.anchor_points)
        ? cam.anchor_points
        : Array.isArray(cam.anchors)
        ? cam.anchors
        : [];
      const anchorCnt = anchorList.length;

      const boundSummary = boundGroups.length
        ? boundGroups
            .map(
              (g) =>
                `<span class="grp-code-pill">${escapeHtml(g.group_code)}</span> ${escapeHtml(
                  g.group_name || g.building_name || ""
                )}`
            )
            .join(" · ")
        : `<span style="color:#B25000">尚未绑定平面图车位组</span>`;
      const unboundCandidate = !boundGroups.length
        ? spotGroups.find((g) => !g.is_bound)
        : null;
      const primaryGroupCode = boundGroups[0]?.group_code || unboundCandidate?.group_code || "";
      const ingestCfg = cam.ingest_config || {};
      const vendorLabel = ingestCfg.vendor_label || ingestCfg.vendor || "通用摄像头";
      const protoLabel = ingestCfg.protocol_label || (ingestCfg.protocol || "ftp_push").toUpperCase();

      return `
        <article class="cam-roster-card ${isBound ? "" : "is-unbound"}" data-cam-card="${escapeHtml(
        cam.camera_id
      )}">
          <div class="cam-roster-head">
            <div>
              <div class="cam-roster-title">
                <span>📹 ${escapeHtml(cam.name || cam.camera_id)}</span>
              </div>
              <span class="cam-roster-id">${escapeHtml(cam.camera_id)}${
        cam.group_code ? ` · 编码 ${escapeHtml(cam.group_code)}` : ""
      } · ${escapeHtml(vendorLabel)} (${escapeHtml(protoLabel)})</span>
            </div>
            <span class="cam-status-badge ${isBound ? "is-live" : "is-pending"}">
              ${isBound ? `● 已接入 · 净余 ${freeSpots}/${totalSpots}` : `◐ 待绑平面组 · ${totalSpots}位`}
            </span>
          </div>

          <div class="cam-roster-meta">
            <div class="cam-meta-row">
              <span class="cam-meta-label">所属片区</span>
              <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:0;">
                <select class="cam-inline-zone-select js-cam-inline-zone"
                        data-cam-id="${escapeHtml(cam.camera_id)}"
                        title="直接切换该机位所属小区片区（自动保存并重算导览距离）">
                  ${zoneOpts}
                </select>
                <button type="button" class="apple-mini-pill-btn js-open-zone-modal"
                        data-zone-id="${escapeHtml(cam.zone_id || "")}"
                        title="编辑片区名称或新增补充片区">⚙️</button>
              </div>
            </div>

            <div class="cam-meta-row">
              <span class="cam-meta-label">绑定车位组</span>
              <div class="cam-meta-val" style="display:flex;align-items:center;justify-content:space-between;gap:6px;flex:1;flex-wrap:wrap;">
                <span>${boundSummary}</span>
                <span style="display:inline-flex;gap:5px;align-items:center;">
                  ${
                    !boundGroups.length
                      ? `<button type="button" class="apple-mini-pill-btn js-cam-place-on-map"
                                 data-cam-id="${escapeHtml(cam.camera_id)}"
                                 title="跳转到态势平面图，点击即可自动绘制并绑定">🗺️ 去平面图摆位</button>`
                      : ""
                  }
                  <button type="button" class="apple-mini-pill-btn js-cam-bind-group"
                          data-cam-id="${escapeHtml(cam.camera_id)}"
                          data-group-code="${escapeHtml(primaryGroupCode)}">${
                    boundGroups.length ? "换绑/改组" : "绑定车位组"
                  }</button>
                </span>
              </div>
            </div>

            <div class="cam-meta-row">
              <span class="cam-meta-label">安装位置</span>
              <span class="cam-meta-val">${
                cam.mount_address
                  ? escapeHtml(cam.mount_address)
                  : '<span class="subtle-text">未备注拍摄楼层/窗口（点下方「编辑信息」可补充）</span>'
              }</span>
            </div>

            <div class="cam-meta-row">
              <span class="cam-meta-label">空间特征</span>
              <div style="display:flex;gap:5px;flex-wrap:wrap;">
                <span class="badge-pill ${hasTpl ? "is-good" : "is-warn"}">${
        hasTpl ? "★ 已有参考模板" : "待传参考模板"
      }</span>
                ${hasCrop ? `<span class="badge-pill is-good">✂️ 固定区域裁剪</span>` : ""}
                ${anchorCnt > 0 ? `<span class="badge-pill">📍 ${anchorCnt} 锚点</span>` : ""}
              </div>
            </div>
          </div>

          <div class="cam-roster-footer">
            <div class="cam-roster-footer-left">
              <button type="button" class="btn-apple btn-apple-primary btn-apple-xs js-cam-open-ingest"
                      data-cam-id="${escapeHtml(cam.camera_id)}">🔌 接入配置与绑定</button>
              <button type="button" class="btn-apple btn-apple-tinted btn-apple-xs js-cam-go-calib"
                      data-cam-id="${escapeHtml(cam.camera_id)}">🎯 空间标定</button>
              <button type="button" class="btn-apple btn-apple-secondary btn-apple-xs js-cam-go-lab"
                      data-cam-id="${escapeHtml(cam.camera_id)}">🧪 抓拍测试</button>
              <button type="button" class="btn-apple btn-apple-secondary btn-apple-xs js-cam-edit-meta"
                      data-cam-id="${escapeHtml(cam.camera_id)}">✎ 编辑信息</button>
            </div>
            <div class="cam-roster-footer-left">
              ${
                boundGroups.length
                  ? `<button type="button" class="apple-mini-pill-btn js-unbind-group-cam"
                             data-cam-id="${escapeHtml(cam.camera_id)}"
                             data-group-code="${escapeHtml(boundGroups[0].group_code)}"
                             title="仅解除与平面图车位组的绑定，保留机位配置">⛓️‍💥 解绑</button>`
                  : ""
              }
              <button type="button" class="apple-mini-pill-btn is-danger js-delete-bound-cam"
                      data-cam-id="${escapeHtml(cam.camera_id)}"
                      data-group-code="${escapeHtml(boundGroups[0]?.group_code || "")}">🗑 删除机位</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

async function unbindSpotGroupCamera(groupCode) {
  if (!groupCode) return;
  try {
    if (state.siteMap.editMode) {
      await persistSiteMapDraftInPlace();
    }
    await api("/api/sitemap/bind-group", {
      method: "POST",
      body: JSON.stringify({
        group_code: groupCode,
        camera_id: "UNASSIGNED",
      }),
    });
    await refreshOverview();
    if (state.siteMap.editMode && state.overview) {
      const flatSpots = [];
      (state.overview.zones || []).forEach((z) => {
        (z.spots || []).forEach((sp) => {
          if (sp.map_placed === false) return;
          flatSpots.push(
            JSON.parse(
              JSON.stringify({
                ...sp,
                zone_id: sp.zone_id || z.zone_id,
                zone_name: sp.zone_name || z.name,
                group_name: sp.group_name || "",
                near_unit_id: sp.near_unit_id || "",
              })
            )
          );
        });
      });
      state.siteMap.draftSpots = flatSpots;
      syncSiteMapStudioControls();
      renderAbstractSvgMap(state.overview);
    }
    showAppleToast(`已解绑车位组 ${groupCode}（保留平面车位框与历史标定数据）`);
  } catch (err) {
    showAppleToast(`解绑失败：${err.message || err}`, "error", 4500);
  }
}

function bindSpatialGovernanceAndModals() {
  // 3-way Governance view switch + clickable KPI tiles
  document.querySelectorAll(".gov-view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.govViewMode = btn.dataset.govView || "building";
      syncGovernanceViewVisibility();
    });
  });
  document.querySelectorAll(".gov-kpi-tile[data-gov-kpi]").forEach((tile) => {
    tile.addEventListener("click", () => {
      state.govViewMode = tile.dataset.govKpi || "building";
      syncGovernanceViewVisibility();
    });
  });

  // Instant search across buildings, connected cameras, and zones
  const searchInput = document.getElementById("govSearchInput");
  searchInput?.addEventListener("input", () => {
    state.govSearchQuery = searchInput.value || "";
    if (state.overview) {
      renderBuildingClusters(state.overview);
      renderConnectedCameras(state.overview);
      renderZoneCards(state.overview.zones);
    }
  });

  // Map Footer Dock & Governance Hero CTAs
  document.getElementById("btnJumpToGovernance")?.addEventListener("click", () => {
    switchMainTab("governance");
  });
  document.getElementById("btnOpenZonesFromMap")?.addEventListener("click", () => {
    openZonesModal();
  });
  document.getElementById("btnGovOpenZonesModal")?.addEventListener("click", () => {
    openZonesModal();
  });
  document.getElementById("btnGovQuickCreateZone")?.addEventListener("click", () => {
    openZonesModal();
    document.getElementById("newZoneNameInput")?.focus();
  });
  document.getElementById("btnOpenZonesFromCalib")?.addEventListener("click", () => {
    const curZ = document.getElementById("calibZoneSelect")?.value || null;
    openZonesModal(curZ);
  });
  document.getElementById("btnGovOpenUnitsModal")?.addEventListener("click", openUnitsModal);
  document.getElementById("btnGovAddCamera")?.addEventListener("click", () => {
    openCameraConfigModal("create");
  });

  // Open / close Destination Units modal
  document.getElementById("btnOpenUnitsModal")?.addEventListener("click", openUnitsModal);
  document.getElementById("btnOpenUnitsModalFromStudio")?.addEventListener("click", openUnitsModal);
  document.getElementById("btnCloseUnitsModal")?.addEventListener("click", closeUnitsModal);
  document.getElementById("unitsModalBackdrop")?.addEventListener("click", closeUnitsModal);

  // Open / close Community Parking Zones modal
  document.getElementById("btnCloseZonesModal")?.addEventListener("click", closeZonesModal);
  document.getElementById("btnDoneZonesModal")?.addEventListener("click", closeZonesModal);
  document.getElementById("zonesModalBackdrop")?.addEventListener("click", closeZonesModal);
  document.getElementById("btnOpenZonesFromGroupModal")?.addEventListener("click", () => {
    const curZ = document.getElementById("grpModalZoneSelect")?.value || null;
    openZonesModal(curZ);
  });

  document.getElementById("btnAddZoneSubmit")?.addEventListener("click", async () => {
    const nameEl = document.getElementById("newZoneNameInput");
    const idEl = document.getElementById("newZoneIdInput");
    const descEl = document.getElementById("newZoneDescInput");
    const name = nameEl?.value?.trim() || "";
    const zone_id = idEl?.value?.trim() || "";
    const gate_access = document.getElementById("newZoneGateSelect")?.value || "south,east";
    const dead_end_penalty = Number(document.getElementById("newZoneDeadEndSelect")?.value || 0);
    const description = descEl?.value?.trim() || "";
    if (!name) {
      setModalFeedback("zoneCrudFeedback", false, "请输入片区名称（例如：10号楼北侧地面区）");
      nameEl?.focus();
      return;
    }
    try {
      const res = await api("/api/zones", {
        method: "POST",
        body: JSON.stringify({ zone_id, name, gate_access, dead_end_penalty, description }),
      });
      if (nameEl) nameEl.value = "";
      if (idEl) idEl.value = "";
      if (descEl) descEl.value = "";
      await refreshOverview();
      if (res.zone?.zone_id) {
        const calibZ = document.getElementById("calibZoneSelect");
        if (calibZ && [...calibZ.options].some((o) => o.value === res.zone.zone_id)) {
          calibZ.value = res.zone.zone_id;
          state.calib.zoneId = res.zone.zone_id;
        }
        const grpZ = document.getElementById("grpModalZoneSelect");
        if (grpZ && [...grpZ.options].some((o) => o.value === res.zone.zone_id)) {
          grpZ.value = res.zone.zone_id;
        }
      }
      renderZonesManageTable(res.zone?.zone_id || null);
      setModalFeedback(
        "zoneCrudFeedback",
        true,
        `✅ 已保存片区「${res.zone?.name || name} (${res.zone?.zone_id})」，机位与态势图可立即选用！`
      );
    } catch (err) {
      setModalFeedback("zoneCrudFeedback", false, `保存片区失败：${err.message}`);
    }
  });

  document.getElementById("zonesManageTbody")?.addEventListener("click", async (e) => {
    const saveBtn = e.target.closest(".js-zone-save-row");
    if (saveBtn) {
      const origId = saveBtn.dataset.zoneId;
      const row = saveBtn.closest("tr");
      const newId = row?.querySelector(".js-zone-id-edit")?.value?.trim() || origId;
      const name = row?.querySelector(".js-zone-name-edit")?.value?.trim() || "";
      const gate_access = row?.querySelector(".js-zone-gate-edit")?.value || "south,east";
      const dead_end_penalty = Number(row?.querySelector(".js-zone-deadend-edit")?.value || 0);
      const description = row?.querySelector(".js-zone-desc-edit")?.value?.trim() || "";
      if (!name) {
        setModalFeedback("zoneCrudFeedback", false, "片区名称不能为空");
        return;
      }
      saveBtn.disabled = true;
      try {
        const res = await api(`/api/zones/${encodeURIComponent(origId)}`, {
          method: "PUT",
          body: JSON.stringify({ zone_id: newId, name, gate_access, dead_end_penalty, description }),
        });
        await refreshOverview();
        renderZonesManageTable(res.zone?.zone_id || newId);
        setModalFeedback(
          "zoneCrudFeedback",
          true,
          `✅ 已更新片区「${res.zone?.name || name}」，关联机位与单元步行距离已自动同步！`
        );
      } catch (err) {
        setModalFeedback("zoneCrudFeedback", false, `更新失败：${err.message}`);
      } finally {
        saveBtn.disabled = false;
      }
      return;
    }

    const delBtn = e.target.closest(".js-zone-delete-row");
    if (delBtn) {
      const zid = delBtn.dataset.zoneId;
      delBtn.disabled = true;
      try {
        const res = await api(`/api/zones/${encodeURIComponent(zid)}`, {
          method: "DELETE",
        });
        await refreshOverview();
        renderZonesManageTable();
        setModalFeedback(
          "zoneCrudFeedback",
          true,
          `🗑️ 已删除片区 ${zid}（关联机位/车位已平滑归并至「${
            res.reassigned_zone_name || res.reassigned_to
          }」）`
        );
      } catch (err) {
        setModalFeedback("zoneCrudFeedback", false, `删除失败：${err.message}`);
      } finally {
        delBtn.disabled = false;
      }
    }
  });

  // Safe Camera Delete Modal handlers
  document
    .getElementById("delModalChoiceStack")
    ?.addEventListener("change", syncCameraDeleteChoiceCards);
  document.getElementById("btnCloseCameraDeleteModal")?.addEventListener(
    "click",
    closeCameraDeleteModal
  );
  document.getElementById("btnCancelCameraDeleteModal")?.addEventListener(
    "click",
    closeCameraDeleteModal
  );
  document.getElementById("cameraDeleteModalBackdrop")?.addEventListener(
    "click",
    closeCameraDeleteModal
  );
  document.getElementById("btnConfirmCameraDeleteModal")?.addEventListener("click", async () => {
    const camId = pendingCameraDeleteCtx.cameraId;
    const grpCode = pendingCameraDeleteCtx.groupCode;
    if (!camId) return;
    const chosen =
      document.querySelector('input[name="camDeleteMode"]:checked')?.value || "detach";
    const btn = document.getElementById("btnConfirmCameraDeleteModal");
    btn.disabled = true;
    try {
      if (chosen === "unbind_only" && grpCode) {
        await unbindSpotGroupCamera(grpCode);
        closeCameraDeleteModal();
        return;
      }
      const spotAction = chosen === "delete_spots" ? "delete_spots" : "detach";
      await api(
        `/api/cameras/${encodeURIComponent(camId)}?spot_action=${encodeURIComponent(spotAction)}`,
        { method: "DELETE" }
      );
      document.getElementById("camCrudFormPanel")?.classList.add("hidden");
      closeCameraDeleteModal();
      closeGroupBindModal();
      await refreshOverview();

      if (state.calib.cameraId === camId) {
        const nextCam = state.overview?.cameras?.[0]?.camera_id || "";
        state.calib.cameraId = nextCam;
        const camSel = document.getElementById("calibCameraSelect");
        if (nextCam && camSel) {
          camSel.value = nextCam;
          await loadCameraSpotsForCalib(nextCam);
        } else {
          state.calib.spots = [];
          renderCameraMetaSummaryCard();
          renderSpotListSidebar();
          renderCalibCanvas();
        }
      }
      const saveMsg = document.getElementById("calibSaveMsg");
      if (saveMsg) {
        saveMsg.style.color = "#1D9E31";
        saveMsg.textContent =
          spotAction === "delete_spots"
            ? `🗑️ 已彻底删除机位 ${camId} 及其名下车位`
            : `🗑️ 已删除机位 ${camId}（平面图车位已安全保留为待接相机）`;
      }
    } catch (err) {
      setModalFeedback("delModalFeedback", false, `执行失败：${err.message}`);
    } finally {
      btn.disabled = false;
    }
  });

  // Auto-suggest friendly Chinese name when typing unit_id (e.g. 9-2 -> 9号楼2单元)
  const newIdInput = document.getElementById("newUnitIdInput");
  const newNameInput = document.getElementById("newUnitNameInput");
  newIdInput?.addEventListener("input", () => {
    if (newNameInput && !newNameInput.dataset.userTyped) {
      newNameInput.value = formatAutoUnitNameClient(newIdInput.value);
    }
  });
  newNameInput?.addEventListener("input", () => {
    newNameInput.dataset.userTyped = newNameInput.value.trim() ? "1" : "";
  });

  document.getElementById("btnAddUnitSubmit")?.addEventListener("click", async () => {
    const rawId = newIdInput?.value?.trim() || "";
    if (!rawId) {
      setModalFeedback("unitCrudFeedback", false, "请输入单元编号（例如 9-2）");
      return;
    }
    const name = newNameInput?.value?.trim() || formatAutoUnitNameClient(rawId);
    const default_gate = document.getElementById("newUnitGateSelect")?.value || "south";
    try {
      const res = await api("/api/units", {
        method: "POST",
        body: JSON.stringify({ unit_id: rawId, name, default_gate }),
      });
      if (newIdInput) newIdInput.value = "";
      if (newNameInput) {
        newNameInput.value = "";
        newNameInput.dataset.userTyped = "";
      }
      const unitSel = document.getElementById("unitSelect");
      await refreshOverview();
      if (unitSel && res.unit?.unit_id) {
        unitSel.value = res.unit.unit_id;
        updateSiriHint();
        await refreshOverview();
      }
      renderUnitsManageTable();
      setModalFeedback(
        "unitCrudFeedback",
        true,
        `✅ 已保存目的地「${res.unit?.name || name} (${res.unit?.unit_id || rawId})」，已同步至目的地列表与平面图！`
      );
    } catch (err) {
      setModalFeedback("unitCrudFeedback", false, `保存失败：${err.message}`);
    }
  });

  document.getElementById("unitsManageTbody")?.addEventListener("click", async (e) => {
    const saveBtn = e.target.closest(".js-unit-save-row");
    if (saveBtn) {
      const uid = saveBtn.dataset.unitId;
      const row = saveBtn.closest("tr");
      const nameVal = row?.querySelector(".js-unit-name-edit")?.value?.trim() || "";
      const gateVal = row?.querySelector(".js-unit-gate-edit")?.value || "south";
      try {
        await api("/api/units", {
          method: "POST",
          body: JSON.stringify({ unit_id: uid, name: nameVal, default_gate: gateVal }),
        });
        await refreshOverview();
        renderUnitsManageTable();
        setModalFeedback("unitCrudFeedback", true, `✅ 已更新「${nameVal || uid}」`);
      } catch (err) {
        setModalFeedback("unitCrudFeedback", false, `更新失败：${err.message}`);
      }
      return;
    }

    const locBtn = e.target.closest(".js-unit-locate-door");
    if (locBtn) {
      focusUnitDoorInStudio(locBtn.dataset.unitId);
      return;
    }

    const delBtn = e.target.closest(".js-unit-delete-row");
    if (delBtn) {
      const uid = delBtn.dataset.unitId;
      try {
        await api(`/api/units/${encodeURIComponent(uid)}`, { method: "DELETE" });
        await refreshOverview();
        renderUnitsManageTable();
        setModalFeedback("unitCrudFeedback", true, `已删除单元 ${uid}`);
      } catch (err) {
        setModalFeedback("unitCrudFeedback", false, `删除失败：${err.message}`);
      }
    }
  });

  // Group Naming, Zone Reassignment, Lifecycle & Camera Binding Modal
  document.getElementById("btnCloseGroupBindModal")?.addEventListener("click", closeGroupBindModal);
  document.getElementById("btnCancelGroupBindModal")?.addEventListener("click", closeGroupBindModal);
  document.getElementById("groupBindModalBackdrop")?.addEventListener("click", closeGroupBindModal);

  document.getElementById("btnJumpCalibFromModal")?.addEventListener("click", () => {
    const camId = document.getElementById("grpModalCameraSelect")?.value || "";
    closeGroupBindModal();
    if (camId && camId !== "UNASSIGNED") {
      jumpToCalibrateCamera(camId, false);
    }
  });

  document.getElementById("btnUnbindCamFromModal")?.addEventListener("click", async () => {
    const origCode = document.getElementById("grpModalOrigCode")?.value?.trim() || "";
    if (!origCode) return;
    try {
      await unbindSpotGroupCamera(origCode);
      closeGroupBindModal();
    } catch (err) {
      setModalFeedback("grpModalFeedback", false, `解绑失败：${err.message}`);
    }
  });

  document.getElementById("btnDeleteBoundCamFromModal")?.addEventListener("click", () => {
    const origCode = document.getElementById("grpModalOrigCode")?.value?.trim() || "";
    const grp = findSpotGroupClient(origCode);
    const camId =
      grp?.camera_id || document.getElementById("grpModalCameraSelect")?.value || "";
    if (camId && camId !== "UNASSIGNED") {
      closeGroupBindModal();
      openCameraDeleteModal(camId, grp?.group_code || origCode);
    }
  });

  document.getElementById("btnDeleteGroupFromModal")?.addEventListener("click", async () => {
    const origCode = document.getElementById("grpModalOrigCode")?.value?.trim() || "";
    if (!origCode) return;
    try {
      await api("/api/sitemap/delete-group", {
        method: "POST",
        body: JSON.stringify({ group_code: origCode, camera_action: "keep" }),
      });
      closeGroupBindModal();
      await refreshOverview();
    } catch (err) {
      setModalFeedback("grpModalFeedback", false, `删除车位组失败：${err.message}`);
    }
  });

  document.getElementById("grpModalTargetGroupSelect")?.addEventListener("change", (e) => {
    const chosenCode = e.target.value;
    const prefillCamId = e.target.dataset.prefillCameraId || null;
    if (chosenCode === CREATE_NEW_MAP_GROUP_VALUE && prefillCamId) {
      setModalFeedback("grpModalFeedback", true, "");
      populateGroupBindModalForNewPlacement(prefillCamId);
      return;
    }
    const chosenGrp = findSpotGroupClient(chosenCode);
    if (chosenGrp) {
      setModalFeedback("grpModalFeedback", true, "");
      populateGroupBindModalFields(chosenGrp, prefillCamId);
    }
  });

  document.getElementById("btnSaveGroupBindModal")?.addEventListener("click", async () => {
    const origCode = document.getElementById("grpModalOrigCode")?.value?.trim() || "";
    const newCode =
      document.getElementById("grpModalCodeInput")?.value?.trim().toUpperCase() || "";
    const groupName = document.getElementById("grpModalNameInput")?.value?.trim() || "";
    const nearUnitId = document.getElementById("grpModalNearUnitSelect")?.value || "";
    const zoneId = document.getElementById("grpModalZoneSelect")?.value || "";
    const cameraId = document.getElementById("grpModalCameraSelect")?.value || "UNASSIGNED";
    if (!origCode) return;

    const btn = document.getElementById("btnSaveGroupBindModal");
    btn.disabled = true;
    try {
      if (origCode === CREATE_NEW_MAP_GROUP_VALUE) {
        if (!cameraId || cameraId === "UNASSIGNED") {
          setModalFeedback("grpModalFeedback", false, "请选择要自动新建并绑定的机位");
          return;
        }
        const spec = inferCameraPlacementDefaults(cameraId);
        const res = await api("/api/sitemap/place-camera", {
          method: "POST",
          body: JSON.stringify({
            camera_id: cameraId,
            orientation: spec?.orientation || "auto",
            group_code: newCode || spec?.baseCode || undefined,
            group_name: groupName || spec?.groupName || undefined,
            near_unit_id: nearUnitId || spec?.nearUnitId || undefined,
            zone_id: zoneId || spec?.zoneId || undefined,
          }),
        });
        closeGroupBindModal();
        if (state.siteMap.editMode) {
          await persistSiteMapDraftInPlace({
            selectGroupCode: res.group_code,
            selectCameraId: cameraId,
          });
        } else {
          await refreshOverview();
        }
        showAppleToast(
          `✅ 已在平面图新建独立车位组「${res.group_code}」并绑定 ${cameraId}（实标 ${res.spot_count} 位）`
        );
        return;
      }

      const existingGrp = findSpotGroupClient(origCode);
      if (
        existingGrp?.is_bound &&
        existingGrp.camera_id &&
        existingGrp.camera_id !== "UNASSIGNED" &&
        cameraId !== "UNASSIGNED" &&
        existingGrp.camera_id !== cameraId
      ) {
        const ok = window.confirm(
          `平面车位组「${origCode}」当前已绑定机位「${existingGrp.camera_id}」。\n\n是否确认换绑为「${cameraId}」？\n（若想为 ${cameraId} 新建独立车位组而不影响 ${origCode}，请点“取消”并在目标车位组下拉框选择第一项「✨ 在平面图自动新建独立车位组」！）`
        );
        if (!ok) return;
      }

      let activeCode = origCode;
      const renRes = await api("/api/sitemap/rename-group", {
        method: "POST",
        body: JSON.stringify({
          group_code: origCode,
          new_group_code: newCode || origCode,
          group_name: groupName,
          near_unit_id: nearUnitId,
          zone_id: zoneId || undefined,
          for_camera_id: cameraId !== "UNASSIGNED" ? cameraId : undefined,
        }),
      });
      activeCode = renRes.group_code || newCode || origCode;
      const bindRes = await api("/api/sitemap/bind-group", {
        method: "POST",
        body: JSON.stringify({
          group_code: activeCode,
          camera_id: cameraId,
          group_name: groupName || undefined,
          near_unit_id: nearUnitId,
          zone_id: zoneId || undefined,
        }),
      });
      closeGroupBindModal();
      if (state.siteMap.editMode) {
        await persistSiteMapDraftInPlace({
          selectGroupCode: bindRes.group_code || activeCode,
          selectCameraId: cameraId !== "UNASSIGNED" ? cameraId : "",
        });
      } else {
        await refreshOverview();
      }
      showAppleToast(
        cameraId !== "UNASSIGNED"
          ? `✅ 已保存并绑定车位组「${bindRes.group_code || activeCode}」→ ${cameraId}`
          : `✅ 已更新车位组「${activeCode}」信息`
      );
    } catch (err) {
      setModalFeedback("grpModalFeedback", false, `保存调整失败：${err.message}`);
    } finally {
      btn.disabled = false;
    }
  });

  // Pointer Drag-and-Drop from Unbound Cameras Banner directly onto #communitySvg / #communityRefSvg
  document.getElementById("unboundCamerasBanner")?.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (
      e.target.closest(
        ".js-quick-bind-cam, .js-open-bind-picker, .js-unbound-edit-cam, .js-unbound-del-cam"
      )
    ) {
      return;
    }
    const card = e.target.closest("[data-draggable-cam-id]");
    const camId = card?.dataset?.draggableCamId;
    if (!camId) return;
    const spec = inferCameraPlacementDefaults(camId);
    if (!spec) return;
    state.siteMap.dragging = {
      kind: "unbound-cam-drag",
      spec,
      fromPlaceBtn: Boolean(e.target.closest(".js-place-cam-on-map")),
      startClientX: e.clientX,
      startClientY: e.clientY,
      moved: false,
      hoverSvg: null,
      hoverWorld: null,
      snapGroup: null,
    };
  });

  // Event delegation for Unbound Cameras Banner
  document.getElementById("unboundCamerasBanner")?.addEventListener("click", async (e) => {
    if (state.siteMap.suppressBannerClickOnce) {
      state.siteMap.suppressBannerClickOnce = false;
      return;
    }
    const quickBtn = e.target.closest(".js-quick-bind-cam");
    if (quickBtn) {
      const camId = quickBtn.dataset.camId;
      const grpCode = quickBtn.dataset.groupCode;
      quickBtn.disabled = true;
      try {
        const res = await api("/api/sitemap/bind-group", {
          method: "POST",
          body: JSON.stringify({ camera_id: camId, group_code: grpCode }),
        });
        await refreshOverview();
        showAppleToast(
          `✅ 已将 ${camId} 一键关联至平面组 ${res.group_code || grpCode}（实标 ${
            res.spot_count || ""
          } 位）`
        );
      } catch (err) {
        showAppleToast(`绑定机位失败：${err.message}`, "error");
      } finally {
        quickBtn.disabled = false;
      }
      return;
    }
    const placeBtn = e.target.closest(".js-place-cam-on-map");
    if (placeBtn) {
      placeUnboundCameraOnFloorPlan(placeBtn.dataset.camId);
      return;
    }
    const pickerBtn = e.target.closest(".js-open-bind-picker");
    if (pickerBtn) {
      openGroupBindModal(pickerBtn.dataset.groupCode, pickerBtn.dataset.camId);
      return;
    }
    const editBtn = e.target.closest(".js-unbound-edit-cam");
    if (editBtn) {
      jumpToCalibrateCamera(editBtn.dataset.camId, true);
      return;
    }
    const delBtn = e.target.closest(".js-unbound-del-cam");
    if (delBtn) {
      openCameraDeleteModal(delBtn.dataset.camId);
    }
  });

  // HUD controls inside #siteMapFrame during interactive click-to-place
  document.getElementById("camPlaceHudBar")?.addEventListener("click", (e) => {
    const spec = state.siteMap.pendingCameraPlacement;
    if (!spec) return;
    if (e.target.closest(".js-cam-hud-cancel")) {
      cancelInteractiveCameraPlacement();
      return;
    }
    if (e.target.closest(".js-cam-hud-orient")) {
      spec.orientation = spec.orientation === "horizontal" ? "vertical" : "horizontal";
      renderCameraPlacementHud();
      if (state.siteMap.cursorWorld) {
        renderCameraDropPreviewDom(spec, state.siteMap.cursorWorld, null, null);
      }
      return;
    }
    if (e.target.closest(".js-cam-hud-rot")) {
      spec.angleOffset = ((Number(spec.angleOffset || 0) + 90) % 360);
      renderCameraPlacementHud();
      if (state.siteMap.cursorWorld) {
        renderCameraDropPreviewDom(spec, state.siteMap.cursorWorld, null, null);
      }
    }
  });

  // Event delegation for Building Cluster Cards
  document.getElementById("buildingClustersGrid")?.addEventListener("click", async (e) => {
    const unitBtn = e.target.closest(".js-select-dest-unit");
    if (unitBtn) {
      const uid = unitBtn.dataset.unitId;
      const sel = document.getElementById("unitSelect");
      if (sel && uid) {
        sel.value = uid;
        state.lastDoorWalkPath = null;
        updateSiriHint();
        await refreshOverview();
      }
      return;
    }
    const zoneTag = e.target.closest(".js-open-zone-from-bldg");
    if (zoneTag) {
      openZonesModal(zoneTag.dataset.zoneId || null);
      return;
    }
    const jumpCalibBtn = e.target.closest(".js-jump-cam-calib");
    if (jumpCalibBtn) {
      jumpToCalibrateCamera(jumpCalibBtn.dataset.camId, false);
      return;
    }
    const grpBtn = e.target.closest(".js-open-group-modal");
    if (grpBtn) {
      openGroupBindModal(grpBtn.dataset.groupCode);
      return;
    }
    const unbindBtn = e.target.closest(".js-unbind-group-cam");
    if (unbindBtn) {
      unbindBtn.disabled = true;
      try {
        await unbindSpotGroupCamera(unbindBtn.dataset.groupCode);
      } catch (err) {
        alert(`解绑失败：${err.message}`);
      } finally {
        unbindBtn.disabled = false;
      }
      return;
    }
    const delCamBtn = e.target.closest(".js-delete-bound-cam");
    if (delCamBtn) {
      openCameraDeleteModal(delCamBtn.dataset.camId, delCamBtn.dataset.groupCode || "");
      return;
    }
    const delGrpBtn = e.target.closest(".js-delete-spot-group");
    if (delGrpBtn) {
      const gCode = delGrpBtn.dataset.groupCode;
      delGrpBtn.disabled = true;
      try {
        await api("/api/sitemap/delete-group", {
          method: "POST",
          body: JSON.stringify({ group_code: gCode, camera_action: "keep" }),
        });
        await refreshOverview();
      } catch (err) {
        alert(`删除车位组失败：${err.message}`);
      } finally {
        delGrpBtn.disabled = false;
      }
      return;
    }
    const addUnitBtn = e.target.closest(".js-bldg-add-unit");
    if (addUnitBtn) {
      const bldgId = addUnitBtn.dataset.bldgId || "9";
      const existingNums = (state.overview?.units || [])
        .map((u) => {
          const m = String(u.unit_id || "").match(/^([A-Za-z0-9]+)[-_#](\d+)$/);
          return m && m[1].toUpperCase() === bldgId.toUpperCase() ? Number(m[2]) : 0;
        })
        .filter((n) => n > 0);
      const nextNo = (existingNums.length ? Math.max(...existingNums) : 1) + 1;
      const nextId = `${bldgId}-${nextNo}`;
      openUnitsModal();
      const idEl = document.getElementById("newUnitIdInput");
      const nmEl = document.getElementById("newUnitNameInput");
      if (idEl) idEl.value = nextId;
      if (nmEl) {
        nmEl.value = formatAutoUnitNameClient(nextId);
        nmEl.dataset.userTyped = "";
      }
    }
  });

  // Event delegation for Connected Cameras Roster Cards
  const camRoster = document.getElementById("connectedCamerasGrid");
  camRoster?.addEventListener("change", async (e) => {
    const zSel = e.target.closest(".js-cam-inline-zone");
    if (!zSel) return;
    const camId = zSel.dataset.camId;
    const zone_id = zSel.value;
    zSel.disabled = true;
    try {
      await api(`/api/cameras/${encodeURIComponent(camId)}`, {
        method: "PUT",
        body: JSON.stringify({ zone_id }),
      });
      await refreshOverview();
    } catch (err) {
      alert(`调整所属片区失败：${err.message}`);
      await refreshOverview();
    } finally {
      zSel.disabled = false;
    }
  });

  camRoster?.addEventListener("click", async (e) => {
    const ingestBtn = e.target.closest(".js-cam-open-ingest");
    if (ingestBtn) {
      openCameraConfigModal("edit", ingestBtn.dataset.camId);
      return;
    }
    const zModalBtn = e.target.closest(".js-open-zone-modal");
    if (zModalBtn) {
      openZonesModal(zModalBtn.dataset.zoneId || null);
      return;
    }
    const placeOnMapBtn = e.target.closest(".js-cam-place-on-map");
    if (placeOnMapBtn) {
      startInteractiveCameraPlacement(placeOnMapBtn.dataset.camId);
      return;
    }
    const bindBtn = e.target.closest(".js-cam-bind-group");
    if (bindBtn) {
      openGroupBindModal(bindBtn.dataset.groupCode, bindBtn.dataset.camId);
      return;
    }
    const calibBtn = e.target.closest(".js-cam-go-calib");
    if (calibBtn) {
      jumpToCalibrateCamera(calibBtn.dataset.camId, false);
      return;
    }
    const labBtn = e.target.closest(".js-cam-go-lab");
    if (labBtn) {
      jumpToLabCamera(labBtn.dataset.camId);
      return;
    }
    const editBtn = e.target.closest(".js-cam-edit-meta");
    if (editBtn) {
      jumpToCalibrateCamera(editBtn.dataset.camId, true);
      return;
    }
    const unbindBtn = e.target.closest(".js-unbind-group-cam");
    if (unbindBtn) {
      unbindBtn.disabled = true;
      try {
        await unbindSpotGroupCamera(unbindBtn.dataset.groupCode);
      } catch (err) {
        alert(`解绑失败：${err.message}`);
      } finally {
        unbindBtn.disabled = false;
      }
      return;
    }
    const delBtn = e.target.closest(".js-delete-bound-cam");
    if (delBtn) {
      openCameraDeleteModal(delBtn.dataset.camId, delBtn.dataset.groupCode || "");
    }
  });

  // Event delegation for Editable Zone Bento Cards
  document.getElementById("zoneCardsGrid")?.addEventListener("click", async (e) => {
    const editBtn = e.target.closest(".js-zone-card-edit");
    if (editBtn) {
      openZonesModal(editBtn.dataset.zoneId || null);
      return;
    }
    const addCamBtn = e.target.closest(".js-zone-card-add-cam");
    if (addCamBtn) {
      openCameraConfigModal("create", "", { presetZoneId: addCamBtn.dataset.zoneId || "" });
      return;
    }
    const delBtn = e.target.closest(".js-zone-card-delete");
    if (delBtn) {
      const zid = delBtn.dataset.zoneId;
      delBtn.disabled = true;
      try {
        await api(`/api/zones/${encodeURIComponent(zid)}`, { method: "DELETE" });
        await refreshOverview();
      } catch (err) {
        alert(`删除片区失败：${err.message}`);
      } finally {
        delBtn.disabled = false;
      }
    }
  });
}

function classifyAdminVoiceDialect(v) {
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
  if (/liaoning|晓北|东北/i.test(`${lang} ${name}`)) return { group: "dialect", tag: "东北话" };
  if (/shaanxi|晓妮|陕西/i.test(`${lang} ${name}`)) return { group: "dialect", tag: "陕西话" };
  if (lang.includes("tw") || /mei[-_ ]?jia|美佳|hsiaochen|hsiaoyu|yunjhe|曉臻|曉雨|雲哲/i.test(name)) {
    return { group: "dialect", tag: "台湾腔" };
  }
  return { group: "mandarin", tag: "普通话" };
}

function bindAdminVoiceSelector() {
  const sel = document.getElementById("adminVoiceSelect");
  if (!sel || !("speechSynthesis" in window)) return;
  const populate = () => {
    const all = window.speechSynthesis.getVoices() || [];
    const zh = all.filter((v) =>
      /^zh|chinese|cmn|yue|cantonese|sin-ji|tingting|mei-jia/i.test(`${v.lang || ""} ${v.name || ""}`)
    );
    zh.sort((a, b) => {
      const aL = a.localService !== false && !/online/i.test(String(a.name || "")) ? 0 : 1;
      const bL = b.localService !== false && !/online/i.test(String(b.name || "")) ? 0 : 1;
      return aL !== bL ? aL - bL : String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
    });
    sel.innerHTML = "";
    const defOpt = document.createElement("option");
    defOpt.value = "";
    defOpt.textContent = "🎙️ 默认音色";
    sel.appendChild(defOpt);
    const groups = {
      mandarin: { label: "普通话 · 国语", items: [] },
      cantonese: { label: "粤语 · 广东话 (善怡等)", items: [] },
      dialect: { label: "台湾腔与特色方言", items: [] },
    };
    const seen = new Set();
    const seenLabels = new Set();
    zh.forEach((v) => {
      const uri = String(v.voiceURI || v.name || "").trim();
      if (!uri || seen.has(uri)) return;
      const info = classifyAdminVoiceDialect(v);
      const shortName = String(v.name || uri)
        .replace(/^Microsoft\s+/i, "")
        .replace(/\s*Online\s*\(Natural\)\s*-\s*Chinese.*$/i, "")
        .replace(/\s*-\s*Chinese.*$/i, "")
        .trim();
      const online = /online|cloud|natural/i.test(`${v.name || ""} ${uri}`);
      const lbl = `${online ? "✨" : "⚡"} ${shortName} · ${info.tag}`;
      if (seenLabels.has(lbl) && uri !== state.adminVoiceUri) return;
      seen.add(uri);
      seenLabels.add(lbl);
      groups[info.group].items.push({
        uri,
        label: lbl,
      });
    });
    ["mandarin", "cantonese", "dialect"].forEach((gk) => {
      if (!groups[gk].items.length) return;
      const og = document.createElement("optgroup");
      og.label = groups[gk].label;
      groups[gk].items.forEach((it) => {
        const opt = document.createElement("option");
        opt.value = it.uri;
        opt.textContent = it.label;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
    sel.value = state.adminVoiceUri && seen.has(state.adminVoiceUri) ? state.adminVoiceUri : "";
  };
  populate();
  if ("onvoiceschanged" in window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = populate;
  }
  sel.addEventListener("change", () => {
    state.adminVoiceUri = sel.value || "";
    try {
      localStorage.setItem("parking_admin_voice_uri", state.adminVoiceUri);
    } catch (_) {}
    const curText = document.getElementById("voiceText")?.textContent?.trim() || "";
    if (curText) speakChinese(curText, true);
  });
}

function speakChinese(text, forceSpeak = false) {
  if (!forceSpeak && !document.getElementById("ttsEnabled")?.checked) return;
  if (!("speechSynthesis" in window) || !text) return;
  try {
    if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
      window.speechSynthesis.cancel();
    }
    const voices = window.speechSynthesis.getVoices() || [];
    const prefLang = String(state.workspace?.preferred_voice_lang || "").trim().replace(/_/g, "-");
    let chosen = null;
    if (state.adminVoiceUri) {
      const matches = voices.filter((v) => v.voiceURI === state.adminVoiceUri || v.name === state.adminVoiceUri);
      chosen = matches.find((v) => v.localService !== false) || matches[0] || null;
    } else if (prefLang) {
      chosen =
        voices.find(
          (v) =>
            !/online|cloud|natural/i.test(String(v.name || "")) &&
            String(v.lang || "").replace(/_/g, "-").toLowerCase().startsWith(prefLang.toLowerCase())
        ) || null;
    }
    const utter = new SpeechSynthesisUtterance(text);
    if (chosen) {
      utter.voice = chosen;
      const dInfo = classifyAdminVoiceDialect(chosen);
      const rawLang = String(chosen.lang || "").trim().replace(/_/g, "-");
      if (dInfo.group === "cantonese") {
        utter.lang = /^(zh-HK|zh-MO|yue)/i.test(rawLang) ? rawLang : "zh-HK";
      } else if (dInfo.tag === "台湾腔") {
        utter.lang = /^zh-TW/i.test(rawLang) ? rawLang : "zh-TW";
      } else {
        utter.lang = rawLang || prefLang || "zh-CN";
      }
    } else {
      utter.lang = prefLang || "zh-CN";
    }
    utter.rate = 1.04;
    window.speechSynthesis.speak(utter);
  } catch (_) {}
}

async function refreshWorkspacesCatalog() {
  const data = await api("/api/workspaces");
  state.workspacesList = data.workspaces || [];
  state.scenePresets = data.scene_presets || {};
  state.broadcastTemplates = data.broadcast_templates || {};
  if (!state.workspacesList.some((w) => w.workspace_id === state.workspaceId)) {
    state.workspaceId = "default";
    try {
      localStorage.setItem("parking_admin_ws_id", "default");
    } catch (_) {}
  }
  const sel = document.getElementById("workspaceSwitcherSelect");
  if (sel) {
    sel.innerHTML = "";
    state.workspacesList.forEach((ws) => {
      const opt = document.createElement("option");
      opt.value = ws.workspace_id;
      const icon = (ws.scene_label || "🏘️").split(" ")[0];
      opt.textContent = `${icon} ${ws.name} (${ws.total_effective_free}/${ws.total_capacity})`;
      sel.appendChild(opt);
    });
    const newOpt = document.createElement("option");
    newOpt.value = "__create_new__";
    newOpt.textContent = "＋ 新建独立小区 / 办公区工作空间…";
    sel.appendChild(newOpt);
    sel.value = state.workspaceId;
  }
  return data;
}

async function switchActiveWorkspace(nextWsId) {
  const cleanId = String(nextWsId || "default").trim() || "default";
  state.workspaceId = cleanId;
  try {
    localStorage.setItem("parking_admin_ws_id", cleanId);
    const u = new URL(window.location.href);
    if (cleanId === "default") u.searchParams.delete("ws");
    else u.searchParams.set("ws", cleanId);
    window.history.replaceState(null, "", u.toString());
  } catch (_) {}
  state.siteMap.backdropDataUrl = null;
  state.siteMap.backdropLoaded = false;
  state.calib.cameraId = "";
  await refreshWorkspacesCatalog();
  await refreshOverview();
  const cam0 = state.overview?.cameras?.[0]?.camera_id || "";
  if (cam0) {
    await loadCameraSpotsForCalib(cam0);
  }
  showAppleToast(`已切换至工作空间：${state.overview?.community_name || cleanId}`);
}

function buildWorkspaceExclusiveUrls(wsId) {
  const cfg = state.overview?.config || {};
  const vg = cfg.voice_gateway || {};
  const mobPort = cfg.mobile_http_port || 18790;
  const voicePort = cfg.voice_http_port || 18791;
  const mobBase = (vg.public_mobile_url || `${location.protocol}//${location.hostname}:${mobPort}`).replace(/\/+$/, "");
  const voiceBase = (vg.public_voice_url || `${location.protocol}//${location.hostname}:${voicePort}`).replace(/\/+$/, "");
  const wsSeg = wsId && wsId !== "default" ? `/w/${encodeURIComponent(wsId)}` : "";
  return {
    url18790: `${mobBase}${wsSeg}/`,
    url18791: `${voiceBase}${wsSeg}/`,
  };
}

function populateWorkspaceModalFields(mode) {
  state.wsModalMode = mode === "create" ? "create" : "edit";
  document.querySelectorAll("#wsModalModeSeg .ws-mode-btn").forEach((b) => {
    b.classList.toggle("is-active", b.getAttribute("data-wsmode") === state.wsModalMode);
  });
  const isCreate = state.wsModalMode === "create";
  document.getElementById("wsSlugInput").disabled = !isCreate;
  document.getElementById("wsCreateInitRow").classList.toggle("hidden", !isCreate);
  const curWs =
    state.workspacesList.find((w) => w.workspace_id === state.workspaceId) ||
    state.workspace ||
    {};
  document.getElementById("btnDeleteWorkspace").classList.toggle(
    "hidden",
    isCreate || curWs.workspace_id === "default"
  );
  document.getElementById("wsExclusiveLinksBox").classList.toggle("hidden", isCreate);

  if (isCreate) {
    const defPreset = state.scenePresets.community || {};
    document.getElementById("wsCurrentSlugPill").textContent = "新建独立站点";
    document.getElementById("wsNameInput").value = "";
    document.getElementById("wsShortNameInput").value = "";
    document.getElementById("wsSlugInput").value = "";
    document.getElementById("wsSceneTypeSelect").value = "community";
    document.getElementById("wsRoleTitleInput").value = defPreset.role_title || "业主";
    document.getElementById("wsPrefVoiceLangSelect").value = "";
    document.getElementById("wsGreetingTplInput").value = defPreset.greeting_template || "";
    document.getElementById("wsFullTplInput").value = defPreset.full_template || "";
    document.getElementById("wsArrivalPlaceInput").value = "";
    document.getElementById("wsViewerTokPill").textContent = "选填";
    document.getElementById("wsAdminTokPill").textContent = "选填";
  } else {
    const wsId = curWs.workspace_id || "default";
    document.getElementById("wsCurrentSlugPill").textContent = `workspace: ${wsId}`;
    document.getElementById("wsNameInput").value = curWs.name || "";
    document.getElementById("wsShortNameInput").value = curWs.short_name || "";
    document.getElementById("wsSlugInput").value = wsId;
    document.getElementById("wsSceneTypeSelect").value = curWs.scene_type || "community";
    document.getElementById("wsRoleTitleInput").value = curWs.role_title || "业主";
    document.getElementById("wsPrefVoiceLangSelect").value = curWs.preferred_voice_lang || "";
    document.getElementById("wsGreetingTplInput").value = curWs.greeting_template || "";
    document.getElementById("wsFullTplInput").value = curWs.full_template || "";
    document.getElementById("wsArrivalPlaceInput").value = curWs.arrival_place || "";
    document.getElementById("wsViewerTokPill").textContent = curWs.has_access_token
      ? `已设 (${curWs.access_token_masked})`
      : "免口令";
    document.getElementById("wsAdminTokPill").textContent = curWs.has_admin_token
      ? `已设 (${curWs.admin_token_masked})`
      : "未设置";
    const urls = buildWorkspaceExclusiveUrls(wsId);
    document.getElementById("wsLink18790").textContent = urls.url18790;
    document.getElementById("wsLink18791").textContent = urls.url18791;
  }
  document.getElementById("wsAccessTokenInput").value = "";
  document.getElementById("wsAdminTokenInput").value = "";
  document.getElementById("wsClearAccessTokenChk").checked = false;
  document.getElementById("wsClearAdminTokenChk").checked = false;
  document.getElementById("wsModalFeedback").textContent = "";
  updateWorkspaceLiveBroadcastPreview();
}

async function updateWorkspaceLiveBroadcastPreview() {
  const targetWs = state.wsModalMode === "create" ? "default" : state.workspaceId || "default";
  const draft = {
    name: document.getElementById("wsNameInput").value.trim() || "阳光花园",
    short_name: document.getElementById("wsShortNameInput").value.trim(),
    scene_type: document.getElementById("wsSceneTypeSelect").value,
    role_title: document.getElementById("wsRoleTitleInput").value.trim() || "业主",
    greeting_template: document.getElementById("wsGreetingTplInput").value.trim(),
    full_template: document.getElementById("wsFullTplInput").value.trim(),
  };
  try {
    const res = await api(`/api/workspaces/${encodeURIComponent(targetWs)}/preview-broadcast`, {
      method: "POST",
      body: JSON.stringify(draft),
    });
    document.getElementById("wsLivePreviewText").textContent = res.sample_voice_text || "";
    return res.sample_voice_text || "";
  } catch (_) {
    return "";
  }
}

function bindWorkspaceModal() {
  const modal = document.getElementById("workspaceModal");
  if (!modal) return;
  const openModal = async (mode = "edit") => {
    await refreshWorkspacesCatalog();
    populateWorkspaceModalFields(mode);
    modal.classList.remove("hidden");
  };
  const closeModal = () => modal.classList.add("hidden");

  document.getElementById("btnOpenWorkspaceModal")?.addEventListener("click", () => openModal("edit"));
  document.getElementById("btnCloseWorkspaceModal")?.addEventListener("click", closeModal);
  document.getElementById("btnCancelWorkspaceModal")?.addEventListener("click", closeModal);
  document.getElementById("workspaceModalBackdrop")?.addEventListener("click", closeModal);

  document.getElementById("workspaceSwitcherSelect")?.addEventListener("change", async (e) => {
    const val = e.target.value;
    if (val === "__create_new__") {
      e.target.value = state.workspaceId;
      await openModal("create");
      return;
    }
    await switchActiveWorkspace(val);
  });

  document.getElementById("wsModalModeSeg")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".ws-mode-btn");
    if (!btn) return;
    populateWorkspaceModalFields(btn.getAttribute("data-wsmode") || "edit");
  });

  document.getElementById("wsSceneTypeSelect")?.addEventListener("change", (e) => {
    const preset = state.scenePresets[e.target.value];
    if (preset) {
      document.getElementById("wsRoleTitleInput").value = preset.role_title || "";
      document.getElementById("wsGreetingTplInput").value = preset.greeting_template || "";
      document.getElementById("wsFullTplInput").value = preset.full_template || "";
      updateWorkspaceLiveBroadcastPreview();
    }
  });

  document.getElementById("wsTplPresetChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".ws-chip-btn");
    if (!chip) return;
    const tplObj = state.broadcastTemplates[chip.getAttribute("data-tplkey") || ""];
    if (tplObj) {
      document.getElementById("wsGreetingTplInput").value = tplObj.greeting_template || "";
      document.getElementById("wsFullTplInput").value = tplObj.full_template || "";
      updateWorkspaceLiveBroadcastPreview();
    }
  });

  ["wsNameInput", "wsShortNameInput", "wsRoleTitleInput", "wsGreetingTplInput", "wsFullTplInput"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => {
      clearTimeout(bindWorkspaceModal._prevTimer);
      bindWorkspaceModal._prevTimer = setTimeout(updateWorkspaceLiveBroadcastPreview, 220);
    });
  });

  document.getElementById("btnWsPreviewSpeak")?.addEventListener("click", async () => {
    const sample = await updateWorkspaceLiveBroadcastPreview();
    if (sample) speakChinese(sample, true);
  });

  document.getElementById("btnCopyWs18790")?.addEventListener("click", async () => {
    const txt = document.getElementById("wsLink18790").textContent || "";
    await navigator.clipboard?.writeText(txt).catch(() => {});
    showAppleToast("已复制本场所专属 :18790 平面图链接");
  });
  document.getElementById("btnCopyWs18791")?.addEventListener("click", async () => {
    const txt = document.getElementById("wsLink18791").textContent || "";
    await navigator.clipboard?.writeText(txt).catch(() => {});
    showAppleToast("已复制本场所专属 :18791 快捷指令链接");
  });

  document.getElementById("btnSaveWorkspaceModal")?.addEventListener("click", async () => {
    const fb = document.getElementById("wsModalFeedback");
    fb.textContent = "";
    const payload = {
      name: document.getElementById("wsNameInput").value.trim(),
      short_name: document.getElementById("wsShortNameInput").value.trim(),
      scene_type: document.getElementById("wsSceneTypeSelect").value,
      role_title: document.getElementById("wsRoleTitleInput").value.trim(),
      preferred_voice_lang: document.getElementById("wsPrefVoiceLangSelect").value,
      greeting_template: document.getElementById("wsGreetingTplInput").value.trim(),
      full_template: document.getElementById("wsFullTplInput").value.trim(),
      arrival_place: document.getElementById("wsArrivalPlaceInput").value.trim(),
      access_token: document.getElementById("wsAccessTokenInput").value.trim(),
      admin_token: document.getElementById("wsAdminTokenInput").value.trim(),
      clear_access_token: document.getElementById("wsClearAccessTokenChk").checked,
      clear_admin_token: document.getElementById("wsClearAdminTokenChk").checked,
    };
    try {
      if (state.wsModalMode === "create") {
        payload.workspace_id = document.getElementById("wsSlugInput").value.trim();
        payload.template_mode = document.getElementById("wsTemplateInitSelect").value;
        const res = await api("/api/workspaces", { method: "POST", body: JSON.stringify(payload) });
        const newId = res.workspace?.workspace_id;
        if (payload.admin_token && newId) {
          saveWsAdminToken(newId, payload.admin_token);
        }
        closeModal();
        if (newId) await switchActiveWorkspace(newId);
      } else {
        const curId = state.workspaceId || "default";
        if (payload.admin_token) {
          saveWsAdminToken(curId, payload.admin_token);
        } else if (payload.clear_admin_token) {
          saveWsAdminToken(curId, "");
        }
        await api(`/api/workspaces/${encodeURIComponent(curId)}/update`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        closeModal();
        await refreshWorkspacesCatalog();
        await refreshOverview();
        showAppleToast("工作空间设置与自定义播报语已生效");
      }
    } catch (err) {
      fb.textContent = err.message || "保存失败";
    }
  });

  document.getElementById("btnDeleteWorkspace")?.addEventListener("click", async () => {
    const curId = state.workspaceId || "";
    if (!curId || curId === "default") return;
    if (!window.confirm(`确定要彻底删除工作空间「${curId}」及其独立平面图数据吗？默认主空间不受影响。`)) {
      return;
    }
    try {
      await api(`/api/workspaces/${encodeURIComponent(curId)}`, { method: "DELETE" });
      closeModal();
      await switchActiveWorkspace("default");
    } catch (err) {
      document.getElementById("wsModalFeedback").textContent = err.message || "删除失败";
    }
  });
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

async function syncSiteMapBackdropIfNeeded(siteMapMeta) {
  if (!siteMapMeta || !siteMapMeta.has_backdrop) {
    state.siteMap.backdropDataUrl = "";
    state.siteMap.backdropUpdatedAt = 0;
    state.siteMap.showBackdrop = false;
    return;
  }
  if (
    state.siteMap.backdropDataUrl &&
    state.siteMap.backdropUpdatedAt === siteMapMeta.updated_at
  ) {
    return;
  }
  try {
    const full = await api("/api/sitemap");
    state.siteMap.backdropDataUrl = full.site_map?.backdrop_jpeg_b64 || "";
    state.siteMap.backdropUpdatedAt = full.site_map?.updated_at || siteMapMeta.updated_at;
  } catch (_) {}
}

function setDriverPanelCollapsed(collapsed, isAuto = false) {
  state.siteMap.driverCollapsed = Boolean(collapsed);
  state.siteMap.driverAutoCollapsedByEdit = Boolean(collapsed && isAuto);
  syncMapWorkspaceLayout();
}

function setCompareSplitMode(splitOn, byUser = false) {
  const nextSplit = Boolean(splitOn);
  state.siteMap.compareSplit = nextSplit;
  if (byUser) {
    state.siteMap.userToggledCompare = true;
  }
  if (nextSplit && state.siteMap.backdropDataUrl && !state.siteMap.userToggledBackdrop) {
    // Keep right pane vector-clean with subtle 20% ghost overlay while left pane shows 100% crisp floor plan
    state.siteMap.showBackdrop = true;
    state.siteMap.backdropAlpha = 0.2;
    const slider = document.getElementById("mapBackdropAlphaSlider");
    if (slider) slider.value = "20";
    const alphaVal = document.getElementById("mapBackdropAlphaVal");
    if (alphaVal) alphaVal.textContent = "20%";
  }
  syncMapWorkspaceLayout();
  updateSiteMapHeaderUI(state.overview?.site_map);
  if (state.overview) renderAbstractSvgMap(state.overview);
}

function syncMapWorkspaceLayout() {
  const isEdit = Boolean(state.siteMap.editMode);
  const isSplit = Boolean(state.siteMap.compareSplit);
  const isCollapsed = Boolean(state.siteMap.driverCollapsed);
  const isInspectorOpen = Boolean(state.siteMap.inspectorOpen);
  const onDriverTab = document.getElementById("tab-driver")?.classList.contains("active") !== false;

  document.body.classList.toggle("studio-wide", onDriverTab && (isEdit || isSplit || isCollapsed));

  const bento = document.getElementById("driverBentoGrid");
  const driverCard = document.getElementById("driverGuidanceCard");
  const expandRail = document.getElementById("btnExpandDriverPanel");
  const expandedBody = document.getElementById("driverExpandedBody");
  bento?.classList.toggle("driver-collapsed", isCollapsed);
  driverCard?.classList.toggle("is-collapsed", isCollapsed);
  expandRail?.classList.toggle("hidden", !isCollapsed);
  expandedBody?.classList.toggle("hidden", isCollapsed);

  document.getElementById("mapEditTopBar")?.classList.toggle("hidden", !isEdit);
  document.getElementById("mapEditToolbar")?.classList.toggle("hidden", !(isEdit && isInspectorOpen));
  document
    .getElementById("mapWorkspaceBody")
    ?.classList.toggle("with-sidebar", isEdit && isInspectorOpen);

  const btnDrawer = document.getElementById("btnToggleInspectorDrawer");
  if (btnDrawer) {
    btnDrawer.classList.toggle("active", isInspectorOpen);
    btnDrawer.textContent = isInspectorOpen ? "◧ 收起属性栏" : "◨ 展开属性栏";
  }

  const splitStage = document.getElementById("siteMapSplitStage");
  splitStage?.classList.toggle("is-split", isSplit);
  splitStage?.classList.toggle("is-editing", isEdit);

  document.getElementById("siteMapRefPane")?.classList.toggle("hidden", !isSplit);
  document.getElementById("siteMapMainPaneBar")?.classList.toggle("hidden", !(isSplit || isEdit));
  document.getElementById("siteMapFrame")?.classList.toggle("editing", isEdit);
  document.getElementById("mapFooterDock")?.classList.toggle("hidden", isEdit);

  const btnSplitHdr = document.getElementById("btnToggleSplitCompare");
  if (btnSplitHdr) {
    btnSplitHdr.classList.toggle("active", isSplit);
    btnSplitHdr.textContent = isSplit ? "◫ 退出对比" : "◫ 左右对比";
  }

  document.querySelectorAll(".map-viewmode-btn").forEach((btn) => {
    const mode = btn.dataset.compareMode;
    btn.classList.toggle("active", (mode === "split") === isSplit);
  });
}

function updateSiteMapHeaderUI(siteMapMeta) {
  const btnBg = document.getElementById("btnToggleMapBackdrop");
  const noteEl = document.getElementById("siteMapStatusNote");
  const alphaWrap = document.getElementById("mapBackdropOpacityWrap");
  const alphaSlider = document.getElementById("mapBackdropAlphaSlider");
  const alphaValEl = document.getElementById("mapBackdropAlphaVal");
  const refUploadInline = document.getElementById("btnRefUploadInline");
  const hasBg = Boolean(siteMapMeta?.has_backdrop && state.siteMap.backdropDataUrl);

  if (btnBg) {
    btnBg.disabled = !hasBg;
    if (!hasBg) {
      btnBg.textContent = "纯净矢量";
    } else {
      btnBg.textContent = state.siteMap.showBackdrop ? "底图叠显中 · 切回矢量" : "纯净矢量 · 叠显底图";
    }
  }
  if (alphaWrap) {
    alphaWrap.classList.toggle("hidden", !hasBg);
  }
  if (alphaSlider) {
    const curPct = state.siteMap.showBackdrop
      ? Math.round(Number(state.siteMap.backdropAlpha ?? 0.25) * 100)
      : 0;
    alphaSlider.value = String(curPct);
    if (alphaValEl) alphaValEl.textContent = `${curPct}%`;
  }
  if (refUploadInline) {
    refUploadInline.classList.remove("hidden");
    refUploadInline.textContent = hasBg ? "更换原图" : "上传平面图";
  }
  if (noteEl) {
    const spotCount = (state.overview?.zones || []).reduce(
      (acc, z) => acc + ((z.spots || []).length || 0),
      0
    );
    const summary = siteMapMeta?.meta?.scene_summary || "";
    const kb = siteMapMeta?.meta?.backdrop_kb
      ? ` · 底图压缩缓存 ${siteMapMeta.meta.backdrop_kb} KB`
      : "";
    const baseTxt = summary ? `平面图拓扑：${summary}${kb}` : "";
    const editTip = state.siteMap.editMode
      ? `左右对比编辑中：左侧原图与右侧态势图同步缩放/平移，右侧停靠面板调节属性（左侧语音寻位栏可随时点击『⟩』展开）`
      : `当前按实际标定逐位渲染 ${spotCount} 个车位（支持『◫ 左右对比』同步对照原平面图，点击『微调与编辑』进入左右工作台）`;
    noteEl.textContent = baseTxt ? `${baseTxt} · ${editTip}` : editTip;
  }
}

function getSiteMapMetrics(smap) {
  const aspect = Math.min(2.6, Math.max(0.45, Number(smap?.aspect_ratio || 1.45)));
  const worldW = aspect >= 1.0 ? +(100 * aspect).toFixed(3) : 100;
  const worldH = aspect >= 1.0 ? 100 : +(100 / aspect).toFixed(3);
  return {
    aspect,
    worldW,
    worldH,
    px: (nx) => +((Number(nx) / 100) * worldW).toFixed(3),
    py: (ny) => +((Number(ny) / 100) * worldH).toFixed(3),
    nx: (sx) => +((Number(sx) / worldW) * 100).toFixed(2),
    ny: (sy) => +((Number(sy) / worldH) * 100).toFixed(2),
  };
}

function applySiteMapViewBox(svg, worldW, worldH) {
  const zoom = Math.min(5.5, Math.max(1.0, Number(state.siteMap.zoom || 1.0)));
  const visW = worldW / zoom;
  const visH = worldH / zoom;
  let cx = state.siteMap.camCenterX;
  let cy = state.siteMap.camCenterY;
  if (cx == null || cy == null || zoom <= 1.001) {
    cx = worldW / 2;
    cy = worldH / 2;
  } else {
    cx = Math.min(worldW - visW / 2, Math.max(visW / 2, cx));
    cy = Math.min(worldH - visH / 2, Math.max(visH / 2, cy));
  }
  state.siteMap.zoom = zoom;
  state.siteMap.camCenterX = cx;
  state.siteMap.camCenterY = cy;
  const vb = `${(cx - visW / 2).toFixed(3)} ${(cy - visH / 2).toFixed(3)} ${visW.toFixed(
    3
  )} ${visH.toFixed(3)}`;
  if (svg) svg.setAttribute("viewBox", vb);
  const refSvg = document.getElementById("communityRefSvg");
  if (refSvg) refSvg.setAttribute("viewBox", vb);
  const resetBtn = document.getElementById("btnMapZoomReset");
  if (resetBtn) {
    resetBtn.textContent = `${Math.round(zoom * 100)}%`;
  }
}

function clientToSiteMapWorld(svg, evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return [50, 50];
  const svgP = pt.matrixTransform(ctm.inverse());
  return [svgP.x, svgP.y];
}

function zoomSiteMapAt(factor, anchorWorld = null) {
  if (!state.overview) return;
  const svg = document.getElementById("communitySvg");
  const { worldW, worldH } = getSiteMapMetrics(state.overview.site_map);
  const oldZoom = state.siteMap.zoom || 1.0;
  const newZoom = Math.min(5.5, Math.max(1.0, +(oldZoom * factor).toFixed(3)));
  if (Math.abs(newZoom - oldZoom) < 0.001) return;

  const oldCx = state.siteMap.camCenterX ?? worldW / 2;
  const oldCy = state.siteMap.camCenterY ?? worldH / 2;
  if (anchorWorld && newZoom > 1.001) {
    const [wx, wy] = anchorWorld;
    state.siteMap.camCenterX = wx - (wx - oldCx) * (oldZoom / newZoom);
    state.siteMap.camCenterY = wy - (wy - oldCy) * (oldZoom / newZoom);
  }
  state.siteMap.zoom = newZoom;
  applySiteMapViewBox(svg, worldW, worldH);
  renderAbstractSvgMap(state.overview);
}

function resetSiteMapZoom() {
  if (!state.overview) return;
  const svg = document.getElementById("communitySvg");
  const { worldW, worldH } = getSiteMapMetrics(state.overview.site_map);
  state.siteMap.zoom = 1.0;
  state.siteMap.camCenterX = worldW / 2;
  state.siteMap.camCenterY = worldH / 2;
  applySiteMapViewBox(svg, worldW, worldH);
  renderAbstractSvgMap(state.overview);
}

function normalizeDeg(a) {
  let v = Number(a || 0);
  while (v <= -180) v += 360;
  while (v > 180) v -= 360;
  return +v.toFixed(1);
}

function snapshotSiteMapDraft() {
  return JSON.parse(
    JSON.stringify({
      draftSpots: state.siteMap.draftSpots || [],
      draftUnits: state.siteMap.draftUnits || [],
      draftRoads: state.siteMap.draftRoads || [],
      draftRoutes: state.siteMap.draftRoutes || [],
      draftWalkways: state.siteMap.draftWalkways || [],
      draftSpanMeters: state.siteMap.draftSpanMeters || 185,
      draftGates: state.siteMap.draftGates || [],
      deletedSpotIds: state.siteMap.deletedSpotIds || [],
      deletedUnitIds: state.siteMap.deletedUnitIds || [],
      selectedSpotIds: state.siteMap.selectedSpotIds || [],
      selectedSpotId: state.siteMap.selectedSpotId || null,
    })
  );
}

function restoreSiteMapDraft(snap) {
  if (!snap) return;
  state.siteMap.draftSpots = JSON.parse(JSON.stringify(snap.draftSpots || []));
  state.siteMap.draftUnits = JSON.parse(JSON.stringify(snap.draftUnits || []));
  state.siteMap.draftRoads = JSON.parse(JSON.stringify(snap.draftRoads || []));
  state.siteMap.draftRoutes = JSON.parse(JSON.stringify(snap.draftRoutes || []));
  state.siteMap.draftWalkways = JSON.parse(JSON.stringify(snap.draftWalkways || []));
  state.siteMap.draftSpanMeters = Number(snap.draftSpanMeters || 185);
  state.siteMap.draftGates = JSON.parse(JSON.stringify(snap.draftGates || []));
  state.siteMap.deletedSpotIds = [...(snap.deletedSpotIds || [])];
  state.siteMap.deletedUnitIds = [...(snap.deletedUnitIds || [])];
  setSelectedSpotIds(snap.selectedSpotIds || [], snap.selectedSpotId || null);
  syncSiteMapStudioControls();
  if (state.overview) renderAbstractSvgMap(state.overview);
}

function pushSiteMapUndo() {
  if (!state.siteMap.editMode) return;
  state.siteMap.undoStack.push(snapshotSiteMapDraft());
  if (state.siteMap.undoStack.length > 40) state.siteMap.undoStack.shift();
  state.siteMap.redoStack = [];
  updateSiteMapUndoButtons();
}

function undoSiteMapEdit() {
  if (!state.siteMap.editMode || state.siteMap.undoStack.length === 0) return;
  state.siteMap.redoStack.push(snapshotSiteMapDraft());
  const prev = state.siteMap.undoStack.pop();
  restoreSiteMapDraft(prev);
  updateSiteMapUndoButtons();
}

function redoSiteMapEdit() {
  if (!state.siteMap.editMode || state.siteMap.redoStack.length === 0) return;
  state.siteMap.undoStack.push(snapshotSiteMapDraft());
  const next = state.siteMap.redoStack.pop();
  restoreSiteMapDraft(next);
  updateSiteMapUndoButtons();
}

function updateSiteMapUndoButtons() {
  const uBtn = document.getElementById("btnMapUndo");
  const rBtn = document.getElementById("btnMapRedo");
  if (uBtn) uBtn.disabled = state.siteMap.undoStack.length === 0;
  if (rBtn) rBtn.disabled = state.siteMap.redoStack.length === 0;
}

function getSpotGroupKey(sp) {
  if (sp.group_code) return sp.group_code;
  if (sp.camera_id && sp.camera_id !== "UNASSIGNED") return sp.camera_id;
  return sp.spot_id || sp.zone_id || "DEFAULT";
}

function allocateNextGroupCode(spots = state.siteMap.draftSpots || []) {
  const usedNums = new Set();
  const usedCodes = new Set();
  (spots || []).forEach((s) => {
    const code = String(s.group_code || "").trim().toUpperCase();
    if (!code) return;
    usedCodes.add(code);
    const m = code.match(/^G0*(\d+)$/);
    if (m) usedNums.add(Number(m[1]));
  });
  let n = 1;
  while (usedNums.has(n) || usedCodes.has(`G${String(n).padStart(2, "0")}`)) {
    n += 1;
  }
  return `G${String(n).padStart(2, "0")}`;
}

function setSelectedSpotIds(ids, primaryId = null) {
  const spots = state.siteMap.draftSpots || [];
  const validSet = new Set(spots.map((s) => s.spot_id));
  const deduped = [];
  const seen = new Set();
  (ids || []).forEach((id) => {
    if (id && validSet.has(id) && !seen.has(id)) {
      seen.add(id);
      deduped.push(id);
    }
  });
  state.siteMap.selectedSpotIds = deduped;
  const nextPrimary =
    primaryId && seen.has(primaryId) ? primaryId : deduped[0] || null;
  state.siteMap.selectedSpotId = nextPrimary;
  if (nextPrimary) {
    const primarySpot = spots.find((s) => s.spot_id === nextPrimary);
    if (primarySpot) {
      state.siteMap.selectedGroupCamId = getSpotGroupKey(primarySpot);
    }
  } else {
    state.siteMap.selectedGroupCamId = null;
  }
}

function finishCurrentSpotGroup({ armNextPlace = false } = {}) {
  state.siteMap.selectedSpotIds = [];
  state.siteMap.selectedSpotId = null;
  state.siteMap.selectedGroupCamId = null;
  if (armNextPlace) {
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
    setDesignatedPlaceMode("spots");
  } else {
    if (state.siteMap.placeMode === "spots") {
      setDesignatedPlaceMode(null);
    }
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  }
}

function getSelectedSpots() {
  const spots = state.siteMap.draftSpots || [];
  if (!spots.length) return [];
  const selIds = state.siteMap.selectedSpotIds || [];
  if (selIds.length > 0) {
    const idSet = new Set(selIds);
    const matched = spots.filter((s) => idSet.has(s.spot_id));
    if (matched.length > 0) return matched;
  }
  if (state.siteMap.selectedSpotId) {
    const single = spots.find((s) => s.spot_id === state.siteMap.selectedSpotId);
    if (single) {
      state.siteMap.selectedSpotIds = [single.spot_id];
      return [single];
    }
  }
  return [];
}

function isMultiScaleSpacingChecked() {
  const el = document.getElementById("chkMultiScaleSpacing");
  return el ? Boolean(el.checked) : true;
}

function getSpotTransformTargets(forceGroup = false) {
  const selSpots = getSelectedSpots();
  if (selSpots.length >= 2 && !forceGroup) {
    return selSpots;
  }
  const groupChecked = Boolean(document.getElementById("chkSpotGroupTransform")?.checked);
  if (forceGroup || groupChecked) {
    const g = getActiveGroupSpots();
    return g.length ? g : selSpots;
  }
  return selSpots.length ? selSpots : getActiveGroupSpots();
}

function getSpotWorldCorners(sp, metrics) {
  const sx = metrics.px(sp.map_x ?? 50);
  const sy = metrics.py(sp.map_y ?? 50);
  const hw = Math.max(0.42, Number(sp.map_w || 1.75) / 2);
  const hh = Math.max(0.42, Number(sp.map_h || 3.25) / 2);
  const rad = (Number(sp.map_angle || 0) * Math.PI) / 180;
  const cosA = Math.cos(rad);
  const sinA = Math.sin(rad);
  return [
    [sx + (-hw * cosA - -hh * sinA), sy + (-hw * sinA + -hh * cosA)],
    [sx + (hw * cosA - -hh * sinA), sy + (hw * sinA + -hh * cosA)],
    [sx + (hw * cosA - hh * sinA), sy + (hw * sinA + hh * cosA)],
    [sx + (-hw * cosA - hh * sinA), sy + (-hw * sinA + hh * cosA)],
  ];
}

function spotIntersectsWorldBox(sp, minWx, minWy, maxWx, maxWy, metrics) {
  if (!metrics) return false;
  const corners = getSpotWorldCorners(sp, metrics);
  let sMinX = Infinity;
  let sMaxX = -Infinity;
  let sMinY = Infinity;
  let sMaxY = -Infinity;
  for (const [cx, cy] of corners) {
    if (cx < sMinX) sMinX = cx;
    if (cx > sMaxX) sMaxX = cx;
    if (cy < sMinY) sMinY = cy;
    if (cy > sMaxY) sMaxY = cy;
  }
  if (sMaxX < minWx || sMinX > maxWx || sMaxY < minWy || sMinY > maxWy) {
    return false;
  }
  // Check Separating Axis Theorem along stall's two local axes
  const boxCorners = [
    [minWx, minWy],
    [maxWx, minWy],
    [maxWx, maxWy],
    [minWx, maxWy],
  ];
  const rad = (Number(sp.map_angle || 0) * Math.PI) / 180;
  const axes = [
    [Math.cos(rad), Math.sin(rad)],
    [-Math.sin(rad), Math.cos(rad)],
  ];
  for (const [ax, ay] of axes) {
    let pMinA = Infinity;
    let pMaxA = -Infinity;
    for (const [cx, cy] of corners) {
      const p = cx * ax + cy * ay;
      if (p < pMinA) pMinA = p;
      if (p > pMaxA) pMaxA = p;
    }
    let pMinB = Infinity;
    let pMaxB = -Infinity;
    for (const [bx, by] of boxCorners) {
      const p = bx * ax + by * ay;
      if (p < pMinB) pMinB = p;
      if (p > pMaxB) pMaxB = p;
    }
    if (pMaxA < pMinB || pMinA > pMaxB) return false;
  }
  return true;
}

function computeMultiSelectBoxGeometry(selectedSpots, metrics, invScale = 1) {
  if (!selectedSpots?.length || !metrics) return null;
  let rawMinX = Infinity;
  let rawMaxX = -Infinity;
  let rawMinY = Infinity;
  let rawMaxY = -Infinity;
  selectedSpots.forEach((sp) => {
    const corners = getSpotWorldCorners(sp, metrics);
    corners.forEach(([wx, wy]) => {
      if (wx < rawMinX) rawMinX = wx;
      if (wx > rawMaxX) rawMaxX = wx;
      if (wy < rawMinY) rawMinY = wy;
      if (wy > rawMaxY) rawMaxY = wy;
    });
  });
  const pad = 0.58 * invScale;
  const minX = rawMinX - pad;
  const maxX = rawMaxX + pad;
  const minY = rawMinY - pad;
  const maxY = rawMaxY + pad;
  const bw = Math.max(2.0, maxX - minX);
  const bh = Math.max(2.0, maxY - minY);
  const cxWorld = (minX + maxX) / 2;
  const cyWorld = (minY + maxY) / 2;
  return {
    minX,
    maxX,
    minY,
    maxY,
    bw,
    bh,
    boxW: bw,
    boxH: bh,
    cxWorld,
    cyWorld,
  };
}

function getActiveGroupSpots() {
  const spots = state.siteMap.draftSpots || [];
  if (!spots.length) return [];
  const selSpot = spots.find((s) => s.spot_id === state.siteMap.selectedSpotId);
  const gKey =
    (selSpot && getSpotGroupKey(selSpot)) || state.siteMap.selectedGroupCamId || "";
  if (!gKey) return [];
  return spots.filter((s) => getSpotGroupKey(s) === gKey);
}

function setDesignatedPlaceMode(mode) {
  const nextMode = mode === "spots" || mode === "routes" || mode === "roads" ? mode : null;
  state.siteMap.placeMode = nextMode;
  if (nextMode) {
    state.siteMap.drawingRoad = false;
    state.siteMap.newRoadPts = [];
    state.siteMap.drawingRoute = false;
    state.siteMap.newRoutePts = [];
    state.siteMap.drawingWalkway = false;
    state.siteMap.newWalkwayPts = [];
    state.siteMap.walkwaySnapTarget = null;
    document.getElementById("btnMapFinishRoad")?.classList.add("hidden");
    document.getElementById("btnMapFinishRoute")?.classList.add("hidden");
    document.getElementById("btnMapFinishWalkway")?.classList.add("hidden");
  } else {
    updateSpotPlacePreviewDom(null);
  }
  document.getElementById("siteMapFrame")?.classList.toggle("placing-spots", Boolean(nextMode));
  document.getElementById("siteMapRefFrame")?.classList.toggle("placing-spots", Boolean(nextMode));

  const spotPill = document.getElementById("btnToggleSpotPlaceMode");
  if (spotPill) {
    const isSpotPlacing = nextMode === "spots";
    spotPill.classList.toggle("active", isSpotPlacing);
    spotPill.textContent = isSpotPlacing ? "✓ 点击/拉线落位中(Esc)" : "🎯 点图/拉线摆位";
  }

  const routeBtn = document.getElementById("btnPickRoutePlaceOnMap");
  if (routeBtn) {
    const isRoutePlacing = nextMode === "routes" || nextMode === "roads";
    routeBtn.classList.toggle("is-placing", isRoutePlacing);
    routeBtn.textContent = isRoutePlacing
      ? "✓ 正在沿路指定位置 · 单击落位或按住拉线 (Esc退出)"
      : "🎯 沿路在图上指定位置摆放";
  }

  const routeHint = document.getElementById("routePlaceStatusHint");
  if (routeHint) {
    const isRoutePlacing = nextMode === "routes" || nextMode === "roads";
    routeHint.classList.toggle("active", isRoutePlacing);
    routeHint.textContent = isRoutePlacing
      ? "已开启沿路指哪放哪：移动鼠标自动贴合最近车行道切线 → 单击落位，或按住首尾拖拉成排（按 R 换向，Esc 退出）。"
      : "单击图上位置或按住拉线，自动沿选中车行道切线批量排布车位。";
  }
}

function getSpotModelPreset(sp) {
  const key =
    sp?.vehicle_model ||
    (Number(sp?.map_w || 1.75) > Number(sp?.map_h || 3.25) * 1.2
      ? "parallel"
      : sp?.compact_only
      ? "compact"
      : "sedan");
  return VEHICLE_MODELS_CLIENT[key] || VEHICLE_MODELS_CLIENT.sedan;
}

function syncSpotTransformSlidersOnly(selSpot) {
  const selSpots = getSelectedSpots();
  const primary = selSpot || selSpots[0] || null;
  const multiCount = selSpots.length;
  const angleSlider = document.getElementById("mapSpotAngleSlider");
  const angleVal = document.getElementById("mapSpotAngleValue");
  const scaleSlider = document.getElementById("mapSpotScaleSlider");
  const scaleVal = document.getElementById("mapSpotScaleValue");

  if (angleSlider) {
    const deg = primary ? Math.round(normalizeDeg(primary.map_angle || 0)) : 0;
    angleSlider.value = String(deg);
    angleSlider.disabled = !primary;
    if (angleVal) {
      angleVal.textContent = primary
        ? multiCount >= 2
          ? `${deg}° (${multiCount}辆)`
          : `${deg}°`
        : "未选中";
    }
  }

  if (scaleSlider) {
    if (primary) {
      const preset = getSpotModelPreset(primary);
      const baseDiag = Math.max(0.5, Math.hypot(preset.w, preset.h));
      const curW = Number(primary.map_w || preset.w);
      const curH = Number(primary.map_h || preset.h);
      const curDiag = Math.hypot(curW, curH);
      const pct = Math.min(240, Math.max(40, Math.round((curDiag / baseDiag) * 100)));
      scaleSlider.value = String(pct);
      scaleSlider.disabled = false;
      if (scaleVal) {
        scaleVal.textContent =
          multiCount >= 2
            ? `${pct}% · 共${multiCount}辆 (${curW.toFixed(1)}×${curH.toFixed(1)}m)`
            : `${pct}% · ${curW.toFixed(1)}×${curH.toFixed(1)}m`;
      }
    } else {
      scaleSlider.value = "100";
      scaleSlider.disabled = true;
      if (scaleVal) scaleVal.textContent = "100%";
    }
  }
}

function setSiteMapSubTool(tool) {
  state.siteMap.subTool = tool;
  state.siteMap.drawingRoad = false;
  state.siteMap.newRoadPts = [];
  state.siteMap.drawingRoute = false;
  state.siteMap.newRoutePts = [];
  state.siteMap.drawingWalkway = false;
  state.siteMap.newWalkwayPts = [];
  state.siteMap.walkwaySnapTarget = null;
  if (state.siteMap.placeMode && state.siteMap.placeMode !== tool) {
    setDesignatedPlaceMode(null);
  }
  document.querySelectorAll(".map-subtool-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mapTool === tool);
  });
  document.getElementById("mapInspectorSpots")?.classList.toggle("hidden", tool !== "spots");
  document.getElementById("mapInspectorRoutes")?.classList.toggle("hidden", tool !== "routes");
  document.getElementById("mapInspectorRoads")?.classList.toggle("hidden", tool !== "roads");
  document.getElementById("mapInspectorWalkways")?.classList.toggle("hidden", tool !== "walkways");
  document.getElementById("mapInspectorNodes")?.classList.toggle("hidden", tool !== "nodes");
  document.getElementById("btnMapFinishRoad")?.classList.add("hidden");
  document.getElementById("btnMapFinishRoute")?.classList.add("hidden");
  document.getElementById("btnMapFinishWalkway")?.classList.add("hidden");

  const hintEl = document.getElementById("mapStudioHint");
  if (hintEl) {
    if (tool === "spots") {
      hintEl.textContent =
        "在画布空白处按住拖框（或 Shift/⌘+点击）可多选车位同步缩放/旋转；按住空格键可平移画布，也可直接从上方待关联机位拖放到图上";
    } else if (tool === "roads") {
      hintEl.textContent =
        "2F 车行道（合一路幅 + 行车方向箭头 + 沿路布位）：点击道路可改单/双向、反转流向或拖拽『＋』加弯，人车均可通行";
    } else if (tool === "walkways") {
      hintEl.textContent =
        "2F 内部人行步道：仅行人通行，绘制时自动磁吸外围 2F 车行道与 🚪 单元门口，保存后按 2F 真实路网最短折线精准播报步行米数";
    } else if (tool === "nodes") {
      hintEl.textContent =
        "直接拖拽楼栋铭牌或 🚪 单元门图标到真实位置，保存后系统自动沿 2F 车行道 + 人行步道路网计算最短步行路径";
    } else {
      hintEl.textContent =
        "滚轮或右上角 ＋/− 最高支持 550% 矢量放大，按住画布任意位置拖动平移（在其他工具下也可按住空格键平移）";
    }
  }
  syncSiteMapStudioControls();
  if (state.overview) renderAbstractSvgMap(state.overview);
}

function syncSiteMapStudioControls() {
  if (!state.siteMap.editMode) return;
  updateSiteMapUndoButtons();

  // Populate camera/group selector & sync multi-selection UI
  const grpSelect = document.getElementById("mapGroupSelect");
  const spots = state.siteMap.draftSpots || [];
  const selSpots = getSelectedSpots();
  const selSpot =
    spots.find((s) => s.spot_id === state.siteMap.selectedSpotId) || selSpots[0] || null;

  const marqueeBtn = document.getElementById("btnToggleMarqueeMode");
  if (marqueeBtn) {
    marqueeBtn.classList.toggle("active", state.siteMap.marqueeMode !== false);
  }

  const multiBadge = document.getElementById("multiSelectCountBadge");
  const multiBar = multiBadge?.closest(".multi-select-bar");
  if (multiBadge) {
    if (selSpots.length >= 2) {
      multiBadge.textContent = `✓ 已联合选中 ${selSpots.length} 辆车 · 拖多选框四角或滑杆同步缩放`;
      multiBadge.classList.add("is-multi");
      multiBar?.classList.add("has-multi");
    } else if (selSpots.length === 1) {
      multiBadge.textContent = `当前选中 1 辆 (${selSpots[0].label || "01"}) · 空白处拖框或 Shift 点选多车`;
      multiBadge.classList.remove("is-multi");
      multiBar?.classList.remove("has-multi");
    } else {
      multiBadge.textContent = "未选中 · 空白处拖框或 Shift 点选多辆车一起缩放";
      multiBadge.classList.remove("is-multi");
      multiBar?.classList.remove("has-multi");
    }
  }

  const workflowBar = document.getElementById("activeGroupWorkflowBar");
  const workflowBadge = document.getElementById("nextGroupHintBadge");
  const finishGrpBtn = document.getElementById("btnFinishCurrentGroup");
  const nextAutoCode = allocateNextGroupCode(spots);
  if (workflowBadge && finishGrpBtn) {
    if (selSpots.length > 0) {
      const gKey = selSpot ? getSpotGroupKey(selSpot) : "";
      workflowBar?.classList.add("has-active-group");
      workflowBadge.textContent = `当前调整：${gKey || "本组"}（${selSpots.length}辆）· 完成后互不粘连`;
      finishGrpBtn.textContent = "✓ 完成本组 · 再加一组";
    } else {
      workflowBar?.classList.remove("has-active-group");
      workflowBadge.textContent = state.siteMap.placeMode === "spots"
        ? `🎯 正在摆放新组 ${nextAutoCode}：拖拉线段或拖入车型`
        : `✓ 上组已独立锁定 · 下次拖入自动建新组 (${nextAutoCode})`;
      finishGrpBtn.textContent = state.siteMap.placeMode === "spots"
        ? "✓ 退出连续摆组"
        : `＋ 开始摆新组 (${nextAutoCode})`;
    }
  }

  // Sync vehicle count pills with state.siteMap.spotPlaceCountMode & #spotPlaceCountInput
  const curPlaceCount = Math.max(
    1,
    Math.min(40, Number(document.getElementById("spotPlaceCountInput")?.value || 1))
  );
  const isAutoCount = (state.siteMap.spotPlaceCountMode || "auto") === "auto";
  document.querySelectorAll("#spotCountPills .spot-count-pill").forEach((pill) => {
    if (pill.dataset.countMode === "auto") {
      pill.classList.toggle("active", isAutoCount);
    } else {
      const pVal = Number(pill.dataset.countVal || 0);
      pill.classList.toggle("active", !isAutoCount && pVal === curPlaceCount);
    }
  });

  const delBtn = document.getElementById("btnMapDeleteSpot");
  if (delBtn) {
    delBtn.textContent =
      selSpots.length >= 2 ? `删除已选 ${selSpots.length} 辆车位` : "删除选中车位";
  }

  if (grpSelect) {
    const groups = new Map();
    spots.forEach((sp) => {
      const k = getSpotGroupKey(sp);
      if (!groups.has(k)) {
        const camObj = (state.overview?.cameras || []).find(
          (c) => c.camera_id === sp.camera_id || c.camera_id === k
        );
        const parsed = sp.group_code ? parseSingleGroupTokenClient(sp.group_code) : null;
        const camStatus =
          !sp.camera_id || sp.camera_id === "UNASSIGNED"
            ? "⏳待接相机"
            : camObj
            ? `📷${camObj.name}`
            : sp.camera_id;
        const title = parsed
          ? `[${parsed.code}] ${parsed.short_cn} · ${camStatus}`
          : sp.group_code
          ? `[${sp.group_code}] 独立车位组 · ${camStatus}`
          : camObj
          ? `${camObj.name} (${k})`
          : `${sp.zone_name || k} (${camStatus})`;
        groups.set(k, { key: k, title, count: 0 });
      }
      groups.get(k).count += 1;
    });

    const activeKey =
      (selSpot && getSpotGroupKey(selSpot)) ||
      state.siteMap.selectedGroupCamId ||
      "";
    state.siteMap.selectedGroupCamId = activeKey || null;

    const noneOpt = `<option value="" ${
      !activeKey ? "selected" : ""
    }>— 未选旧组（新拖放将新建独立组 ${escapeHtml(nextAutoCode)}） —</option>`;
    grpSelect.innerHTML =
      noneOpt +
      Array.from(groups.values())
        .map(
          (g) =>
            `<option value="${escapeHtml(g.key)}" ${
              g.key === activeKey ? "selected" : ""
            }>${escapeHtml(g.title)} · ${g.count}位</option>`
        )
        .join("");
  }

  const activeGrpSpots = getActiveGroupSpots();
  const unassignedDraftSpots = spots.filter(
    (s) => !s.camera_id || s.camera_id === "UNASSIGNED"
  );
  const fallbackUnassignedSample = unassignedDraftSpots.length
    ? unassignedDraftSpots[unassignedDraftSpots.length - 1]
    : null;
  const grpSample = selSpot || activeGrpSpots[0] || fallbackUnassignedSample || null;

  const lblInput = document.getElementById("mapSpotLabelInput");
  if (lblInput) {
    lblInput.value = selSpot ? selSpot.label || "" : "";
    lblInput.disabled = !selSpot;
  }

  const gCodeInput = document.getElementById("mapSpotGroupCodeInput");
  if (gCodeInput) {
    gCodeInput.value = grpSample ? grpSample.group_code || "" : "";
    gCodeInput.disabled = false;
  }

  const gNameInput = document.getElementById("mapSpotGroupNameInput");
  if (gNameInput) {
    gNameInput.value = grpSample ? grpSample.group_name || "" : "";
    gNameInput.disabled = false;
  }

  const nearUnitSel = document.getElementById("mapSpotNearUnitSelect");
  if (nearUnitSel) {
    nearUnitSel.disabled = false;
    const nuid = grpSample ? grpSample.near_unit_id || "" : "";
    nearUnitSel.value = [...nearUnitSel.options].some((o) => o.value === nuid) ? nuid : "";
  }

  const camBindSel = document.getElementById("mapSpotCameraBindSelect");
  if (camBindSel) {
    camBindSel.disabled = false;
    if (grpSample) {
      const cid = grpSample.camera_id || "UNASSIGNED";
      if ([...camBindSel.options].some((o) => o.value === cid)) {
        camBindSel.value = cid;
      } else {
        camBindSel.value = "UNASSIGNED";
      }
    }
  }

  // Highlight active vehicle model chip & sync transform sliders
  const activeModel = selSpot
    ? getSpotModelPreset(selSpot).key
    : state.siteMap.paletteModel || "sedan";
  state.siteMap.paletteModel = activeModel;
  document.querySelectorAll(".vmodel-chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.vmodel === activeModel);
  });
  syncSpotTransformSlidersOnly(selSpot);

  // Driving Route Planner inspector sync
  const routes = state.siteMap.draftRoutes || [];
  if (state.siteMap.selectedRouteIdx >= routes.length) {
    state.siteMap.selectedRouteIdx = routes.length ? routes.length - 1 : -1;
  }
  if (state.siteMap.selectedRouteIdx < 0 && routes.length > 0) {
    state.siteMap.selectedRouteIdx = 0;
  }
  const selRoute = routes[state.siteMap.selectedRouteIdx] || null;
  const rtSelect = document.getElementById("mapRouteSelect");
  if (rtSelect) {
    rtSelect.innerHTML = routes.length
      ? routes
          .map(
            (rt, idx) =>
              `<option value="${idx}" ${
                idx === state.siteMap.selectedRouteIdx ? "selected" : ""
              }>${escapeHtml(rt.name || `路线#${idx + 1}`)} (${
                rt.one_way !== false ? "单向➤" : "双向⇄"
              } · ${(rt.points || []).length}点)</option>`
          )
          .join("")
      : `<option value="-1">暂无路线（点击左侧绘制）</option>`;
  }
  const rtNameInput = document.getElementById("mapRouteNameInput");
  if (rtNameInput) {
    rtNameInput.value = selRoute ? selRoute.name || "" : "";
    rtNameInput.disabled = !selRoute;
  }
  const rtOneWaySel = document.getElementById("mapRouteOneWaySelect");
  if (rtOneWaySel && selRoute) {
    rtOneWaySel.value = selRoute.one_way === false ? "0" : "1";
  }

  // Unified 2F Road + Direction inspector sync
  const roads = state.siteMap.draftRoads || [];
  if (state.siteMap.selectedRoadIdx >= roads.length) {
    state.siteMap.selectedRoadIdx = roads.length ? roads.length - 1 : -1;
  }
  if (state.siteMap.selectedRoadIdx < 0 && roads.length > 0) {
    state.siteMap.selectedRoadIdx = 0;
  }
  const selRoad = roads[state.siteMap.selectedRoadIdx] || null;
  const rSelect = document.getElementById("mapRoadSelect");
  if (rSelect) {
    rSelect.innerHTML = roads.length
      ? roads
          .map(
            (r, idx) =>
              `<option value="${idx}" ${
                idx === state.siteMap.selectedRoadIdx ? "selected" : ""
              }>${escapeHtml(r.name || `车行道#${idx + 1}`)} (${
                r.one_way === false ? "双向⇄" : "单向➤"
              } · ${(r.points || []).length}点)</option>`
          )
          .join("")
      : `<option value="-1">暂无车行道（点击上方绘制）</option>`;
  }
  const rNameInput = document.getElementById("mapRoadNameInput");
  const rOneWaySel = document.getElementById("mapRoadOneWaySelect");
  const rWidthSlider = document.getElementById("mapRoadWidthSlider");
  const rWidthVal = document.getElementById("mapRoadWidthVal");
  if (rNameInput) {
    rNameInput.value = selRoad ? selRoad.name || "" : "";
    rNameInput.disabled = !selRoad;
  }
  if (rOneWaySel) {
    rOneWaySel.disabled = !selRoad;
    if (selRoad) {
      rOneWaySel.value = selRoad.one_way === false ? "0" : "1";
    }
  }
  if (rWidthSlider) {
    rWidthSlider.disabled = !selRoad;
    if (selRoad) {
      const rw = Number(selRoad.width || 4.4);
      rWidthSlider.value = String(Math.round(rw * 10));
      if (rWidthVal) rWidthVal.textContent = rw.toFixed(1);
    }
  }

  // 2F Pedestrian Walkway & Scale Calibration inspector sync
  const walkways = state.siteMap.draftWalkways || [];
  if (state.siteMap.selectedWalkwayIdx >= walkways.length) {
    state.siteMap.selectedWalkwayIdx = walkways.length ? walkways.length - 1 : -1;
  }
  if (state.siteMap.selectedWalkwayIdx < 0 && walkways.length > 0) {
    state.siteMap.selectedWalkwayIdx = 0;
  }
  const selWalkway = walkways[state.siteMap.selectedWalkwayIdx] || null;
  const wkSelect = document.getElementById("mapWalkwaySelect");
  if (wkSelect) {
    wkSelect.innerHTML = walkways.length
      ? walkways
          .map(
            (wk, idx) =>
              `<option value="${idx}" ${
                idx === state.siteMap.selectedWalkwayIdx ? "selected" : ""
              }>${escapeHtml(wk.name || `人行步道#${idx + 1}`)} (${
                (wk.points || []).length
              }点)</option>`
          )
          .join("")
      : `<option value="-1">暂无内部步道（点击上方绘制）</option>`;
  }
  const wkNameInput = document.getElementById("mapWalkwayNameInput");
  if (wkNameInput) {
    wkNameInput.value = selWalkway ? selWalkway.name || "" : "";
    wkNameInput.disabled = !selWalkway;
  }
  const spanM = Math.round(
    Math.max(40, Math.min(800, Number(state.siteMap.draftSpanMeters || 185)))
  );
  const spanInput = document.getElementById("mapSpanMetersInput");
  const spanSlider = document.getElementById("mapSpanMetersSlider");
  if (spanInput && document.activeElement !== spanInput) {
    spanInput.value = String(spanM);
  }
  if (spanSlider) {
    spanSlider.value = String(Math.max(60, Math.min(500, spanM)));
  }

  // Node inspector sync
  const nodeNameInput = document.getElementById("mapNodeNameInput");
  const doorBox = document.getElementById("mapUnitDoorControls");
  const doorStatusEl = document.getElementById("mapUnitDoorStatusText");
  if (nodeNameInput) {
    let val = "";
    if (state.siteMap.selectedNodeKind === "unit") {
      const u = (state.siteMap.draftUnits || []).find(
        (x) => x.unit_id === state.siteMap.selectedNodeId
      );
      val = u ? u.unit_id : "";
      if (doorBox) doorBox.classList.toggle("hidden", !u);
      if (doorStatusEl && u) {
        const dx = Number(u.door_x ?? u.map_x ?? 50).toFixed(1);
        const dy = Number(u.door_y ?? u.map_y ?? 50).toFixed(1);
        doorStatusEl.textContent = `${u.name || u.unit_id}：门位 (${dx}, ${dy}) · ${
          u.door_custom ? "🎯 已手动定位（可在图上继续拖动 🚪 图标）" : "🧭 自动朝道路投影（可直接拖动图上 🚪 图标精调）"
        }`;
      }
    } else if (state.siteMap.selectedNodeKind === "gate") {
      const g = (state.siteMap.draftGates || []).find(
        (x) => x.gate_id === state.siteMap.selectedNodeId
      );
      val = g ? g.name || "" : "";
      if (doorBox) doorBox.classList.add("hidden");
    } else {
      if (doorBox) doorBox.classList.add("hidden");
    }
    nodeNameInput.value = val;
    nodeNameInput.disabled = !state.siteMap.selectedNodeKind;
  }
}

function layoutGroupSpotsLinear(groupSpots, styleMode = null) {
  if (!groupSpots.length || !state.overview) return;
  const metrics = getSiteMapMetrics(state.overview.site_map);
  const pts = groupSpots.map((s) => [metrics.px(s.map_x), metrics.py(s.map_y)]);
  const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
  const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;

  let lineAngle = Number(groupSpots[0].map_angle || 0);
  if (pts.length >= 2) {
    const dx = pts[pts.length - 1][0] - pts[0][0];
    const dy = pts[pts.length - 1][1] - pts[0][1];
    if (Math.hypot(dx, dy) > 0.8) {
      lineAngle = (Math.atan2(dy, dx) * 180) / Math.PI;
    }
  }
  const rad = (lineAngle * Math.PI) / 180;
  const ux = Math.cos(rad);
  const uy = Math.sin(rad);

  groupSpots.forEach((sp) => {
    if (sp.spot_type === "elastic_strip") return;
    if (styleMode === "perp") {
      sp.map_w = 1.75;
      sp.map_h = 3.25;
    } else if (styleMode === "parallel") {
      sp.map_w = 3.30;
      sp.map_h = 1.70;
    }
  });

  const gap = styleMode === "parallel" ? 0.34 : 0.28;
  const widths = groupSpots.map((sp) => Number(sp.map_w || 1.75));
  const totalSpan =
    widths.reduce((a, b) => a + b, 0) + Math.max(0, groupSpots.length - 1) * gap;
  let cursor = -totalSpan / 2;

  groupSpots.forEach((sp, i) => {
    const w = widths[i];
    const offset = cursor + w / 2;
    cursor += w + gap;
    const sx = Math.min(metrics.worldW - 1.2, Math.max(1.2, cx + ux * offset));
    const sy = Math.min(metrics.worldH - 1.2, Math.max(1.2, cy + uy * offset));
    sp.map_x = metrics.nx(sx);
    sp.map_y = metrics.ny(sy);
    sp.map_angle = normalizeDeg(lineAngle);
    sp.map_custom = 1;
  });
}

function findNearestRouteOrRoadTangent(wx, wy, metrics) {
  const topo = state.overview?.site_map?.topology || {};
  const routes =
    state.siteMap.editMode && state.siteMap.draftRoutes?.length
      ? state.siteMap.draftRoutes
      : topo.routes || [];
  const roads =
    state.siteMap.editMode && state.siteMap.draftRoads?.length
      ? state.siteMap.draftRoads
      : topo.roads || [];
  const candidates = [
    ...routes.map((rt) => ({ points: rt.points || [], width: 3.6, isRoute: true })),
    ...roads.map((rd) => ({ points: rd.points || [], width: Number(rd.width || 4.4), isRoute: false })),
  ];
  let best = null;
  candidates.forEach((c) => {
    const pts = (c.points || []).map(([rx, ry]) => [metrics.px(rx), metrics.py(ry)]);
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i];
      const [bx, by] = pts[i + 1];
      const vx = bx - ax;
      const vy = by - ay;
      const len2 = vx * vx + vy * vy;
      if (len2 < 0.25) continue;
      const t = Math.min(1, Math.max(0, ((wx - ax) * vx + (wy - ay) * vy) / len2));
      const projX = ax + vx * t;
      const projY = ay + vy * t;
      const dist = Math.hypot(wx - projX, wy - projY);
      // Slight preference for planned driving routes when close
      const score = c.isRoute ? dist * 0.82 : dist;
      if (!best || score < best.score) {
        best = {
          score,
          dist,
          projX,
          projY,
          vx,
          vy,
          segLen: Math.sqrt(len2),
          roadWidth: c.width,
          angleDeg: (Math.atan2(vy, vx) * 180) / Math.PI,
        };
      }
    }
  });
  return best;
}

function snapActiveGroupToNearestRoad() {
  const groupSpots = getActiveGroupSpots();
  const hasPaths =
    (state.siteMap.draftRoutes && state.siteMap.draftRoutes.length) ||
    (state.siteMap.draftRoads && state.siteMap.draftRoads.length);
  if (!groupSpots.length || !hasPaths || !state.overview) return;

  const metrics = getSiteMapMetrics(state.overview.site_map);
  const pts = groupSpots.map((s) => [metrics.px(s.map_x), metrics.py(s.map_y)]);
  const gcx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
  const gcy = pts.reduce((a, p) => a + p[1], 0) / pts.length;

  const best = findNearestRouteOrRoadTangent(gcx, gcy, metrics);
  if (!best) return;

  pushSiteMapUndo();
  let ux = best.vx / best.segLen;
  let uy = best.vy / best.segLen;
  if (pts.length >= 2) {
    const curDx = pts[pts.length - 1][0] - pts[0][0];
    const curDy = pts[pts.length - 1][1] - pts[0][1];
    if (curDx * ux + curDy * uy < 0) {
      ux = -ux;
      uy = -uy;
    }
  }
  // Normal vector pointing from road centerline toward current group side
  let nxVec = -uy;
  let nyVec = ux;
  if ((gcx - best.projX) * nxVec + (gcy - best.projY) * nyVec < 0) {
    nxVec = -nxVec;
    nyVec = -nyVec;
  }

  const avgH =
    groupSpots.reduce((a, s) => a + Number(s.map_h || 3.25), 0) / groupSpots.length;
  const shoulderOffset = best.roadWidth * 0.48 + avgH * 0.52 + 0.25;
  const anchorCx = best.projX + nxVec * shoulderOffset;
  const anchorCy = best.projY + nyVec * shoulderOffset;
  const lineAngle = (Math.atan2(uy, ux) * 180) / Math.PI;

  const gap = 0.28;
  const widths = groupSpots.map((sp) => Number(sp.map_w || 1.75));
  const totalSpan =
    widths.reduce((a, b) => a + b, 0) + Math.max(0, groupSpots.length - 1) * gap;
  let cursor = -totalSpan / 2;

  groupSpots.forEach((sp, i) => {
    const w = widths[i];
    const offset = cursor + w / 2;
    cursor += w + gap;
    const sx = Math.min(metrics.worldW - 1.2, Math.max(1.2, anchorCx + ux * offset));
    const sy = Math.min(metrics.worldH - 1.2, Math.max(1.2, anchorCy + uy * offset));
    sp.map_x = metrics.nx(sx);
    sp.map_y = metrics.ny(sy);
    sp.map_angle = normalizeDeg(lineAngle);
    sp.map_custom = 1;
  });

  if (state.overview) renderAbstractSvgMap(state.overview);
}

function bindSiteMapControls() {
  const fileInput = document.getElementById("siteMapFileInput");
  const btnUpload = document.getElementById("btnUploadSiteMap");
  const btnToggleBg = document.getElementById("btnToggleMapBackdrop");
  const btnEdit = document.getElementById("btnToggleEditMap");
  const btnSave = document.getElementById("btnSaveMapLayout");
  const btnCancel = document.getElementById("btnCancelMapLayout");
  const svg = document.getElementById("communitySvg");
  const refSvg = document.getElementById("communityRefSvg");
  const hoverBadge = document.getElementById("mapHoverBadge");

  // Collapsible Left Voice Guidance Rail
  document.getElementById("btnCollapseDriverPanel")?.addEventListener("click", () => {
    setDriverPanelCollapsed(true, false);
  });
  document.getElementById("btnExpandDriverPanel")?.addEventListener("click", () => {
    setDriverPanelCollapsed(false, false);
  });

  // Left-Right Synchronized Comparison View & Docked Right Inspector Drawer
  document.getElementById("btnToggleSplitCompare")?.addEventListener("click", () => {
    setCompareSplitMode(!state.siteMap.compareSplit, true);
  });
  document.querySelectorAll(".map-viewmode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      setCompareSplitMode(btn.dataset.compareMode === "split", true);
    });
  });
  document.getElementById("btnToggleInspectorDrawer")?.addEventListener("click", () => {
    state.siteMap.inspectorOpen = !state.siteMap.inspectorOpen;
    syncMapWorkspaceLayout();
  });
  document.getElementById("chkRefWireframe")?.addEventListener("change", (e) => {
    state.siteMap.refWireframe = Boolean(e.target.checked);
    if (state.overview) renderAbstractSvgMap(state.overview);
  });
  document.getElementById("btnRefUploadInline")?.addEventListener("click", () => {
    fileInput.click();
  });

  btnUpload.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      await uploadAndGenerateSiteMap(ev.target.result);
    };
    reader.readAsDataURL(file);
    fileInput.value = "";
  });

  btnToggleBg.addEventListener("click", () => {
    if (!state.siteMap.backdropDataUrl) return;
    state.siteMap.showBackdrop = !state.siteMap.showBackdrop;
    if (state.siteMap.showBackdrop && state.siteMap.backdropAlpha <= 0.02) {
      state.siteMap.backdropAlpha = state.siteMap.compareSplit ? 0.22 : 0.58;
    }
    state.siteMap.userToggledBackdrop = true;
    updateSiteMapHeaderUI(state.overview?.site_map);
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("mapBackdropAlphaSlider")?.addEventListener("input", (e) => {
    const rawPct = Math.min(90, Math.max(0, Number(e.target.value || 0)));
    state.siteMap.userToggledBackdrop = true;
    if (rawPct <= 1) {
      state.siteMap.showBackdrop = false;
      state.siteMap.backdropAlpha = 0;
    } else {
      state.siteMap.showBackdrop = true;
      state.siteMap.backdropAlpha = +(rawPct / 100).toFixed(2);
    }
    const alphaValEl = document.getElementById("mapBackdropAlphaVal");
    if (alphaValEl) alphaValEl.textContent = `${Math.round(rawPct)}%`;
    updateSiteMapHeaderUI(state.overview?.site_map);
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  // Zoom HUD buttons + wheel zoom (synchronized on both main & reference panes)
  document.getElementById("btnMapZoomIn")?.addEventListener("click", () => zoomSiteMapAt(1.32));
  document.getElementById("btnMapZoomOut")?.addEventListener("click", () => zoomSiteMapAt(1 / 1.32));
  document.getElementById("btnMapZoomReset")?.addEventListener("click", () => resetSiteMapZoom());

  svg.addEventListener(
    "wheel",
    (evt) => {
      if (!state.overview) return;
      evt.preventDefault();
      const anchor = clientToSiteMapWorld(svg, evt);
      const factor = evt.deltaY < 0 ? 1.16 : 1 / 1.16;
      zoomSiteMapAt(factor, anchor);
    },
    { passive: false }
  );

  const isGroupTransformChecked = () =>
    Boolean(document.getElementById("chkSpotGroupTransform")?.checked);

  const updateMarqueeBoxDom = (rectWorld, hitCount = 0) => {
    const mainG = document.getElementById("mainMarqueeBox");
    const refG = document.getElementById("refMarqueeBox");
    if (!rectWorld || !state.overview) {
      if (mainG) mainG.innerHTML = "";
      if (refG) refG.innerHTML = "";
      return;
    }
    const [minWx, minWy, maxWx, maxWy] = rectWorld;
    const w = Math.max(0.1, maxWx - minWx);
    const h = Math.max(0.1, maxWy - minWy);
    const invScale = 1 / Math.max(1, Math.min(2.6, Math.sqrt(state.siteMap.zoom || 1)));
    const badgeW = 9.2 * invScale;
    const badgeH = 2.0 * invScale;
    const bx = maxWx - badgeW;
    const by = Math.max(0.8, minWy - badgeH - 0.35 * invScale);
    const markup = `
      <rect x="${minWx.toFixed(2)}" y="${minWy.toFixed(2)}" width="${w.toFixed(
      2
    )}" height="${h.toFixed(
      2
    )}" rx="0.35" fill="rgba(0, 113, 227, 0.12)" stroke="#0071E3" stroke-width="${(
      0.34 * invScale
    ).toFixed(2)}" stroke-dasharray="${(0.95 * invScale).toFixed(2)},${(
      0.65 * invScale
    ).toFixed(2)}"/>
      ${
        hitCount > 0
          ? `<g>
              <rect x="${bx.toFixed(2)}" y="${by.toFixed(2)}" width="${badgeW.toFixed(
              2
            )}" height="${badgeH.toFixed(2)}" rx="${(0.8 * invScale).toFixed(
              2
            )}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(0.24 * invScale).toFixed(
              2
            )}"/>
              <text x="${(bx + badgeW / 2).toFixed(2)}" y="${(
              by +
              badgeH * 0.69
            ).toFixed(2)}" font-size="${(1.12 * invScale).toFixed(
              2
            )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">框选 ${hitCount} 辆</text>
            </g>`
          : ""
      }
    `;
    if (mainG) mainG.innerHTML = markup;
    if (refG) refG.innerHTML = markup;
  };

  const startSpotOrHandleDrag = (dragTarget, evt, activeSvg, metrics, mx, my) => {
    const kind = dragTarget.dataset.dragKind;
    if (
      kind !== "spot" &&
      kind !== "spot-group-move" &&
      kind !== "finish-spot-group" &&
      kind !== "multi-spot-move" &&
      kind !== "multi-spot-scale" &&
      kind !== "multi-spot-rotate" &&
      kind !== "spot-rotate" &&
      kind !== "spot-scale" &&
      kind !== "spot-resize" &&
      kind !== "spot-resize-w" &&
      kind !== "spot-resize-h" &&
      kind !== "spot-group-line"
    ) {
      return false;
    }

    evt.preventDefault();
    if (kind === "finish-spot-group") {
      finishCurrentSpotGroup({ armNextPlace: false });
      return true;
    }
    const spots = state.siteMap.draftSpots || [];

    if (kind === "multi-spot-move") {
      const selSpots = getSelectedSpots();
      if (!selSpots.length) return true;
      pushSiteMapUndo();
      state.siteMap.dragging = {
        kind: "spot-move-list",
        activeSvg,
        startX: mx,
        startY: my,
        startClientX: evt.clientX,
        startClientY: evt.clientY,
        movedPx: 0,
        undoPushed: true,
        items: selSpots.map((t) => ({
          ref: t,
          origX: Number(t.map_x),
          origY: Number(t.map_y),
        })),
      };
      return true;
    }

    if (kind === "multi-spot-scale") {
      const selSpots = getSelectedSpots();
      if (selSpots.length < 2) return true;
      const geom = computeMultiSelectBoxGeometry(selSpots, metrics, 1);
      if (!geom) return true;
      const [wx0, wy0] = clientToSiteMapWorld(activeSvg, evt);
      const startDist = Math.max(0.6, Math.hypot(wx0 - geom.cxWorld, wy0 - geom.cyWorld));
      pushSiteMapUndo();
      state.siteMap.dragging = {
        kind: "multi-spot-scale",
        activeSvg,
        cxWorld: geom.cxWorld,
        cyWorld: geom.cyWorld,
        origBaseDist: startDist,
        scaleSpacing: isMultiScaleSpacingChecked(),
        primarySpot: selSpots[0],
        items: selSpots.map((t) => ({
          ref: t,
          origWx: metrics.px(t.map_x),
          origWy: metrics.py(t.map_y),
          origW: Math.max(0.75, Number(t.map_w || 1.75)),
          origH: Math.max(0.75, Number(t.map_h || 3.25)),
        })),
      };
      return true;
    }

    if (kind === "multi-spot-rotate") {
      const selSpots = getSelectedSpots();
      if (selSpots.length < 2) return true;
      const geom = computeMultiSelectBoxGeometry(selSpots, metrics, 1);
      if (!geom) return true;
      const [wx0, wy0] = clientToSiteMapWorld(activeSvg, evt);
      const startPointerDeg =
        (Math.atan2(wy0 - geom.cyWorld, wx0 - geom.cxWorld) * 180) / Math.PI;
      pushSiteMapUndo();
      state.siteMap.dragging = {
        kind: "multi-spot-rotate",
        activeSvg,
        cxWorld: geom.cxWorld,
        cyWorld: geom.cyWorld,
        startPointerDeg,
        revolvePositions: isMultiScaleSpacingChecked(),
        primarySpot: selSpots[0],
        items: selSpots.map((t) => ({
          ref: t,
          origWx: metrics.px(t.map_x),
          origWy: metrics.py(t.map_y),
          origAngle: Number(t.map_angle || 0),
        })),
      };
      return true;
    }

    if (kind === "spot" || kind === "spot-group-move") {
      const spotId = dragTarget.dataset.spotId || dragTarget.dataset.refSpotId;
      const gKey = dragTarget.dataset.groupKey;
      if (state.siteMap.subTool !== "spots") {
        state.siteMap.subTool = "spots";
        document.querySelectorAll(".map-subtool-btn").forEach((btn) => {
          btn.classList.toggle("active", btn.dataset.mapTool === "spots");
        });
        document.getElementById("mapInspectorSpots")?.classList.remove("hidden");
        document.getElementById("mapInspectorRoutes")?.classList.add("hidden");
        document.getElementById("mapInspectorRoads")?.classList.add("hidden");
        document.getElementById("mapInspectorNodes")?.classList.add("hidden");
      }

      if (kind === "spot-group-move") {
        const grpSpots = spots.filter(
          (s) => getSpotGroupKey(s) === (gKey || state.siteMap.selectedGroupCamId)
        );
        if (!grpSpots.length) return true;
        setSelectedSpotIds(
          grpSpots.map((s) => s.spot_id),
          grpSpots[0].spot_id
        );
        syncSiteMapStudioControls();
        pushSiteMapUndo();
        state.siteMap.dragging = {
          kind: "spot-move-list",
          activeSvg,
          startX: mx,
          startY: my,
          startClientX: evt.clientX,
          startClientY: evt.clientY,
          movedPx: 0,
          undoPushed: true,
          items: grpSpots.map((t) => ({
            ref: t,
            origX: Number(t.map_x),
            origY: Number(t.map_y),
          })),
        };
        renderAbstractSvgMap(state.overview);
        return true;
      }

      const sp = spots.find((s) => s.spot_id === spotId);
      if (!sp) return true;
      const curSelIds = getSelectedSpots().map((s) => s.spot_id);
      const isToggleMod = Boolean(evt.shiftKey || evt.metaKey || evt.ctrlKey);
      let narrowToSpotOnClick = null;

      if (isToggleMod) {
        if (curSelIds.includes(sp.spot_id)) {
          setSelectedSpotIds(curSelIds.filter((id) => id !== sp.spot_id));
          syncSiteMapStudioControls();
          renderAbstractSvgMap(state.overview);
          return true;
        }
        setSelectedSpotIds([...curSelIds, sp.spot_id], sp.spot_id);
      } else if (curSelIds.length >= 2 && curSelIds.includes(sp.spot_id)) {
        // Preserve multi-selection on mousedown so dragging moves all selected cars together!
        setSelectedSpotIds(curSelIds, sp.spot_id);
        narrowToSpotOnClick = sp.spot_id;
      } else {
        setSelectedSpotIds([sp.spot_id], sp.spot_id);
      }

      syncSiteMapStudioControls();
      const selNow = getSelectedSpots();
      const targets =
        selNow.length >= 2
          ? selNow
          : isGroupTransformChecked()
          ? getActiveGroupSpots()
          : [sp];
      if (!targets.length) return true;

      state.siteMap.dragging = {
        kind: "spot-move-list",
        activeSvg,
        startX: mx,
        startY: my,
        startClientX: evt.clientX,
        startClientY: evt.clientY,
        movedPx: 0,
        undoPushed: false,
        preMoveSnapshot: snapshotSiteMapDraft(),
        narrowToSpotOnClick,
        items: targets.map((t) => ({ ref: t, origX: Number(t.map_x), origY: Number(t.map_y) })),
      };
      renderAbstractSvgMap(state.overview);
      return true;
    }

    if (kind === "spot-rotate") {
      const sp = spots.find((s) => s.spot_id === dragTarget.dataset.spotId);
      if (!sp) return true;
      const applyGrp = evt.shiftKey || isGroupTransformChecked();
      const grpTargets = applyGrp ? getActiveGroupSpots() : [sp];
      pushSiteMapUndo();
      state.siteMap.dragging = {
        kind: "spot-rotate",
        activeSvg,
        spot: sp,
        origAngle: Number(sp.map_angle || 0),
        cxWorld: metrics.px(sp.map_x),
        cyWorld: metrics.py(sp.map_y),
        groupTargets: grpTargets.map((t) => ({
          ref: t,
          origAngle: Number(t.map_angle || 0),
        })),
      };
      return true;
    }

    if (kind === "spot-scale") {
      const sp = spots.find((s) => s.spot_id === dragTarget.dataset.spotId);
      if (!sp) return true;
      const applyGrp = evt.shiftKey || isGroupTransformChecked();
      const grpTargets = applyGrp ? getActiveGroupSpots() : [sp];
      const list = grpTargets.length ? grpTargets : [sp];
      const cxWorld = metrics.px(sp.map_x);
      const cyWorld = metrics.py(sp.map_y);
      const [wx0, wy0] = clientToSiteMapWorld(activeSvg, evt);
      const startDist = Math.max(
        0.55,
        Math.hypot(wx0 - cxWorld, wy0 - cyWorld) ||
          Math.hypot(Number(sp.map_w || 1.75) / 2, Number(sp.map_h || 3.25) / 2)
      );
      pushSiteMapUndo();
      state.siteMap.dragging = {
        kind: "spot-scale",
        activeSvg,
        spot: sp,
        cxWorld,
        cyWorld,
        origBaseDist: startDist,
        items: list.map((t) => ({
          ref: t,
          origW: Math.max(0.8, Number(t.map_w || 1.75)),
          origH: Math.max(0.8, Number(t.map_h || 3.25)),
        })),
      };
      return true;
    }

    if (kind === "spot-resize" || kind === "spot-resize-w" || kind === "spot-resize-h") {
      const sp = spots.find((s) => s.spot_id === dragTarget.dataset.spotId);
      if (!sp) return true;
      pushSiteMapUndo();
      state.siteMap.dragging = {
        kind,
        activeSvg,
        spot: sp,
        cxWorld: metrics.px(sp.map_x),
        cyWorld: metrics.py(sp.map_y),
      };
      return true;
    }

    if (kind === "spot-group-line") {
      const gKey = dragTarget.dataset.groupKey || state.siteMap.selectedGroupCamId;
      const groupSpots = spots.filter((s) => getSpotGroupKey(s) === gKey);
      if (groupSpots.length < 2) return true;
      pushSiteMapUndo();
      state.siteMap.dragging = {
        kind: "spot-group-line",
        activeSvg,
        groupSpots,
        anchorWorldX: metrics.px(groupSpots[0].map_x),
        anchorWorldY: metrics.py(groupSpots[0].map_y),
      };
      return true;
    }

    return false;
  };

  if (refSvg) {
    refSvg.addEventListener(
      "wheel",
      (evt) => {
        if (!state.overview) return;
        evt.preventDefault();
        const anchor = clientToSiteMapWorld(refSvg, evt);
        const factor = evt.deltaY < 0 ? 1.16 : 1 / 1.16;
        zoomSiteMapAt(factor, anchor);
      },
      { passive: false }
    );

    refSvg.addEventListener("dragover", (evt) => {
      evt.preventDefault();
      if (evt.dataTransfer) evt.dataTransfer.dropEffect = "copy";
    });

    refSvg.addEventListener("drop", (evt) => {
      if (!state.siteMap.editMode) return;
      const mKey =
        evt.dataTransfer?.getData("text/vmodel") || evt.dataTransfer?.getData("text/plain");
      if (!mKey || !VEHICLE_MODELS_CLIENT[mKey]) return;
      evt.preventDefault();
      const worldPt = clientToSiteMapWorld(refSvg, evt);
      createDraftVehicleSpotAt(mKey, worldPt);
    });

    refSvg.addEventListener("mousedown", (evt) => {
      if (!state.overview) return;
      const metrics = getSiteMapMetrics(state.overview.site_map);
      const [wx, wy] = clientToSiteMapWorld(refSvg, evt);
      const mx = metrics.nx(wx);
      const my = metrics.ny(wy);

      // Interactive camera placement on Left Reference Floor Plan (works in Browse & Edit mode!)
      if (
        evt.button === 0 &&
        !state.siteMap.spacePressed &&
        state.siteMap.pendingCameraPlacement
      ) {
        evt.preventDefault();
        const spec = state.siteMap.pendingCameraPlacement;
        const snapGrp = findHoveredMapSpotGroup([wx, wy], metrics);
        state.siteMap.dragging = {
          kind: "pending-cam-span-drag",
          activeSvg: refSvg,
          spec,
          anchorWorld: [wx, wy],
          currentWorld: [wx, wy],
          snapGroup: snapGrp,
        };
        renderCameraDropPreviewDom(spec, [wx, wy], null, snapGrp);
        return;
      }

      // Support tracing Driving Routes or Roads directly on the crisp Left Original Floor Plan!
      if (
        evt.button === 0 &&
        !state.siteMap.spacePressed &&
        state.siteMap.editMode &&
        state.siteMap.subTool === "routes" &&
        state.siteMap.drawingRoute
      ) {
        evt.preventDefault();
        const last = state.siteMap.newRoutePts[state.siteMap.newRoutePts.length - 1];
        if (!last || Math.hypot(mx - last[0], my - last[1]) > 0.6) {
          state.siteMap.newRoutePts.push([mx, my]);
          renderAbstractSvgMap(state.overview);
        }
        return;
      }
      if (
        evt.button === 0 &&
        !state.siteMap.spacePressed &&
        state.siteMap.editMode &&
        state.siteMap.subTool === "roads" &&
        state.siteMap.drawingRoad
      ) {
        evt.preventDefault();
        const last = state.siteMap.newRoadPts[state.siteMap.newRoadPts.length - 1];
        if (!last || Math.hypot(mx - last[0], my - last[1]) > 0.6) {
          state.siteMap.newRoadPts.push([mx, my]);
          renderAbstractSvgMap(state.overview);
        }
        return;
      }

      // Designated placement mode on Left Original Floor Plan (click at A or drag A -> B)
      if (
        evt.button === 0 &&
        !state.siteMap.spacePressed &&
        state.siteMap.editMode &&
        state.siteMap.placeMode
      ) {
        evt.preventDefault();
        state.siteMap.dragging = {
          kind: "designated-span-drag",
          activeSvg: refSvg,
          mode: state.siteMap.placeMode,
          anchorWorld: [wx, wy],
          currentWorld: [wx, wy],
        };
        updateSpotPlacePreviewDom([wx, wy], null, state.siteMap.placeMode);
        return;
      }

      // In edit mode, support full stall move / multi-select / rotate / scale handles directly on Left Original Floor Plan!
      if (
        evt.button === 0 &&
        !state.siteMap.spacePressed &&
        state.siteMap.editMode &&
        state.siteMap.subTool !== "pan"
      ) {
        const refHandleEl = evt.target.closest("[data-drag-kind]");
        if (refHandleEl && startSpotOrHandleDrag(refHandleEl, evt, refSvg, metrics, mx, my)) {
          return;
        }
        const refSpotEl = evt.target.closest("[data-ref-spot-id]");
        if (refSpotEl) {
          const fakeTarget = {
            dataset: { dragKind: "spot", spotId: refSpotEl.dataset.refSpotId },
          };
          if (startSpotOrHandleDrag(fakeTarget, evt, refSvg, metrics, mx, my)) {
            return;
          }
        }
        // Empty-canvas drag in spots mode starts rubber-band marquee multi-car selection!
        if (
          state.siteMap.subTool === "spots" &&
          state.siteMap.marqueeMode !== false
        ) {
          evt.preventDefault();
          state.siteMap.dragging = {
            kind: "marquee-select",
            activeSvg: refSvg,
            additive: Boolean(evt.shiftKey || evt.metaKey || evt.ctrlKey),
            baseIds: getSelectedSpots().map((s) => s.spot_id),
            startWorld: [wx, wy],
            curWorld: [wx, wy],
            startClientX: evt.clientX,
            startClientY: evt.clientY,
            movedPx: 0,
          };
          return;
        }
      }

      if (evt.button === 0 || evt.button === 1) {
        const ctm = refSvg.getScreenCTM();
        if (!ctm) return;
        evt.preventDefault();
        state.siteMap.dragging = {
          kind: "pan",
          button: evt.button,
          movedPx: 0,
          startClientX: evt.clientX,
          startClientY: evt.clientY,
          ctmA: ctm.a || 1,
          ctmD: ctm.d || 1,
          origCamX: state.siteMap.camCenterX ?? metrics.worldW / 2,
          origCamY: state.siteMap.camCenterY ?? metrics.worldH / 2,
        };
      }
    });

    refSvg.addEventListener("dblclick", (evt) => {
      if (state.siteMap.editMode && state.siteMap.drawingRoute) {
        evt.preventDefault();
        document.getElementById("btnMapFinishRoute")?.click();
        return;
      }
      if (state.siteMap.editMode && state.siteMap.drawingRoad) {
        evt.preventDefault();
        document.getElementById("btnMapFinishRoad")?.click();
      }
    });

    refSvg.addEventListener("mousemove", (evt) => {
      if (!state.overview || !state.siteMap.compareSplit) return;
      const worldPt = clientToSiteMapWorld(refSvg, evt);
      state.siteMap.syncCursorWorld = worldPt;
      if (
        !state.siteMap.dragging &&
        state.siteMap.editMode &&
        (state.siteMap.drawingRoad || state.siteMap.drawingRoute)
      ) {
        state.siteMap.cursorWorld = worldPt;
        renderAbstractSvgMap(state.overview);
        return;
      }
      if (!state.siteMap.dragging && state.siteMap.editMode && state.siteMap.placeMode) {
        updateSpotPlacePreviewDom(worldPt, null, state.siteMap.placeMode);
      }
      updateSyncCrosshairDom(worldPt);
    });

    refSvg.addEventListener("mouseleave", () => {
      state.siteMap.syncCursorWorld = null;
      updateSyncCrosshairDom(null);
      if (!state.siteMap.dragging && state.siteMap.placeMode) {
        updateSpotPlacePreviewDom(null);
      }
    });
  }

  svg.addEventListener("mousemove", (evt) => {
    if (!state.overview) return;
    const worldPt = clientToSiteMapWorld(svg, evt);
    if (state.siteMap.compareSplit) {
      state.siteMap.syncCursorWorld = worldPt;
      updateSyncCrosshairDom(worldPt);
    }
    if (!state.siteMap.dragging && state.siteMap.editMode && state.siteMap.placeMode) {
      updateSpotPlacePreviewDom(worldPt, null, state.siteMap.placeMode);
    }
  });

  svg.addEventListener("mouseleave", () => {
    if (state.siteMap.compareSplit) {
      state.siteMap.syncCursorWorld = null;
      updateSyncCrosshairDom(null);
    }
    if (!state.siteMap.dragging && state.siteMap.placeMode) {
      updateSpotPlacePreviewDom(null);
    }
  });

  // Sub-tool buttons & Undo/Redo
  document.querySelectorAll(".map-subtool-btn").forEach((btn) => {
    btn.addEventListener("click", () => setSiteMapSubTool(btn.dataset.mapTool));
  });
  document.getElementById("btnMapUndo")?.addEventListener("click", () => undoSiteMapEdit());
  document.getElementById("btnMapRedo")?.addEventListener("click", () => redoSiteMapEdit());

  // Spots sub-tool & Multi-Car Selection actions
  document.getElementById("btnToggleMarqueeMode")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
    state.siteMap.marqueeMode = state.siteMap.marqueeMode === false;
    syncSiteMapStudioControls();
  });

  document.getElementById("btnSelectGroupSpots")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
    const grp = getActiveGroupSpots();
    if (!grp.length) return;
    setSelectedSpotIds(
      grp.map((s) => s.spot_id),
      grp[0].spot_id
    );
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnSelectAllSpots")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
    const all = state.siteMap.draftSpots || [];
    if (!all.length) return;
    setSelectedSpotIds(
      all.map((s) => s.spot_id),
      all[0].spot_id
    );
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnClearSpotSelection")?.addEventListener("click", () => {
    finishCurrentSpotGroup({ armNextPlace: false });
  });

  document.getElementById("btnFinishCurrentGroup")?.addEventListener("click", () => {
    if (getSelectedSpots().length > 0) {
      finishCurrentSpotGroup({ armNextPlace: true });
    } else if (state.siteMap.placeMode === "spots") {
      setDesignatedPlaceMode(null);
      syncSiteMapStudioControls();
    } else {
      finishCurrentSpotGroup({ armNextPlace: true });
    }
  });

  const setSpotPlaceCountAndMode = (countVal, mode = "fixed") => {
    const inp = document.getElementById("spotPlaceCountInput");
    if (countVal !== null && countVal !== undefined && inp) {
      const clamped = Math.max(1, Math.min(40, Math.round(Number(countVal) || 1)));
      inp.value = String(clamped);
    }
    state.siteMap.spotPlaceCountMode = mode === "auto" ? "auto" : "fixed";
    syncSiteMapStudioControls();
    const d = state.siteMap.dragging;
    if (d?.kind === "palette-vmodel-drag") {
      const curN = Math.max(
        1,
        Math.min(40, Number(document.getElementById("spotPlaceCountInput")?.value || 1))
      );
      const ghostEl = document.getElementById("floatingVmodelDragGhost");
      if (ghostEl && !ghostEl.classList.contains("hidden")) {
        ghostEl.textContent = `🚗 ${d.label} × ${curN}辆 · 滚轮增减 · 松手新建独立组`;
      }
      if (d.hoverWorld) {
        updateSpotPlacePreviewDom(d.hoverWorld, null, "palette", d.modelKey);
      }
    } else if (state.siteMap.placeMode && state.siteMap.syncCursorWorld) {
      updateSpotPlacePreviewDom(state.siteMap.syncCursorWorld, null, state.siteMap.placeMode);
    }
  };

  document.querySelectorAll("#spotCountPills .spot-count-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      if (!state.siteMap.editMode) enterSiteMapEditMode();
      if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
      if (pill.dataset.countMode === "auto") {
        setSpotPlaceCountAndMode(null, "auto");
        if (getSelectedSpots().length > 0) {
          finishCurrentSpotGroup({ armNextPlace: true });
        } else if (!state.siteMap.placeMode) {
          setDesignatedPlaceMode("spots");
        }
      } else {
        const v = Number(pill.dataset.countVal || 1);
        setSpotPlaceCountAndMode(v, "fixed");
      }
    });
  });

  document.getElementById("spotPlaceCountInput")?.addEventListener("input", (e) => {
    const raw = Number(e.target.value);
    if (Number.isFinite(raw) && raw >= 1) {
      state.siteMap.spotPlaceCountMode = "fixed";
      syncSiteMapStudioControls();
      if (state.siteMap.placeMode && state.siteMap.syncCursorWorld) {
        updateSpotPlacePreviewDom(state.siteMap.syncCursorWorld, null, state.siteMap.placeMode);
      }
    }
  });

  window.addEventListener(
    "wheel",
    (evt) => {
      const d = state.siteMap.dragging;
      if (!d || d.kind !== "palette-vmodel-drag" || !d.moved) return;
      evt.preventDefault();
      evt.stopPropagation();
      const inp = document.getElementById("spotPlaceCountInput");
      const cur = Math.max(1, Math.min(40, Number(inp?.value || 1)));
      const step = evt.deltaY < 0 ? 1 : -1;
      setSpotPlaceCountAndMode(Math.max(1, Math.min(40, cur + step)), "fixed");
    },
    { capture: true, passive: false }
  );

  document.getElementById("mapGroupSelect")?.addEventListener("change", (e) => {
    const key = e.target.value;
    if (!key) {
      finishCurrentSpotGroup({ armNextPlace: false });
      return;
    }
    state.siteMap.selectedGroupCamId = key;
    const grp = (state.siteMap.draftSpots || []).filter((s) => getSpotGroupKey(s) === key);
    setSelectedSpotIds(
      grp.map((s) => s.spot_id),
      grp[0]?.spot_id || null
    );
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapLayoutPerp")?.addEventListener("click", () => {
    const sel = getSelectedSpots();
    const g = sel.length >= 2 ? sel : getActiveGroupSpots();
    if (!g.length) return;
    pushSiteMapUndo();
    layoutGroupSpotsLinear(g, "perp");
    renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapLayoutParallel")?.addEventListener("click", () => {
    const sel = getSelectedSpots();
    const g = sel.length >= 2 ? sel : getActiveGroupSpots();
    if (!g.length) return;
    pushSiteMapUndo();
    layoutGroupSpotsLinear(g, "parallel");
    renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapSnapRoad")?.addEventListener("click", () => {
    snapActiveGroupToNearestRoad();
  });

  const adjustGroupSpacing = (scale) => {
    const sel = getSelectedSpots();
    const g = sel.length >= 2 ? sel : getActiveGroupSpots();
    if (g.length < 2 || !state.overview) return;
    pushSiteMapUndo();
    const metrics = getSiteMapMetrics(state.overview.site_map);
    const pts = g.map((s) => [metrics.px(s.map_x), metrics.py(s.map_y)]);
    const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
    const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
    g.forEach((sp, i) => {
      const sx = Math.min(metrics.worldW - 1, Math.max(1, cx + (pts[i][0] - cx) * scale));
      const sy = Math.min(metrics.worldH - 1, Math.max(1, cy + (pts[i][1] - cy) * scale));
      sp.map_x = metrics.nx(sx);
      sp.map_y = metrics.ny(sy);
      sp.map_custom = 1;
    });
    renderAbstractSvgMap(state.overview);
  };
  document.getElementById("btnMapGapDec")?.addEventListener("click", () => adjustGroupSpacing(0.88));
  document.getElementById("btnMapGapInc")?.addEventListener("click", () => adjustGroupSpacing(1.14));

  const adjustSpotSize = (scale, applyGroup) => {
    const targets = getSpotTransformTargets(applyGroup);
    const list = targets.length ? targets : getActiveGroupSpots();
    if (!list.length) return;
    pushSiteMapUndo();
    const scalePosToo = list.length >= 2 && isMultiScaleSpacingChecked() && state.overview;
    const metrics = scalePosToo ? getSiteMapMetrics(state.overview.site_map) : null;
    const pts = scalePosToo ? list.map((s) => [metrics.px(s.map_x), metrics.py(s.map_y)]) : null;
    const cx = scalePosToo ? pts.reduce((a, p) => a + p[0], 0) / pts.length : 0;
    const cy = scalePosToo ? pts.reduce((a, p) => a + p[1], 0) / pts.length : 0;

    list.forEach((sp, idx) => {
      sp.map_w = +Math.min(14, Math.max(0.75, Number(sp.map_w || 1.75) * scale)).toFixed(2);
      sp.map_h = +Math.min(14, Math.max(0.75, Number(sp.map_h || 3.25) * scale)).toFixed(2);
      if (scalePosToo) {
        const sx = Math.min(
          metrics.worldW - 0.8,
          Math.max(0.8, cx + (pts[idx][0] - cx) * scale)
        );
        const sy = Math.min(
          metrics.worldH - 0.8,
          Math.max(0.8, cy + (pts[idx][1] - cy) * scale)
        );
        sp.map_x = metrics.nx(sx);
        sp.map_y = metrics.ny(sy);
      }
      sp.map_custom = 1;
    });
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  };
  document.getElementById("btnMapSizeDec")?.addEventListener("click", (e) => adjustSpotSize(0.9, e.shiftKey));
  document.getElementById("btnMapSizeInc")?.addEventListener("click", (e) => adjustSpotSize(1.11, e.shiftKey));

  const rotateSpotSelection = (deltaDeg, applyGroup) => {
    const targets = getSpotTransformTargets(applyGroup);
    const list = targets.length ? targets : getActiveGroupSpots();
    if (!list.length || !state.overview) return;
    pushSiteMapUndo();
    if (list.length > 1 && isMultiScaleSpacingChecked()) {
      const metrics = getSiteMapMetrics(state.overview.site_map);
      const pts = list.map((s) => [metrics.px(s.map_x), metrics.py(s.map_y)]);
      const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
      const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
      const rad = (deltaDeg * Math.PI) / 180;
      const cosA = Math.cos(rad);
      const sinA = Math.sin(rad);
      list.forEach((sp, i) => {
        const dx = pts[i][0] - cx;
        const dy = pts[i][1] - cy;
        sp.map_x = metrics.nx(cx + dx * cosA - dy * sinA);
        sp.map_y = metrics.ny(cy + dx * sinA + dy * cosA);
        sp.map_angle = normalizeDeg(Number(sp.map_angle || 0) + deltaDeg);
        sp.map_custom = 1;
      });
    } else {
      list.forEach((sp) => {
        sp.map_angle = normalizeDeg(Number(sp.map_angle || 0) + deltaDeg);
        sp.map_custom = 1;
      });
    }
    syncSiteMapStudioControls();
    renderAbstractSvgMap(state.overview);
  };
  document.getElementById("btnMapRotLeft")?.addEventListener("click", (e) => rotateSpotSelection(-15, e.shiftKey));
  document.getElementById("btnMapRotRight")?.addEventListener("click", (e) => rotateSpotSelection(15, e.shiftKey));

  // Smooth Angle Slider (-180° ~ 180°) & Proportional Scale Slider (40% ~ 240%) for 1 or N selected cars!
  let angleSliderSession = null;
  const angleSliderEl = document.getElementById("mapSpotAngleSlider");
  const endAngleSliderSession = () => {
    angleSliderSession = null;
  };
  angleSliderEl?.addEventListener("pointerdown", endAngleSliderSession);
  angleSliderEl?.addEventListener("change", endAngleSliderSession);
  angleSliderEl?.addEventListener("input", (e) => {
    if (!state.overview) return;
    const targets = getSpotTransformTargets(false);
    const sel = targets[0];
    if (!sel) return;
    const metrics = getSiteMapMetrics(state.overview.site_map);
    const targetDeg = normalizeDeg(Number(e.target.value || 0));
    if (!angleSliderSession) {
      pushSiteMapUndo();
      const revolve = targets.length >= 2 && isMultiScaleSpacingChecked();
      const pts = targets.map((t) => [metrics.px(t.map_x), metrics.py(t.map_y)]);
      const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
      const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
      angleSliderSession = {
        anchorDeg: Number(sel.map_angle || 0),
        revolve,
        cx,
        cy,
        items: targets.map((t, idx) => ({
          ref: t,
          startDeg: Number(t.map_angle || 0),
          origWx: pts[idx][0],
          origWy: pts[idx][1],
        })),
      };
    }
    const delta = targetDeg - angleSliderSession.anchorDeg;
    const rad = (delta * Math.PI) / 180;
    const cosA = Math.cos(rad);
    const sinA = Math.sin(rad);
    angleSliderSession.items.forEach(({ ref, startDeg, origWx, origWy }) => {
      if (angleSliderSession.revolve) {
        const dx = origWx - angleSliderSession.cx;
        const dy = origWy - angleSliderSession.cy;
        const nxW = Math.min(
          metrics.worldW - 0.8,
          Math.max(0.8, angleSliderSession.cx + dx * cosA - dy * sinA)
        );
        const nyW = Math.min(
          metrics.worldH - 0.8,
          Math.max(0.8, angleSliderSession.cy + dx * sinA + dy * cosA)
        );
        ref.map_x = metrics.nx(nxW);
        ref.map_y = metrics.ny(nyW);
      }
      ref.map_angle = normalizeDeg(startDeg + delta);
      ref.map_custom = 1;
    });
    syncSpotTransformSlidersOnly(sel);
    renderAbstractSvgMap(state.overview);
  });

  let scaleSliderSession = null;
  const scaleSliderEl = document.getElementById("mapSpotScaleSlider");
  const endScaleSliderSession = () => {
    scaleSliderSession = null;
  };
  scaleSliderEl?.addEventListener("pointerdown", endScaleSliderSession);
  scaleSliderEl?.addEventListener("change", endScaleSliderSession);
  scaleSliderEl?.addEventListener("input", (e) => {
    if (!state.overview) return;
    const targets = getSpotTransformTargets(false);
    const sel = targets[0];
    if (!sel) return;
    const metrics = getSiteMapMetrics(state.overview.site_map);
    const nextPct = Math.min(240, Math.max(40, Number(e.target.value || 100)));
    if (!scaleSliderSession) {
      pushSiteMapUndo();
      const preset = getSpotModelPreset(sel);
      const baseDiag = Math.max(0.5, Math.hypot(preset.w, preset.h));
      const curDiag = Math.max(
        0.5,
        Math.hypot(Number(sel.map_w || preset.w), Number(sel.map_h || preset.h))
      );
      const startPct = Math.max(25, (curDiag / baseDiag) * 100);
      const scaleSpacing = targets.length >= 2 && isMultiScaleSpacingChecked();
      const pts = targets.map((t) => [metrics.px(t.map_x), metrics.py(t.map_y)]);
      const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
      const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
      scaleSliderSession = {
        startPct,
        scaleSpacing,
        cx,
        cy,
        items: targets.map((t, idx) => ({
          ref: t,
          origWx: pts[idx][0],
          origWy: pts[idx][1],
          startW: Math.max(0.75, Number(t.map_w || preset.w)),
          startH: Math.max(0.75, Number(t.map_h || preset.h)),
        })),
      };
    }
    const factor = nextPct / scaleSliderSession.startPct;
    scaleSliderSession.items.forEach(({ ref, origWx, origWy, startW, startH }) => {
      ref.map_w = +Math.min(14, Math.max(0.75, startW * factor)).toFixed(2);
      ref.map_h = +Math.min(14, Math.max(0.75, startH * factor)).toFixed(2);
      if (scaleSliderSession.scaleSpacing) {
        const nxW = Math.min(
          metrics.worldW - 0.8,
          Math.max(0.8, scaleSliderSession.cx + (origWx - scaleSliderSession.cx) * factor)
        );
        const nyW = Math.min(
          metrics.worldH - 0.8,
          Math.max(0.8, scaleSliderSession.cy + (origWy - scaleSliderSession.cy) * factor)
        );
        ref.map_x = metrics.nx(nxW);
        ref.map_y = metrics.ny(nyW);
      }
      ref.map_custom = 1;
    });
    syncSpotTransformSlidersOnly(sel);
    renderAbstractSvgMap(state.overview);
  });

  // Flip Vertical <-> Horizontal (swap width/length & orientation)
  document.getElementById("btnMapFlipHV")?.addEventListener("click", (e) => {
    const targets = getSpotTransformTargets(e.shiftKey);
    const list = targets.length ? targets : getActiveGroupSpots();
    if (!list.length) return;
    pushSiteMapUndo();
    list.forEach((sp) => {
      const oldW = Number(sp.map_w || 1.75);
      const oldH = Number(sp.map_h || 3.25);
      sp.map_w = +oldH.toFixed(2);
      sp.map_h = +oldW.toFixed(2);
      sp.vehicle_model = sp.map_w > sp.map_h * 1.2 ? "parallel" : "sedan";
      sp.map_custom = 1;
    });
    syncSiteMapStudioControls();
    renderAbstractSvgMap(state.overview);
  });

  // Flip 180° nose direction
  document.getElementById("btnMapFlip180")?.addEventListener("click", (e) => {
    rotateSpotSelection(180, e.shiftKey);
  });

  // Group code, regional name & building/unit proximity editing (e.g., 5LV7 / 5号楼东北侧车位)
  document.getElementById("mapSpotGroupCodeInput")?.addEventListener("change", (e) => {
    const codeVal = e.target.value.trim().toUpperCase();
    const parsed = parseSingleGroupTokenClient(codeVal);
    const normCode = parsed ? parsed.code : codeVal;
    const sp = (state.siteMap.draftSpots || []).find(
      (s) => s.spot_id === state.siteMap.selectedSpotId
    );
    if (!sp) return;
    const grpSpots = getActiveGroupSpots();
    pushSiteMapUndo();
    (grpSpots.length ? grpSpots : [sp]).forEach((item, idx) => {
      item.group_code = normCode;
      if (normCode) {
        item.label = `${normCode}-${String(idx + 1).padStart(2, "0")}`;
      }
      if (parsed && !item.group_name) {
        item.group_name = parsed.full_cn;
        item.zone_name = parsed.full_cn;
      }
    });
    state.siteMap.selectedGroupCamId = getSpotGroupKey(sp);
    syncSiteMapStudioControls();
    renderAbstractSvgMap(state.overview);
  });

  document.getElementById("mapSpotGroupNameInput")?.addEventListener("change", (e) => {
    const gName = e.target.value.trim();
    const grpSpots = getActiveGroupSpots();
    if (!grpSpots.length) return;
    pushSiteMapUndo();
    grpSpots.forEach((item) => {
      item.group_name = gName;
      if (gName) item.zone_name = gName;
    });
    syncSiteMapStudioControls();
    renderAbstractSvgMap(state.overview);
  });

  document.getElementById("mapSpotNearUnitSelect")?.addEventListener("change", (e) => {
    const nearUid = e.target.value || "";
    const grpSpots = getActiveGroupSpots();
    if (!grpSpots.length) return;
    pushSiteMapUndo();
    grpSpots.forEach((item) => {
      item.near_unit_id = nearUid;
    });
    syncSiteMapStudioControls();
    renderAbstractSvgMap(state.overview);
  });

  // Group/spot camera binding select (updates active stall group together so whole group binds cleanly)
  const resolveTargetGroupSpotsForBind = () => {
    const selSpots = getSelectedSpots();
    if (selSpots.length >= 2) return selSpots;
    const activeGroup = getActiveGroupSpots();
    if (activeGroup.length) return activeGroup;
    const singleSp = (state.siteMap.draftSpots || []).find(
      (s) => s.spot_id === state.siteMap.selectedSpotId
    );
    if (singleSp) return [singleSp];
    // Fallback when user just clicked "完成当前车位组" (__NEXT_NEW_GROUP__): pick the latest UNASSIGNED draft group!
    const unassigned = (state.siteMap.draftSpots || []).filter(
      (s) => !s.camera_id || s.camera_id === "UNASSIGNED"
    );
    if (unassigned.length) {
      const lastSp = unassigned[unassigned.length - 1];
      const lastGc = String(lastSp.group_code || "").trim();
      if (lastGc) {
        return unassigned.filter((s) => String(s.group_code || "").trim() === lastGc);
      }
      return [lastSp];
    }
    return [];
  };

  document.getElementById("mapSpotCameraBindSelect")?.addEventListener("change", (e) => {
    const newCamId = e.target.value || "UNASSIGNED";
    const spec = newCamId !== "UNASSIGNED" ? inferCameraPlacementDefaults(newCamId) : null;
    const targetSpots = resolveTargetGroupSpotsForBind();

    // Auto-prefill code/name/near-unit inputs when picking an unbound camera even before clicking 绑定并命名
    const gCodeInput = document.getElementById("mapSpotGroupCodeInput");
    const gNameInput = document.getElementById("mapSpotGroupNameInput");
    const nearUnitSel = document.getElementById("mapSpotNearUnitSelect");
    if (spec) {
      if (gCodeInput) gCodeInput.disabled = false;
      if (gNameInput) gNameInput.disabled = false;
      if (nearUnitSel) nearUnitSel.disabled = false;
    }

    if (!targetSpots.length) {
      if (spec) {
        if (gCodeInput && !gCodeInput.value.trim()) gCodeInput.value = spec.baseCode;
        if (gNameInput && !gNameInput.value.trim()) gNameInput.value = spec.groupName;
        if (nearUnitSel && !nearUnitSel.value && spec.nearUnitId) {
          if ([...nearUnitSel.options].some((o) => o.value === spec.nearUnitId)) {
            nearUnitSel.value = spec.nearUnitId;
          }
        }
        const hintEl = document.getElementById("mapStudioHint");
        if (hintEl) {
          hintEl.textContent = `已选择「${spec.cameraName}」，点击右侧『绑定并命名』将直接在平面图自动生成 ${spec.count} 个实标车位并完成绑定`;
        }
      }
      return;
    }

    // Do NOT silently mutate an already-bound group of a DIFFERENT camera on dropdown preview;
    // wait for explicit confirmation on 『绑定并命名』 if it belongs to another camera!
    const existingCam = targetSpots[0].camera_id || "UNASSIGNED";
    if (
      existingCam !== "UNASSIGNED" &&
      newCamId !== "UNASSIGNED" &&
      existingCam !== newCamId
    ) {
      if (spec) {
        if (gCodeInput) gCodeInput.value = spec.baseCode;
        if (gNameInput) gNameInput.value = spec.groupName;
        if (nearUnitSel && spec.nearUnitId) {
          if ([...nearUnitSel.options].some((o) => o.value === spec.nearUnitId)) {
            nearUnitSel.value = spec.nearUnitId;
          }
        }
      }
      const hintEl = document.getElementById("mapStudioHint");
      if (hintEl) {
        hintEl.textContent = `⚠️ 当前选中车位组已绑 ${existingCam}；点击『绑定并命名』可选择为 ${newCamId} 新建独立组或换绑`;
      }
      return;
    }

    pushSiteMapUndo();
    const camObj =
      newCamId !== "UNASSIGNED"
        ? (state.overview?.cameras || []).find((c) => c.camera_id === newCamId)
        : null;
    const camPrimaryGc = spec?.baseCode || "";
    const curGc = String(targetSpots[0].group_code || "").trim();
    const canAdoptCamGc =
      Boolean(camPrimaryGc) &&
      (!curGc || /^GRP_\d+$/i.test(curGc) || /^G\d+$/i.test(curGc)) &&
      !(state.siteMap.draftSpots || []).some(
        (s) =>
          !targetSpots.includes(s) &&
          String(s.group_code || "").trim().toUpperCase() === camPrimaryGc
      );
    targetSpots.forEach((sp, idx) => {
      sp.camera_id = newCamId;
      if (camObj?.zone_id) {
        sp.zone_id = camObj.zone_id;
      }
      if (canAdoptCamGc) {
        sp.group_code = camPrimaryGc;
        sp.label = `${camPrimaryGc}-${String(idx + 1).padStart(2, "0")}`;
      }
      if (spec?.groupName && (!sp.group_name || sp.group_name === curGc)) {
        sp.group_name = spec.groupName;
      }
      if (spec?.nearUnitId && !sp.near_unit_id) {
        sp.near_unit_id = spec.nearUnitId;
      }
    });
    setSelectedSpotIds(
      targetSpots.map((s) => s.spot_id),
      targetSpots[0].spot_id
    );
    state.siteMap.selectedGroupCamId = getSpotGroupKey(targetSpots[0]);
    const hintEl = document.getElementById("mapStudioHint");
    if (hintEl) {
      hintEl.textContent =
        newCamId === "UNASSIGNED"
          ? `已设为暂不绑定摄像头，点击『绑定并命名』立即保存生效`
          : `已预关联「${camObj?.name || newCamId}」，点击『绑定并命名』立即按实标车位数 1:1 落盘生效`;
    }
    syncSiteMapStudioControls();
    renderAbstractSvgMap(state.overview);
  });

  // Batch bind selected multi-car stalls (or entire active/unassigned group) to selected camera + name + unit AND persist immediately!
  document.getElementById("btnMapBindGroupCam")?.addEventListener("click", async () => {
    const bindBtn = document.getElementById("btnMapBindGroupCam");
    const camSel = document.getElementById("mapSpotCameraBindSelect");
    const newCamId = camSel?.value || "UNASSIGNED";
    const spec = newCamId !== "UNASSIGNED" ? inferCameraPlacementDefaults(newCamId) : null;
    let groupSpots = resolveTargetGroupSpotsForBind();

    const typedCode = document
      .getElementById("mapSpotGroupCodeInput")
      ?.value?.trim()
      .toUpperCase();
    const typedName = document.getElementById("mapSpotGroupNameInput")?.value?.trim() || "";
    const nearUid = document.getElementById("mapSpotNearUnitSelect")?.value || "";

    // Guard if target group is ALREADY bound to a different camera!
    if (groupSpots.length) {
      const existingCam = groupSpots[0].camera_id || "UNASSIGNED";
      if (
        existingCam !== "UNASSIGNED" &&
        newCamId !== "UNASSIGNED" &&
        existingCam !== newCamId
      ) {
        const unassignedAlt = (state.siteMap.draftSpots || []).filter(
          (s) => !s.camera_id || s.camera_id === "UNASSIGNED"
        );
        if (unassignedAlt.length) {
          const lastGc = String(unassignedAlt[unassignedAlt.length - 1].group_code || "").trim();
          groupSpots = lastGc
            ? unassignedAlt.filter((s) => String(s.group_code || "").trim() === lastGc)
            : [unassignedAlt[unassignedAlt.length - 1]];
        } else {
          const createInstead = window.confirm(
            `当前选中的车位组「${groupSpots[0].group_code}」已绑定机位「${existingCam}」！\n\n点击【确定】将为「${
              spec?.cameraName || newCamId
            }」在平面图新建独立车位组（保留 ${
              groupSpots[0].group_code
            } 不变，推荐）；\n点击【取消】则不作修改。`
          );
          if (!createInstead) return;
          groupSpots = [];
        }
      }
    }

    const origBtnText = bindBtn?.textContent || "绑定并命名";
    if (bindBtn) {
      bindBtn.disabled = true;
      bindBtn.textContent = "绑定中...";
    }
    try {
      // If there are no unbound draft spots yet and a camera was chosen, auto-place & bind it cleanly!
      if (!groupSpots.length) {
        if (newCamId === "UNASSIGNED") {
          showAppleToast("请先在平面图点选一组车位，或选择要绑定的机位", "warn");
          return;
        }
        await persistSiteMapDraftInPlace();
        const placeRes = await api("/api/sitemap/place-camera", {
          method: "POST",
          body: JSON.stringify({
            camera_id: newCamId,
            orientation: spec?.orientation || "auto",
            group_code: typedCode || spec?.baseCode || undefined,
            group_name: typedName || spec?.groupName || undefined,
            near_unit_id: nearUid || spec?.nearUnitId || undefined,
            zone_id: spec?.zoneId || undefined,
          }),
        });
        await persistSiteMapDraftInPlace({
          selectGroupCode: placeRes.group_code,
          selectCameraId: newCamId,
        });
        const hintEl = document.getElementById("mapStudioHint");
        if (hintEl) {
          hintEl.textContent = `✅ 已自动生成独立车位组「${placeRes.group_code}」并绑定 ${newCamId}（实标 ${placeRes.spot_count} 位），可直接在图中拖动微调位置`;
        }
        showAppleToast(
          `✅ 已自动生成并绑定「${placeRes.group_code}」（实标 ${placeRes.spot_count} 车位）`
        );
        if (bindBtn) bindBtn.textContent = "✓ 已绑定生效";
        setTimeout(() => {
          if (bindBtn) bindBtn.textContent = origBtnText;
        }, 1600);
        return;
      }

      pushSiteMapUndo();
      const camObj =
        newCamId !== "UNASSIGNED"
          ? (state.overview?.cameras || []).find((c) => c.camera_id === newCamId)
          : null;
      const curGc = String(groupSpots[0].group_code || "").trim();
      let desiredCode = typedCode;
      if (
        (!desiredCode || desiredCode === curGc) &&
        spec?.baseCode &&
        (!curGc || /^GRP_\d+$/i.test(curGc) || /^G\d+$/i.test(curGc)) &&
        !(state.siteMap.draftSpots || []).some(
          (s) =>
            !groupSpots.includes(s) &&
            String(s.group_code || "").trim().toUpperCase() === spec.baseCode
        )
      ) {
        desiredCode = spec.baseCode;
      }
      const parsed = desiredCode ? parseSingleGroupTokenClient(desiredCode) : null;
      const finalCode = parsed ? parsed.code : desiredCode || curGc;
      const finalName =
        typedName ||
        spec?.groupName ||
        (parsed ? parsed.full_cn : "") ||
        groupSpots[0].group_name ||
        "";
      const finalNearUid = nearUid || spec?.nearUnitId || groupSpots[0].near_unit_id || "";

      groupSpots.forEach((sp, idx) => {
        sp.camera_id = newCamId;
        if (finalCode) {
          sp.group_code = finalCode;
          sp.label = `${finalCode}-${String(idx + 1).padStart(2, "0")}`;
        }
        if (finalName) {
          sp.group_name = finalName;
          sp.zone_name = finalName;
        }
        if (camObj?.zone_id) {
          sp.zone_id = camObj.zone_id;
        }
        sp.near_unit_id = finalNearUid;
      });
      state.siteMap.selectedGroupCamId = getSpotGroupKey(groupSpots[0]);

      await persistSiteMapDraftInPlace({
        selectGroupCode: finalCode,
        selectCameraId: newCamId !== "UNASSIGNED" ? newCamId : "",
      });

      const reconciledCount = (state.siteMap.draftSpots || []).filter(
        (s) => String(s.group_code || "").toUpperCase() === String(finalCode || "").toUpperCase()
      ).length;
      const hintEl = document.getElementById("mapStudioHint");
      if (hintEl) {
        hintEl.textContent =
          newCamId !== "UNASSIGNED"
            ? `✅ 已绑定并保存「${finalName || finalCode}」→ ${
                camObj?.name || newCamId
              }（已按空间定标同步为 ${reconciledCount || groupSpots.length} 车位）`
            : `✅ 已保存车位组「${finalName || finalCode}」（${
                reconciledCount || groupSpots.length
              } 车位）`;
      }
      showAppleToast(
        newCamId !== "UNASSIGNED"
          ? `✅ 已绑定「${finalName || finalCode}」→ ${
              camObj?.name || newCamId
            }（实标 ${reconciledCount || groupSpots.length} 位立即生效）`
          : `✅ 已更新车位组「${finalName || finalCode}」`
      );
      if (bindBtn) bindBtn.textContent = "✓ 已绑定生效";
      setTimeout(() => {
        if (bindBtn) bindBtn.textContent = origBtnText;
      }, 1600);
    } catch (err) {
      showAppleToast(`绑定保存失败：${err.message}`, "error", 4600);
      if (bindBtn) bindBtn.textContent = origBtnText;
    } finally {
      if (bindBtn) bindBtn.disabled = false;
    }
  });

  // Drag-and-Drop & Click Vehicle Model Palette (.vmodel-chip)
  // Designated Spot Calculation & Commit (Click at A or Drag A -> B, zero percentage guessing!)
  const getDesignatedPlaceConfig = (sourceMode = "spots", overrideModelKey = null) => {
    if (sourceMode === "routes" || sourceMode === "roads") {
      const rawCode = document.getElementById("routeGenGroupCode")?.value?.trim() || "";
      const parsed = parseSingleGroupTokenClient(rawCode);
      const count = Math.max(
        1,
        Math.min(40, Number(document.getElementById("routeGenCount")?.value || parsed?.count || 2))
      );
      const modelKey =
        overrideModelKey ||
        document.getElementById("routeGenModelSelect")?.value ||
        parsed?.default_model ||
        "parallel";
      const alignMode = document.getElementById("routeGenSideSelect")?.value || "exact";
      const camId = document.getElementById("routeGenCamSelect")?.value || "UNASSIGNED";
      return {
        rawCode,
        parsed,
        count,
        countMode: "fixed",
        modelKey,
        alignMode,
        camId,
      };
    }

    // Section 1 placements ("spots" or "palette") ALWAYS form a brand-new isolated group
    // so finishing one group and adding another never merges or links with the previous group!
    const count = Math.max(
      1,
      Math.min(40, Number(document.getElementById("spotPlaceCountInput")?.value || 1))
    );
    const countMode =
      sourceMode === "palette" ? "fixed" : state.siteMap.spotPlaceCountMode || "auto";
    const modelKey = overrideModelKey || state.siteMap.paletteModel || "sedan";
    return {
      rawCode: "",
      parsed: null,
      count,
      countMode,
      modelKey,
      alignMode: "exact",
      camId: "UNASSIGNED",
    };
  };

  window.__computeDesignatedSpotsClient = (
    anchorWorld,
    endWorld = null,
    sourceMode = "spots",
    overrideModelKey = null
  ) => {
    if (!state.overview || !anchorWorld) return [];
    const metrics = getSiteMapMetrics(state.overview.site_map);
    const cfg = getDesignatedPlaceConfig(sourceMode, overrideModelKey);
    const preset = VEHICLE_MODELS_CLIENT[cfg.modelKey] || VEHICLE_MODELS_CLIENT.sedan;
    const w = preset.w;
    const h = preset.h;
    const isParallel = w > h * 1.2;
    const revSign = state.siteMap.placeReverseDir ? -1 : 1;
    const tiltRad = ((preset.angleOffset || 0) * Math.PI) / 180;
    const cosTilt = Math.max(0.55, Math.abs(Math.cos(tiltRad)));
    const stepPitch = (w + (isParallel ? 0.56 : 0.42)) / cosTilt;

    const ax = Math.min(metrics.worldW - 1.2, Math.max(1.2, anchorWorld[0]));
    const ay = Math.min(metrics.worldH - 1.2, Math.max(1.2, anchorWorld[1]));
    const spanDist = endWorld
      ? Math.hypot(endWorld[0] - anchorWorld[0], endWorld[1] - anchorWorld[1])
      : 0;

    const items = [];
    if (endWorld && spanDist >= 1.0) {
      const bx = Math.min(metrics.worldW - 1.2, Math.max(1.2, endWorld[0]));
      const by = Math.min(metrics.worldH - 1.2, Math.max(1.2, endWorld[1]));
      const vecDx = bx - ax;
      const vecDy = by - ay;
      const vecLen = Math.max(0.001, Math.hypot(vecDx, vecDy));
      const uxSpan = vecDx / vecLen;
      const uySpan = vecDy / vecLen;
      const vecDeg = (Math.atan2(vecDy, vecDx) * 180) / Math.PI;
      const effectiveCount =
        cfg.countMode === "auto"
          ? Math.max(1, Math.min(40, Math.floor(spanDist / stepPitch) + 1))
          : cfg.count;

      if (effectiveCount === 1) {
        // Dragging with 1 car aims its nose directly toward B!
        const baseNoseDeg = isParallel ? vecDeg : vecDeg + 90;
        const angle = normalizeDeg(
          baseNoseDeg + (state.siteMap.placeReverseDir ? 180 : 0) + (preset.angleOffset || 0)
        );
        items.push({ wx: ax, wy: ay, w, h, angle, preset, cfg });
      } else {
        const angle = normalizeDeg(
          vecDeg + (state.siteMap.placeReverseDir ? 180 : 0) + (preset.angleOffset || 0)
        );
        const pitch =
          cfg.countMode === "auto"
            ? stepPitch
            : Math.max(stepPitch, vecLen / Math.max(1, effectiveCount - 1));
        for (let i = 0; i < effectiveCount; i++) {
          const sx = Math.min(metrics.worldW - 1.2, Math.max(1.2, ax + uxSpan * pitch * i));
          const sy = Math.min(metrics.worldH - 1.2, Math.max(1.2, ay + uySpan * pitch * i));
          items.push({
            wx: sx,
            wy: sy,
            w,
            h,
            angle,
            preset,
            cfg,
          });
        }
      }
      return items;
    }

    // Single click / hover at A: auto-detect tangent from selected road/route or nearest road
    let near = null;
    if (sourceMode === "roads" || sourceMode === "routes") {
      const roadsArr = state.siteMap.draftRoads || [];
      const routesArr = state.siteMap.draftRoutes || [];
      const selLine =
        sourceMode === "roads"
          ? roadsArr[state.siteMap.selectedRoadIdx] || roadsArr[0]
          : routesArr[state.siteMap.selectedRouteIdx] || routesArr[0] || roadsArr[0];
      if (selLine && Array.isArray(selLine.points) && selLine.points.length >= 2) {
        let best = null;
        for (let i = 0; i < selLine.points.length - 1; i++) {
          const x1 = metrics.px(selLine.points[i][0]);
          const y1 = metrics.py(selLine.points[i][1]);
          const x2 = metrics.px(selLine.points[i + 1][0]);
          const y2 = metrics.py(selLine.points[i + 1][1]);
          const dx = x2 - x1;
          const dy = y2 - y1;
          const len2 = dx * dx + dy * dy;
          if (len2 < 0.001) continue;
          const t = Math.max(0, Math.min(1, ((ax - x1) * dx + (ay - y1) * dy) / len2));
          const projX = x1 + t * dx;
          const projY = y1 + t * dy;
          const d = Math.hypot(ax - projX, ay - projY);
          if (!best || d < best.dist) {
            best = {
              dist: d,
              projX,
              projY,
              dx,
              dy,
              segLen: Math.sqrt(len2),
              angleDeg: (Math.atan2(dy, dx) * 180) / Math.PI,
            };
          }
        }
        near = best;
      }
    }
    if (!near) {
      near = findNearestRouteOrRoadTangent(ax, ay, metrics);
    }

    const tanDeg = near && near.dist < 32 ? near.angleDeg : 0;
    const tanRad = (tanDeg * Math.PI) / 180;
    const ux = Math.cos(tanRad);
    const uy = Math.sin(tanRad);

    let ox = ax;
    let oy = ay;
    if (cfg.alignMode !== "exact" && near && near.dist < 40) {
      const projX = near.projX ?? ax;
      const projY = near.projY ?? ay;
      let sideSign = 1;
      if (cfg.alignMode === "left") {
        sideSign = -1;
      } else if (cfg.alignMode === "right") {
        sideSign = 1;
      } else {
        const cross = ux * (ay - projY) - uy * (ax - projX);
        sideSign = cross >= 0 ? 1 : -1;
      }
      const nxPerp = -uy * sideSign;
      const nyPerp = ux * sideSign;
      const offsetDist = 2.4 + h * 0.5 + 0.35;
      ox = Math.min(metrics.worldW - 1.2, Math.max(1.2, projX + nxPerp * offsetDist));
      oy = Math.min(metrics.worldH - 1.2, Math.max(1.2, projY + nyPerp * offsetDist));
    }

    const angle = normalizeDeg(
      tanDeg + (revSign < 0 ? 180 : 0) + (preset.angleOffset || 0)
    );
    for (let i = 0; i < cfg.count; i++) {
      const sx = Math.min(metrics.worldW - 1.2, Math.max(1.2, ox + ux * revSign * stepPitch * i));
      const sy = Math.min(metrics.worldH - 1.2, Math.max(1.2, oy + uy * revSign * stepPitch * i));
      items.push({ wx: sx, wy: sy, w, h, angle, preset, cfg });
    }
    return items;
  };

  const commitDesignatedSpots = async ({
    mode = "spots",
    modelKey = null,
    anchorWorld = null,
    endWorld = null,
  }) => {
    if (!state.overview || !anchorWorld) return;
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    const metrics = getSiteMapMetrics(state.overview.site_map);
    const cfg = getDesignatedPlaceConfig(mode, modelKey);
    const preset = VEHICLE_MODELS_CLIENT[cfg.modelKey] || VEHICLE_MODELS_CLIENT.sedan;
    const spotsNow = state.siteMap.draftSpots || [];
    const gCodeNorm = cfg.parsed
      ? cfg.parsed.code
      : cfg.rawCode
      ? cfg.rawCode.toUpperCase()
      : allocateNextGroupCode(spotsNow);
    const camObj =
      cfg.camId !== "UNASSIGNED"
        ? (state.overview.cameras || []).find((c) => c.camera_id === cfg.camId)
        : null;
    const zoneId =
      camObj?.zone_id ||
      (cfg.parsed ? `Z_${cfg.parsed.building}_${cfg.parsed.direction_code}` : null) ||
      `Z_GRP_${gCodeNorm}`;

    // When placing via unified 2F Road / Route Planner and a valid road/route exists, persist/compute via backend endpoint!
    const roadsForGen = state.siteMap.draftRoads || [];
    const routes = state.siteMap.draftRoutes || [];
    const selRt =
      mode === "roads"
        ? roadsForGen[state.siteMap.selectedRoadIdx] || roadsForGen[0]
        : routes[state.siteMap.selectedRouteIdx] || routes[0] || roadsForGen[0];
    if (
      (mode === "routes" || mode === "roads") &&
      selRt &&
      Array.isArray(selRt.points) &&
      selRt.points.length >= 2
    ) {
      try {
        const spanValid =
          endWorld &&
          Math.hypot(endWorld[0] - anchorWorld[0], endWorld[1] - anchorWorld[1]) >= 1.0;
        // Note: When count === 1 and user dragged a nose vector A -> B, local computation gives exact nose aiming;
        // for multi-spot spans or single-click route snap/exact, call backend endpoint cleanly.
        if (!(spanValid && cfg.count === 1)) {
          const res = await api("/api/sitemap/generate-route-spots", {
            method: "POST",
            body: JSON.stringify({
              route_points: selRt.points,
              count: cfg.count,
              vehicle_model: cfg.modelKey,
              side: cfg.alignMode === "exact" ? "auto" : cfg.alignMode,
              placement_mode: cfg.alignMode === "exact" ? "exact" : "route_snap",
              anchor_point: [metrics.nx(anchorWorld[0]), metrics.ny(anchorWorld[1])],
              end_point: spanValid
                ? [metrics.nx(endWorld[0]), metrics.ny(endWorld[1])]
                : null,
              reverse_direction: Boolean(state.siteMap.placeReverseDir),
              angle_offset_deg: Number(preset.angleOffset || 0),
              group_code: gCodeNorm,
              camera_id: cfg.camId,
              zone_id: zoneId,
              aspect_ratio: Number(state.overview.site_map?.aspect_ratio || 1.45),
            }),
          });
          if (res.spots?.length) {
            pushSiteMapUndo();
            const zoneName =
              camObj?.name ||
              (cfg.parsed ? cfg.parsed.full_cn : `${selRt.name || "定点"}车位组`);
            const createdIds = [];
            res.spots.forEach((sp) => {
              state.siteMap.draftSpots.push({
                ...sp,
                zone_name: zoneName,
                effective_status: sp.status || "UNKNOWN",
                obstacle_type: "none",
                last_reason:
                  cfg.camId === "UNASSIGNED"
                    ? "指定位置布设（待接入摄像头）"
                    : "指定位置布设",
              });
              createdIds.push(sp.spot_id);
            });
            setSelectedSpotIds(createdIds, createdIds[0] || null);
            syncSiteMapStudioControls();
            renderAbstractSvgMap(state.overview);
            return;
          }
        }
      } catch (err) {
        console.warn("Route endpoint fallback to client geometry:", err);
      }
    }

    const previewItems = window.__computeDesignatedSpotsClient(
      anchorWorld,
      endWorld,
      mode,
      modelKey
    );
    if (!previewItems.length) return;

    pushSiteMapUndo();
    const spots = state.siteMap.draftSpots || [];
    const sameGroupExisting = spots.filter((s) =>
      gCodeNorm ? s.group_code === gCodeNorm : s.camera_id === cfg.camId
    ).length;
    const zoneName =
      camObj?.name || (cfg.parsed ? cfg.parsed.full_cn : `${preset.label}`);
    const batchSuffix = Date.now().toString(36).slice(-4).toUpperCase();
    let firstCreated = null;

    const createdIds = [];
    previewItems.forEach((it, idx) => {
      const seqNum = sameGroupExisting + idx + 1;
      const labelStr = gCodeNorm
        ? `${gCodeNorm}-${String(seqNum).padStart(2, "0")}`
        : String(seqNum).padStart(2, "0");
      const newId = `SP_${(gCodeNorm || cfg.camId || "M")
        .replace(/[^A-Z0-9_]/gi, "")
        .slice(0, 8)}_${batchSuffix}_${idx + 1}`;
      const newSpot = {
        spot_id: newId,
        camera_id: cfg.camId,
        group_code: gCodeNorm,
        vehicle_model: preset.key,
        zone_id: zoneId,
        zone_name: zoneName,
        label: labelStr,
        spot_type: "standard",
        compact_only: preset.compact_only ? 1 : 0,
        capacity: 1,
        free_units: 1,
        status: cfg.camId === "UNASSIGNED" ? "UNKNOWN" : "FREE",
        effective_status: cfg.camId === "UNASSIGNED" ? "UNKNOWN" : "FREE",
        obstacle_type: "none",
        last_reason:
          cfg.camId === "UNASSIGNED"
            ? "拖拽/定点摆位完成（待绑定摄像头）"
            : `定点布设 · ${preset.label}`,
        map_x: metrics.nx(it.wx),
        map_y: metrics.ny(it.wy),
        map_w: it.w,
        map_h: it.h,
        map_angle: it.angle,
        map_custom: 1,
      };
      spots.push(newSpot);
      createdIds.push(newSpot.spot_id);
      if (!firstCreated) firstCreated = newSpot;
    });

    if (createdIds.length) {
      setSelectedSpotIds(createdIds, createdIds[0]);
    }
    if (state.siteMap.subTool !== "spots" && mode !== "routes") {
      setSiteMapSubTool("spots");
    } else {
      syncSiteMapStudioControls();
      renderAbstractSvgMap(state.overview);
    }
  };

  const createDraftVehicleSpotAt = (modelKey, worldPt = null) => {
    if (worldPt) {
      commitDesignatedSpots({
        mode: "palette",
        modelKey,
        anchorWorld: worldPt,
        endWorld: null,
      });
      return;
    }
    if (!state.overview) return;
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
    const metrics = getSiteMapMetrics(state.overview.site_map);
    const spots = state.siteMap.draftSpots || [];
    const selSpots = getSelectedSpots();
    const g = getActiveGroupSpots();
    const ref =
      (selSpots.length > 0
        ? g[g.length - 1] || selSpots[selSpots.length - 1]
        : null);
    const preset =
      VEHICLE_MODELS_CLIENT[ref?.vehicle_model || modelKey] || VEHICLE_MODELS_CLIENT.sedan;
    if (!ref) {
      // No active group selected -> place a brand-new independent group at canvas center
      commitDesignatedSpots({
        mode: "palette",
        modelKey,
        anchorWorld: [metrics.worldW * 0.5, metrics.worldH * 0.5],
        endWorld: null,
      });
      return;
    }

    // Active group selected -> append 1 stall directly onto THIS group ("＋本组加一位")
    pushSiteMapUndo();
    const w = Number(ref.map_w || preset.w);
    const h = Number(ref.map_h || preset.h);
    const angle = Number(ref.map_angle || 0);
    const step = w + 0.42;
    const rad = (angle * Math.PI) / 180;
    const sx = Math.min(metrics.worldW - 1.5, Math.max(1.5, metrics.px(ref.map_x) + Math.cos(rad) * step));
    const sy = Math.min(metrics.worldH - 1.5, Math.max(1.5, metrics.py(ref.map_y) + Math.sin(rad) * step));
    const gCodeNorm = ref.group_code || allocateNextGroupCode(spots);
    if (!ref.group_code && (!ref.camera_id || ref.camera_id === "UNASSIGNED")) {
      g.forEach((s) => {
        s.group_code = gCodeNorm;
      });
    }
    const seqNum = g.length + 1;
    const labelStr = gCodeNorm
      ? `${gCodeNorm}-${String(seqNum).padStart(2, "0")}`
      : String(seqNum).padStart(2, "0");
    const batchSuffix = Date.now().toString(36).slice(-4).toUpperCase();
    const newId = `SP_${(gCodeNorm || ref.camera_id || "M")
      .replace(/[^A-Z0-9_]/gi, "")
      .slice(0, 8)}_${batchSuffix}_A`;
    const newSpot = {
      spot_id: newId,
      camera_id: ref.camera_id || "UNASSIGNED",
      group_code: gCodeNorm,
      vehicle_model: ref.vehicle_model || preset.key,
      zone_id: ref.zone_id || `Z_GRP_${gCodeNorm}`,
      zone_name: ref.zone_name || preset.label,
      label: labelStr,
      spot_type: "standard",
      compact_only: preset.compact_only ? 1 : 0,
      capacity: 1,
      free_units: 1,
      status: (ref.camera_id || "UNASSIGNED") === "UNASSIGNED" ? "UNKNOWN" : "FREE",
      effective_status: (ref.camera_id || "UNASSIGNED") === "UNASSIGNED" ? "UNKNOWN" : "FREE",
      obstacle_type: "none",
      last_reason: "本组追加车位",
      map_x: metrics.nx(sx),
      map_y: metrics.ny(sy),
      map_w: w,
      map_h: h,
      map_angle: angle,
      map_custom: 1,
    };
    spots.push(newSpot);
    const updatedGroupIds = [...g.map((s) => s.spot_id), newSpot.spot_id];
    setSelectedSpotIds(updatedGroupIds, newSpot.spot_id);
    syncSiteMapStudioControls();
    renderAbstractSvgMap(state.overview);
  };

  // Floating drag pill helper for reliable cross-pane pointer dragging of .vmodel-chip
  const getOrCreateFloatingVmodelGhost = () => {
    let el = document.getElementById("floatingVmodelDragGhost");
    if (!el) {
      el = document.createElement("div");
      el.id = "floatingVmodelDragGhost";
      el.className = "floating-vmodel-drag-ghost hidden";
      document.body.appendChild(el);
    }
    return el;
  };

  document.querySelectorAll(".vmodel-chip").forEach((chip) => {
    chip.addEventListener("dragstart", (evt) => {
      // Prevent native HTML5 button-drag quirks so smooth universal pointer-drag works with real-scale SVG preview!
      evt.preventDefault();
    });

    chip.addEventListener("mousedown", (evt) => {
      if (evt.button !== 0 || !state.overview) return;
      evt.preventDefault();
      const mKey = chip.dataset.vmodel || "sedan";
      const preset = VEHICLE_MODELS_CLIENT[mKey] || VEHICLE_MODELS_CLIENT.sedan;
      state.siteMap.dragging = {
        kind: "palette-vmodel-drag",
        modelKey: mKey,
        label: preset.label,
        shiftKey: evt.shiftKey,
        altKey: evt.altKey,
        startClientX: evt.clientX,
        startClientY: evt.clientY,
        moved: false,
        hoverSvg: null,
        hoverWorld: null,
      };
    });
  });

  document.getElementById("btnToggleSpotPlaceMode")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
    if (state.siteMap.placeMode === "spots") {
      setDesignatedPlaceMode(null);
      syncSiteMapStudioControls();
    } else {
      // Starting a new designated group cleanly finishes/deselects any previous group!
      finishCurrentSpotGroup({ armNextPlace: true });
    }
  });

  const flipPlaceDirection = () => {
    state.siteMap.placeReverseDir = !state.siteMap.placeReverseDir;
    const txt = state.siteMap.placeReverseDir ? "⇄ 已反向(R)" : "⇄ 换向(R)";
    const b1 = document.getElementById("btnFlipSpotPlaceDir");
    const b2 = document.getElementById("btnFlipRoutePlaceDir");
    if (b1) b1.textContent = txt;
    if (b2) b2.textContent = txt;
    if (state.siteMap.placeMode && state.siteMap.syncCursorWorld) {
      updateSpotPlacePreviewDom(state.siteMap.syncCursorWorld, null, state.siteMap.placeMode);
    }
  };
  document.getElementById("btnFlipSpotPlaceDir")?.addEventListener("click", flipPlaceDirection);
  document.getElementById("btnFlipRoutePlaceDir")?.addEventListener("click", flipPlaceDirection);

  svg.addEventListener("dragover", (evt) => {
    evt.preventDefault();
    if (evt.dataTransfer) evt.dataTransfer.dropEffect = "copy";
  });

  svg.addEventListener("drop", (evt) => {
    const mKey =
      evt.dataTransfer?.getData("text/vmodel") || evt.dataTransfer?.getData("text/plain");
    if (!mKey || !VEHICLE_MODELS_CLIENT[mKey]) return;
    evt.preventDefault();
    const worldPt = clientToSiteMapWorld(svg, evt);
    createDraftVehicleSpotAt(mKey, worldPt);
  });

  document.getElementById("mapSpotLabelInput")?.addEventListener("input", (e) => {
    const sp = (state.siteMap.draftSpots || []).find(
      (s) => s.spot_id === state.siteMap.selectedSpotId
    );
    if (!sp) return;
    sp.label = e.target.value.trim() || sp.label;
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapAddSpot")?.addEventListener("click", () => {
    createDraftVehicleSpotAt(state.siteMap.paletteModel || "sedan", null);
  });

  const deleteSelectedMapSpot = () => {
    const spots = state.siteMap.draftSpots || [];
    const selSpots = getSelectedSpots();
    if (!selSpots.length) return;
    const delSet = new Set(selSpots.map((s) => s.spot_id));
    pushSiteMapUndo();
    for (let i = spots.length - 1; i >= 0; i--) {
      if (delSet.has(spots[i].spot_id)) {
        const [removed] = spots.splice(i, 1);
        if (removed?.spot_id && !state.siteMap.deletedSpotIds.includes(removed.spot_id)) {
          state.siteMap.deletedSpotIds.push(removed.spot_id);
        }
      }
    }
    const nextSpot = spots[spots.length - 1] || null;
    setSelectedSpotIds(nextSpot ? [nextSpot.spot_id] : [], nextSpot?.spot_id || null);
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  };
  document.getElementById("btnMapDeleteSpot")?.addEventListener("click", deleteSelectedMapSpot);

  // Driving Route Planner sub-tool actions
  const finishDrawingRoute = () => {
    if (!state.siteMap.drawingRoute) return;
    if (state.siteMap.newRoutePts.length >= 2) {
      pushSiteMapUndo();
      if (!Array.isArray(state.siteMap.draftRoutes)) state.siteMap.draftRoutes = [];
      const nextIdx = state.siteMap.draftRoutes.length + 1;
      state.siteMap.draftRoutes.push({
        route_id: `RT_${Date.now().toString(36).slice(-4).toUpperCase()}`,
        name: `行车路线 #${nextIdx}`,
        one_way: document.getElementById("mapRouteOneWaySelect")?.value !== "0",
        points: state.siteMap.newRoutePts.map(([x, y]) => [+x.toFixed(2), +y.toFixed(2)]),
      });
      state.siteMap.selectedRouteIdx = state.siteMap.draftRoutes.length - 1;
      state.siteMap.selectedRoutePtIdx = -1;
    }
    state.siteMap.drawingRoute = false;
    state.siteMap.newRoutePts = [];
    document.getElementById("btnMapFinishRoute")?.classList.add("hidden");
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  };

  document.getElementById("btnMapNewRoute")?.addEventListener("click", () => {
    state.siteMap.drawingRoute = true;
    state.siteMap.newRoutePts = [];
    document.getElementById("btnMapFinishRoute")?.classList.remove("hidden");
    const hintEl = document.getElementById("mapStudioHint");
    if (hintEl) {
      hintEl.textContent =
        "正在绘制行车路线：请按车辆从小区入口驶入的行进顺序依次点击节点，双击或点击『✓ 完成路线』结束（Esc 取消）";
    }
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapFinishRoute")?.addEventListener("click", finishDrawingRoute);

  document.getElementById("mapRouteSelect")?.addEventListener("change", (e) => {
    state.siteMap.selectedRouteIdx = Number(e.target.value);
    state.siteMap.selectedRoutePtIdx = -1;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("mapRouteNameInput")?.addEventListener("input", (e) => {
    const rt = (state.siteMap.draftRoutes || [])[state.siteMap.selectedRouteIdx];
    if (!rt) return;
    rt.name = e.target.value;
  });

  document.getElementById("mapRouteOneWaySelect")?.addEventListener("change", (e) => {
    const rt = (state.siteMap.draftRoutes || [])[state.siteMap.selectedRouteIdx];
    if (!rt) return;
    pushSiteMapUndo();
    rt.one_way = e.target.value !== "0";
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapReverseRoute")?.addEventListener("click", () => {
    const rt = (state.siteMap.draftRoutes || [])[state.siteMap.selectedRouteIdx];
    if (!rt || !Array.isArray(rt.points) || rt.points.length < 2) return;
    pushSiteMapUndo();
    rt.points.reverse();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapDeleteRoutePt")?.addEventListener("click", () => {
    const rt = (state.siteMap.draftRoutes || [])[state.siteMap.selectedRouteIdx];
    const pIdx = state.siteMap.selectedRoutePtIdx;
    if (!rt || !Array.isArray(rt.points) || rt.points.length <= 2 || pIdx < 0 || pIdx >= rt.points.length) {
      return;
    }
    pushSiteMapUndo();
    rt.points.splice(pIdx, 1);
    state.siteMap.selectedRoutePtIdx = Math.min(pIdx, rt.points.length - 1);
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapDeleteRoute")?.addEventListener("click", () => {
    const routes = state.siteMap.draftRoutes || [];
    const rIdx = state.siteMap.selectedRouteIdx;
    if (rIdx < 0 || rIdx >= routes.length) return;
    pushSiteMapUndo();
    routes.splice(rIdx, 1);
    state.siteMap.selectedRouteIdx = routes.length ? Math.min(rIdx, routes.length - 1) : -1;
    state.siteMap.selectedRoutePtIdx = -1;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  // Live auto-parse of route generator group code (e.g. 5LV7 -> count=7, model=sedan; 10BH2 -> count=2, model=parallel)
  document.getElementById("routeGenGroupCode")?.addEventListener("input", (e) => {
    const parsed = parseSingleGroupTokenClient(e.target.value);
    if (!parsed) return;
    const cntEl = document.getElementById("routeGenCount");
    const modEl = document.getElementById("routeGenModelSelect");
    const sideEl = document.getElementById("routeGenSideSelect");
    if (cntEl) cntEl.value = String(parsed.count);
    if (modEl) modEl.value = parsed.default_model;
    if (sideEl && (parsed.direction_code === "L" || parsed.direction_code === "R")) {
      sideEl.value = parsed.direction_code === "L" ? "left" : "right";
    }
  });

  document.getElementById("btnPickRoutePlaceOnMap")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) enterSiteMapEditMode();
    if (state.siteMap.subTool !== "roads") setSiteMapSubTool("roads");
    setDesignatedPlaceMode(state.siteMap.placeMode === "roads" ? null : "roads");
  });

  // Helper: magnetic snap for 2F walkways onto 2F roads, existing walkway nodes/segments, and unit doors
  const snapWalkwayPointToNetwork = (mx, my, excludeWIdx = -1, excludePtIdx = -1) => {
    const snapEnabled = document.getElementById("chkWalkwaySnap")?.checked !== false;
    const clamped = [
      +Math.min(99.2, Math.max(0.8, mx)).toFixed(2),
      +Math.min(99.2, Math.max(0.8, my)).toFixed(2),
    ];
    if (!snapEnabled) return { pt: clamped, snapped: false, kind: null };

    let best = { pt: clamped, dist: 2.35, snapped: false, kind: null };
    const considerPoint = (px, py, kind, tol = 2.35) => {
      const d = Math.hypot(mx - px, my - py);
      if (d <= tol && d < best.dist) {
        best = { pt: [+px.toFixed(2), +py.toFixed(2)], dist: d, snapped: true, kind };
      }
    };
    const considerSegments = (polys, kind, skipIdx = -1, skipPt = -1) => {
      (polys || []).forEach((poly, idx) => {
        const pts = poly?.points || [];
        pts.forEach((p, pIdx) => {
          if (idx === skipIdx && pIdx === skipPt) return;
          considerPoint(Number(p[0]), Number(p[1]), `${kind}-node`, 2.45);
        });
        for (let i = 0; i < pts.length - 1; i++) {
          if (idx === skipIdx && (i === skipPt || i + 1 === skipPt)) continue;
          const x1 = Number(pts[i][0]);
          const y1 = Number(pts[i][1]);
          const x2 = Number(pts[i + 1][0]);
          const y2 = Number(pts[i + 1][1]);
          const dx = x2 - x1;
          const dy = y2 - y1;
          const len2 = dx * dx + dy * dy;
          if (len2 < 0.001) continue;
          const t = Math.max(0, Math.min(1, ((mx - x1) * dx + (my - y1) * dy) / len2));
          considerPoint(x1 + t * dx, y1 + t * dy, `${kind}-seg`, 2.05);
        }
      });
    };

    // Snap highest priority to unit entrance doors, then roads & walkway intersections
    (state.siteMap.draftUnits || []).forEach((u) => {
      considerPoint(
        Number(u.door_x ?? u.map_x ?? 50),
        Number(u.door_y ?? u.map_y ?? 50),
        "door",
        2.55
      );
    });
    considerSegments(state.siteMap.draftWalkways || [], "walkway", excludeWIdx, excludePtIdx);
    considerSegments(state.siteMap.draftRoads || [], "road", -1, -1);
    return best;
  };

  // Unified 2F Road (+ Direction + Along-Road Placement) sub-tool actions
  const finishDrawingRoad = () => {
    if (!state.siteMap.drawingRoad) return;
    if (state.siteMap.newRoadPts.length >= 2) {
      pushSiteMapUndo();
      const idx = (state.siteMap.draftRoads || []).length + 1;
      const isOneWay = document.getElementById("mapRoadOneWaySelect")?.value !== "0";
      state.siteMap.draftRoads.push({
        road_id: `R_${Date.now().toString(36).slice(-4).toUpperCase()}`,
        name: `2F 车行道 #${idx}`,
        width: 4.4,
        one_way: isOneWay,
        direction: isOneWay ? "oneway" : "twoway",
        points: state.siteMap.newRoadPts.map(([x, y]) => [+x.toFixed(2), +y.toFixed(2)]),
      });
      state.siteMap.selectedRoadIdx = state.siteMap.draftRoads.length - 1;
      state.siteMap.selectedRoadPtIdx = -1;
    }
    state.siteMap.drawingRoad = false;
    state.siteMap.newRoadPts = [];
    document.getElementById("btnMapFinishRoad")?.classList.add("hidden");
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  };

  document.getElementById("btnMapNewRoad")?.addEventListener("click", () => {
    setDesignatedPlaceMode(null);
    state.siteMap.drawingRoad = true;
    state.siteMap.newRoadPts = [];
    document.getElementById("btnMapFinishRoad")?.classList.remove("hidden");
    const hintEl = document.getElementById("mapStudioHint");
    if (hintEl) {
      hintEl.textContent =
        "正在绘制 2F 车行道：按行车顺向依次点击折点（自动带方向箭头），双击或点击『✓ 完成』结束（Esc 取消）";
    }
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapFinishRoad")?.addEventListener("click", finishDrawingRoad);

  document.getElementById("mapRoadSelect")?.addEventListener("change", (e) => {
    const idx = Number(e.target.value);
    if (idx >= 0) {
      state.siteMap.selectedRoadIdx = idx;
      state.siteMap.selectedRoadPtIdx = -1;
      syncSiteMapStudioControls();
      if (state.overview) renderAbstractSvgMap(state.overview);
    }
  });

  document.getElementById("mapRoadNameInput")?.addEventListener("input", (e) => {
    const r = (state.siteMap.draftRoads || [])[state.siteMap.selectedRoadIdx];
    if (!r) return;
    r.name = e.target.value;
    syncSiteMapStudioControls();
  });

  document.getElementById("mapRoadOneWaySelect")?.addEventListener("change", (e) => {
    const r = (state.siteMap.draftRoads || [])[state.siteMap.selectedRoadIdx];
    if (!r) return;
    pushSiteMapUndo();
    r.one_way = e.target.value !== "0";
    r.direction = r.one_way ? "oneway" : "twoway";
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapReverseRoad")?.addEventListener("click", () => {
    const r = (state.siteMap.draftRoads || [])[state.siteMap.selectedRoadIdx];
    if (!r || !Array.isArray(r.points) || r.points.length < 2) return;
    pushSiteMapUndo();
    r.points.reverse();
    state.siteMap.selectedRoadPtIdx = -1;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("mapRoadWidthSlider")?.addEventListener("input", (e) => {
    const r = (state.siteMap.draftRoads || [])[state.siteMap.selectedRoadIdx];
    if (!r) return;
    r.width = +(Number(e.target.value) / 10).toFixed(1);
    const valEl = document.getElementById("mapRoadWidthVal");
    if (valEl) valEl.textContent = r.width.toFixed(1);
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapDeleteRoadPt")?.addEventListener("click", () => {
    const r = (state.siteMap.draftRoads || [])[state.siteMap.selectedRoadIdx];
    const pIdx = state.siteMap.selectedRoadPtIdx;
    if (!r || !Array.isArray(r.points) || r.points.length <= 2 || pIdx < 0 || pIdx >= r.points.length) {
      return;
    }
    pushSiteMapUndo();
    r.points.splice(pIdx, 1);
    state.siteMap.selectedRoadPtIdx = Math.min(pIdx, r.points.length - 1);
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapDeleteRoad")?.addEventListener("click", () => {
    const roads = state.siteMap.draftRoads || [];
    const rIdx = state.siteMap.selectedRoadIdx;
    if (rIdx < 0 || rIdx >= roads.length) return;
    pushSiteMapUndo();
    roads.splice(rIdx, 1);
    state.siteMap.selectedRoadIdx = roads.length ? Math.min(rIdx, roads.length - 1) : -1;
    state.siteMap.selectedRoadPtIdx = -1;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  // 2F Pedestrian Walkway & Scale Calibration sub-tool actions
  const finishDrawingWalkway = () => {
    if (!state.siteMap.drawingWalkway) return;
    if (state.siteMap.newWalkwayPts.length >= 2) {
      pushSiteMapUndo();
      if (!Array.isArray(state.siteMap.draftWalkways)) state.siteMap.draftWalkways = [];
      const idx = state.siteMap.draftWalkways.length + 1;
      state.siteMap.draftWalkways.push({
        walkway_id: `WK_${Date.now().toString(36).slice(-4).toUpperCase()}`,
        name: `2F 内部步道 #${idx}`,
        width: 2.0,
        points: state.siteMap.newWalkwayPts.map(([x, y]) => [+x.toFixed(2), +y.toFixed(2)]),
      });
      state.siteMap.selectedWalkwayIdx = state.siteMap.draftWalkways.length - 1;
      state.siteMap.selectedWalkwayPtIdx = -1;
    }
    state.siteMap.drawingWalkway = false;
    state.siteMap.newWalkwayPts = [];
    state.siteMap.walkwaySnapTarget = null;
    document.getElementById("btnMapFinishWalkway")?.classList.add("hidden");
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  };

  document.getElementById("btnMapNewWalkway")?.addEventListener("click", () => {
    setDesignatedPlaceMode(null);
    state.siteMap.drawingWalkway = true;
    state.siteMap.newWalkwayPts = [];
    state.siteMap.walkwaySnapTarget = null;
    document.getElementById("btnMapFinishWalkway")?.classList.remove("hidden");
    const hintEl = document.getElementById("mapStudioHint");
    if (hintEl) {
      hintEl.textContent =
        "正在绘制 2F 人行步道：移近外围车行道、已有步道或 🚪 单元门会自动磁吸连通；双击或点『✓ 完成』结束（Esc 取消）";
    }
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapFinishWalkway")?.addEventListener("click", finishDrawingWalkway);

  document.getElementById("mapWalkwaySelect")?.addEventListener("change", (e) => {
    const idx = Number(e.target.value);
    if (idx >= 0) {
      state.siteMap.selectedWalkwayIdx = idx;
      state.siteMap.selectedWalkwayPtIdx = -1;
      syncSiteMapStudioControls();
      if (state.overview) renderAbstractSvgMap(state.overview);
    }
  });

  document.getElementById("mapWalkwayNameInput")?.addEventListener("input", (e) => {
    const wk = (state.siteMap.draftWalkways || [])[state.siteMap.selectedWalkwayIdx];
    if (!wk) return;
    wk.name = e.target.value;
    syncSiteMapStudioControls();
  });

  document.getElementById("btnMapDeleteWalkwayPt")?.addEventListener("click", () => {
    const wk = (state.siteMap.draftWalkways || [])[state.siteMap.selectedWalkwayIdx];
    const pIdx = state.siteMap.selectedWalkwayPtIdx;
    if (!wk || !Array.isArray(wk.points) || wk.points.length <= 2 || pIdx < 0 || pIdx >= wk.points.length) {
      return;
    }
    pushSiteMapUndo();
    wk.points.splice(pIdx, 1);
    state.siteMap.selectedWalkwayPtIdx = Math.min(pIdx, wk.points.length - 1);
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapDeleteWalkway")?.addEventListener("click", () => {
    const walkways = state.siteMap.draftWalkways || [];
    const wIdx = state.siteMap.selectedWalkwayIdx;
    if (wIdx < 0 || wIdx >= walkways.length) return;
    pushSiteMapUndo();
    walkways.splice(wIdx, 1);
    state.siteMap.selectedWalkwayIdx = walkways.length ? Math.min(wIdx, walkways.length - 1) : -1;
    state.siteMap.selectedWalkwayPtIdx = -1;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  const applyDraftSpanMeters = (rawVal) => {
    const num = Number(rawVal);
    if (!Number.isFinite(num) || num <= 0) return;
    state.siteMap.draftSpanMeters = Math.max(40, Math.min(800, Math.round(num)));
    syncSiteMapStudioControls();
  };
  document.getElementById("mapSpanMetersInput")?.addEventListener("change", (e) => {
    applyDraftSpanMeters(e.target.value);
  });
  document.getElementById("mapSpanMetersSlider")?.addEventListener("input", (e) => {
    applyDraftSpanMeters(e.target.value);
    const inp = document.getElementById("mapSpanMetersInput");
    if (inp) inp.value = String(state.siteMap.draftSpanMeters);
  });

  // Nodes (Units & Gates) sub-tool actions
  document.getElementById("btnMapAddUnit")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) return;
    pushSiteMapUndo();
    const units = state.siteMap.draftUnits || [];
    let idx = units.length + 1;
    let uid = `${idx}-1`;
    while (units.some((u) => u.unit_id === uid)) {
      idx += 1;
      uid = `${idx}-1`;
    }
    const netLines = [
      ...(state.siteMap.draftWalkways || []),
      ...(state.siteMap.draftRoads || []),
    ];
    const [dx, dy] = projectPointToSegmentsClient(50, 50, netLines);
    units.push({
      unit_id: uid,
      name: formatAutoUnitNameClient(uid),
      default_gate: "south",
      map_x: 50,
      map_y: 50,
      map_w: 6.4,
      map_h: 2.8,
      door_x: dx,
      door_y: dy,
      door_custom: 0,
    });
    state.siteMap.selectedNodeKind = "unit";
    state.siteMap.selectedNodeId = uid;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapAddSiblingUnit")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) return;
    const units = state.siteMap.draftUnits || [];
    const curU =
      units.find((u) => u.unit_id === state.siteMap.selectedNodeId) ||
      units.find((u) => String(u.unit_id || "").startsWith("9-")) ||
      units[0];
    const m = String(curU?.unit_id || "9-1").match(/^([A-Za-z0-9]+)[-_#]?(\d*)$/);
    const bldg = m ? m[1] : "9";
    let unitNo = (m && m[2] ? parseInt(m[2], 10) : 1) + 1;
    let uid = `${bldg}-${unitNo}`;
    while (units.some((u) => u.unit_id === uid)) {
      unitNo += 1;
      uid = `${bldg}-${unitNo}`;
    }
    pushSiteMapUndo();
    const mx = Math.min(95, Math.max(4, Number(curU?.map_x || 50) + 5.8));
    const my = Math.min(95, Math.max(4, Number(curU?.map_y || 50)));
    const netLines = [
      ...(state.siteMap.draftWalkways || []),
      ...(state.siteMap.draftRoads || []),
    ];
    const [dx, dy] = projectPointToSegmentsClient(mx, my, netLines);
    units.push({
      unit_id: uid,
      name: formatAutoUnitNameClient(uid),
      default_gate: curU?.default_gate || "south",
      map_x: +mx.toFixed(2),
      map_y: +my.toFixed(2),
      map_w: 6.4,
      map_h: 2.8,
      door_x: dx,
      door_y: dy,
      door_custom: 0,
    });
    state.siteMap.selectedNodeKind = "unit";
    state.siteMap.selectedNodeId = uid;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapResetUnitDoor")?.addEventListener("click", () => {
    if (!state.siteMap.editMode || state.siteMap.selectedNodeKind !== "unit") return;
    const u = (state.siteMap.draftUnits || []).find(
      (x) => x.unit_id === state.siteMap.selectedNodeId
    );
    if (!u) return;
    pushSiteMapUndo();
    const netLines = [
      ...(state.siteMap.draftWalkways || []),
      ...(state.siteMap.draftRoads || []),
    ];
    const [dx, dy] = projectPointToSegmentsClient(
      Number(u.map_x || 50),
      Number(u.map_y || 50),
      netLines
    );
    u.door_x = dx;
    u.door_y = dy;
    u.door_custom = 0;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapAddGate")?.addEventListener("click", () => {
    if (!state.siteMap.editMode) return;
    pushSiteMapUndo();
    const gates = state.siteMap.draftGates || [];
    const gid = `gate_${Date.now().toString(36).slice(-4)}`;
    gates.push({
      gate_id: gid,
      name: "新出入口",
      x: 50,
      y: 94,
      w: 8.5,
      h: 2.8,
    });
    state.siteMap.selectedNodeKind = "gate";
    state.siteMap.selectedNodeId = gid;
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("mapNodeNameInput")?.addEventListener("change", (e) => {
    const val = e.target.value.trim();
    if (!val) return;
    pushSiteMapUndo();
    if (state.siteMap.selectedNodeKind === "unit") {
      const cleanId = val.replace(/栋$/, "");
      const u = (state.siteMap.draftUnits || []).find(
        (x) => x.unit_id === state.siteMap.selectedNodeId
      );
      if (u && cleanId) {
        if (
          cleanId !== u.unit_id &&
          !(state.siteMap.draftUnits || []).some((o) => o.unit_id === cleanId)
        ) {
          if (!state.siteMap.deletedUnitIds.includes(u.unit_id)) {
            state.siteMap.deletedUnitIds.push(u.unit_id);
          }
          u.unit_id = cleanId;
          state.siteMap.selectedNodeId = cleanId;
        }
        u.name = formatAutoUnitNameClient(u.unit_id);
      }
    } else if (state.siteMap.selectedNodeKind === "gate") {
      const g = (state.siteMap.draftGates || []).find(
        (x) => x.gate_id === state.siteMap.selectedNodeId
      );
      if (g) g.name = val;
    }
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnMapDeleteNode")?.addEventListener("click", () => {
    if (state.siteMap.selectedNodeKind === "unit") {
      const units = state.siteMap.draftUnits || [];
      if (units.length <= 1) return;
      const idx = units.findIndex((u) => u.unit_id === state.siteMap.selectedNodeId);
      if (idx >= 0) {
        pushSiteMapUndo();
        const [removed] = units.splice(idx, 1);
        if (removed?.unit_id && !state.siteMap.deletedUnitIds.includes(removed.unit_id)) {
          state.siteMap.deletedUnitIds.push(removed.unit_id);
        }
        state.siteMap.selectedNodeKind = null;
        state.siteMap.selectedNodeId = null;
      }
    } else if (state.siteMap.selectedNodeKind === "gate") {
      const gates = state.siteMap.draftGates || [];
      if (gates.length <= 1) return;
      const idx = gates.findIndex((g) => g.gate_id === state.siteMap.selectedNodeId);
      if (idx >= 0) {
        pushSiteMapUndo();
        gates.splice(idx, 1);
        state.siteMap.selectedNodeKind = null;
        state.siteMap.selectedNodeId = null;
      }
    }
    syncSiteMapStudioControls();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  // Enter / Exit / Save Edit Mode
  btnEdit.addEventListener("click", () => {
    if (state.siteMap.editMode) {
      exitSiteMapEditMode();
    } else {
      enterSiteMapEditMode();
    }
  });

  btnCancel.addEventListener("click", () => {
    exitSiteMapEditMode();
  });

  btnSave.addEventListener("click", async () => {
    if (!state.siteMap.editMode) return;
    if (state.siteMap.drawingRoad) finishDrawingRoad();
    if (state.siteMap.drawingRoute) finishDrawingRoute();
    if (state.siteMap.drawingWalkway) finishDrawingWalkway();
    btnSave.disabled = true;
    try {
      const spanM = Math.max(40, Math.min(800, Number(state.siteMap.draftSpanMeters || 185)));
      await api("/api/sitemap/layout", {
        method: "POST",
        body: JSON.stringify({
          spots: state.siteMap.draftSpots || [],
          units: state.siteMap.draftUnits || [],
          deleted_spot_ids: state.siteMap.deletedSpotIds || [],
          deleted_unit_ids: state.siteMap.deletedUnitIds || [],
          estimated_span_meters: spanM,
          topology: {
            roads: state.siteMap.draftRoads || [],
            routes: state.siteMap.draftRoutes || [],
            walkways: state.siteMap.draftWalkways || [],
            estimated_span_meters: spanM,
            gates: state.siteMap.draftGates || [],
            greens: state.siteMap.draftGreens || [],
          },
        }),
      });
      exitSiteMapEditMode(false);
      await refreshOverview();
      showAppleToast("✅ 已保存 2F 态势路网与平面微调（步行测距矩阵已自动重算）");
    } catch (err) {
      alert(`保存平面图微调失败：${err.message}`);
    } finally {
      btnSave.disabled = false;
    }
  });

  // Right-click on road, walkway, or route vertex deletes it quickly
  svg.addEventListener("contextmenu", (evt) => {
    if (!state.siteMap.editMode) return;
    const wkPtEl = evt.target.closest("[data-drag-kind='walkway-pt']");
    if (wkPtEl) {
      evt.preventDefault();
      const wIdx = Number(wkPtEl.dataset.walkwayIdx);
      const pIdx = Number(wkPtEl.dataset.ptIdx);
      const wk = (state.siteMap.draftWalkways || [])[wIdx];
      if (wk && Array.isArray(wk.points) && wk.points.length > 2) {
        pushSiteMapUndo();
        wk.points.splice(pIdx, 1);
        state.siteMap.selectedWalkwayIdx = wIdx;
        state.siteMap.selectedWalkwayPtIdx = -1;
        syncSiteMapStudioControls();
        renderAbstractSvgMap(state.overview);
      }
      return;
    }
    const rtPtEl = evt.target.closest("[data-drag-kind='route-pt']");
    if (rtPtEl) {
      evt.preventDefault();
      const rIdx = Number(rtPtEl.dataset.routeIdx);
      const pIdx = Number(rtPtEl.dataset.ptIdx);
      const rt = (state.siteMap.draftRoutes || [])[rIdx];
      if (rt && Array.isArray(rt.points) && rt.points.length > 2) {
        pushSiteMapUndo();
        rt.points.splice(pIdx, 1);
        state.siteMap.selectedRouteIdx = rIdx;
        state.siteMap.selectedRoutePtIdx = -1;
        renderAbstractSvgMap(state.overview);
      }
      return;
    }
    const ptEl = evt.target.closest("[data-drag-kind='road-pt']");
    if (!ptEl) return;
    evt.preventDefault();
    const rIdx = Number(ptEl.dataset.roadIdx);
    const pIdx = Number(ptEl.dataset.ptIdx);
    const r = (state.siteMap.draftRoads || [])[rIdx];
    if (r && Array.isArray(r.points) && r.points.length > 2) {
      pushSiteMapUndo();
      r.points.splice(pIdx, 1);
      state.siteMap.selectedRoadIdx = rIdx;
      state.siteMap.selectedRoadPtIdx = -1;
      renderAbstractSvgMap(state.overview);
    }
  });

  svg.addEventListener("dblclick", (evt) => {
    if (state.siteMap.editMode && state.siteMap.drawingWalkway) {
      evt.preventDefault();
      finishDrawingWalkway();
      return;
    }
    if (state.siteMap.editMode && state.siteMap.drawingRoute) {
      evt.preventDefault();
      finishDrawingRoute();
      return;
    }
    if (state.siteMap.editMode && state.siteMap.drawingRoad) {
      evt.preventDefault();
      finishDrawingRoad();
    }
  });

  // Unified SVG Pointer Down (Drag / Select / Pan / Draw Road)
  svg.addEventListener("mousedown", (evt) => {
    if (!state.overview) return;
    const metrics = getSiteMapMetrics(state.overview.site_map);
    const [wx, wy] = clientToSiteMapWorld(svg, evt);
    const mx = metrics.nx(wx);
    const my = metrics.ny(wy);

    const startPanDrag = () => {
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      evt.preventDefault();
      state.siteMap.dragging = {
        kind: "pan",
        button: evt.button,
        movedPx: 0,
        startClientX: evt.clientX,
        startClientY: evt.clientY,
        ctmA: ctm.a || 1,
        ctmD: ctm.d || 1,
        origCamX: state.siteMap.camCenterX ?? metrics.worldW / 2,
        origCamY: state.siteMap.camCenterY ?? metrics.worldH / 2,
      };
    };

    // Middle-click or Space+drag or Pan tool always pans
    if (
      evt.button === 1 ||
      state.siteMap.spacePressed ||
      (state.siteMap.editMode && state.siteMap.subTool === "pan")
    ) {
      startPanDrag();
      return;
    }
    if (evt.button !== 0) return;

    // Interactive camera placement mode (works seamlessly in BOTH Browse Mode & Edit Mode!)
    if (state.siteMap.pendingCameraPlacement) {
      evt.preventDefault();
      const spec = state.siteMap.pendingCameraPlacement;
      const snapGrp = findHoveredMapSpotGroup([wx, wy], metrics);
      state.siteMap.dragging = {
        kind: "pending-cam-span-drag",
        activeSvg: svg,
        spec,
        anchorWorld: [wx, wy],
        currentWorld: [wx, wy],
        snapGroup: snapGrp,
      };
      renderCameraDropPreviewDom(spec, [wx, wy], null, snapGrp);
      return;
    }

    // Browse Mode: click stall to edit tags/rental, click group badge to name/bind camera, click building/door to set destination
    if (!state.siteMap.editMode) {
      const grpTarget = evt.target.closest("[data-group-badge]");
      if (grpTarget) {
        const gCode = grpTarget.dataset.groupBadge;
        if (gCode) openGroupBindModal(gCode);
        return;
      }
      const unitTarget = evt.target.closest("[data-unit-click]");
      if (unitTarget) {
        const uid = unitTarget.dataset.unitClick;
        const sel = document.getElementById("unitSelect");
        if (sel && uid) {
          sel.value = uid;
          state.lastDoorWalkPath = null;
          updateSiriHint();
          refreshOverview();
        }
        return;
      }
      const spotTarget = evt.target.closest("[data-spot-click]");
      if (spotTarget) {
        const sid = spotTarget.dataset.spotClick;
        const additive = Boolean(evt.shiftKey || evt.metaKey || evt.ctrlKey);
        if ((state.siteMap.zoom || 1) > 1.01) {
          state.siteMap.pendingSpotTagClick = {
            spotId: sid,
            additive,
            clientX: evt.clientX,
            clientY: evt.clientY,
          };
          startPanDrag();
        } else {
          evt.preventDefault();
          handleBrowseSpotTagClick(sid, additive, evt.clientX, evt.clientY);
        }
        return;
      }
      if (state.tagPopover?.open) {
        closeSpotTagPopover();
      }
      if ((state.siteMap.zoom || 1) > 1.01) {
        startPanDrag();
      }
      return;
    }

    // Designated placement mode on Right Situational Map (click at A or drag A -> B)
    if (state.siteMap.placeMode) {
      evt.preventDefault();
      state.siteMap.dragging = {
        kind: "designated-span-drag",
        activeSvg: svg,
        mode: state.siteMap.placeMode,
        anchorWorld: [wx, wy],
        currentWorld: [wx, wy],
      };
      updateSpotPlacePreviewDom([wx, wy], null, state.siteMap.placeMode);
      return;
    }

    // 2F Pedestrian Walkway drawing click (with magnetic snap to 2F roads & unit doors)
    if (state.siteMap.subTool === "walkways" && state.siteMap.drawingWalkway) {
      evt.preventDefault();
      const snapRes = snapWalkwayPointToNetwork(mx, my);
      const [sx, sy] = snapRes.pt;
      const last = state.siteMap.newWalkwayPts[state.siteMap.newWalkwayPts.length - 1];
      if (!last || Math.hypot(sx - last[0], sy - last[1]) > 0.45) {
        state.siteMap.newWalkwayPts.push([sx, sy]);
        renderAbstractSvgMap(state.overview);
      }
      return;
    }

    // Driving Route drawing click
    if (state.siteMap.subTool === "routes" && state.siteMap.drawingRoute) {
      evt.preventDefault();
      const last = state.siteMap.newRoutePts[state.siteMap.newRoutePts.length - 1];
      if (!last || Math.hypot(mx - last[0], my - last[1]) > 0.6) {
        state.siteMap.newRoutePts.push([mx, my]);
        renderAbstractSvgMap(state.overview);
      }
      return;
    }

    // Road drawing click
    if (state.siteMap.subTool === "roads" && state.siteMap.drawingRoad) {
      evt.preventDefault();
      const last = state.siteMap.newRoadPts[state.siteMap.newRoadPts.length - 1];
      if (!last || Math.hypot(mx - last[0], my - last[1]) > 0.6) {
        state.siteMap.newRoadPts.push([mx, my]);
        renderAbstractSvgMap(state.overview);
      }
      return;
    }

    // Check interactive handles / elements
    const walkwayLineEl = evt.target.closest("[data-walkway-select]");
    const routeLineEl = evt.target.closest("[data-route-select]");
    const roadLineEl = evt.target.closest("[data-road-select]");
    const dragTarget = evt.target.closest("[data-drag-kind]");

    if (dragTarget) {
      if (startSpotOrHandleDrag(dragTarget, evt, svg, metrics, mx, my)) {
        return;
      }

      evt.preventDefault();
      const kind = dragTarget.dataset.dragKind;

      if (kind === "walkway-pt") {
        const wIdx = Number(dragTarget.dataset.walkwayIdx);
        const pIdx = Number(dragTarget.dataset.ptIdx);
        const wk = (state.siteMap.draftWalkways || [])[wIdx];
        if (!wk || !wk.points?.[pIdx]) return;
        pushSiteMapUndo();
        state.siteMap.selectedWalkwayIdx = wIdx;
        state.siteMap.selectedWalkwayPtIdx = pIdx;
        syncSiteMapStudioControls();
        state.siteMap.dragging = { kind: "walkway-pt", walkwayIdx: wIdx, ptIdx: pIdx };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "walkway-mid") {
        const wIdx = Number(dragTarget.dataset.walkwayIdx);
        const segIdx = Number(dragTarget.dataset.segIdx);
        const wk = (state.siteMap.draftWalkways || [])[wIdx];
        if (!wk || !Array.isArray(wk.points)) return;
        pushSiteMapUndo();
        wk.points.splice(segIdx + 1, 0, [mx, my]);
        state.siteMap.selectedWalkwayIdx = wIdx;
        state.siteMap.selectedWalkwayPtIdx = segIdx + 1;
        syncSiteMapStudioControls();
        state.siteMap.dragging = { kind: "walkway-pt", walkwayIdx: wIdx, ptIdx: segIdx + 1 };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "route-pt") {
        const rIdx = Number(dragTarget.dataset.routeIdx);
        const pIdx = Number(dragTarget.dataset.ptIdx);
        const rt = (state.siteMap.draftRoutes || [])[rIdx];
        if (!rt || !rt.points?.[pIdx]) return;
        pushSiteMapUndo();
        state.siteMap.selectedRouteIdx = rIdx;
        state.siteMap.selectedRoutePtIdx = pIdx;
        syncSiteMapStudioControls();
        state.siteMap.dragging = { kind: "route-pt", routeIdx: rIdx, ptIdx: pIdx };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "route-mid") {
        const rIdx = Number(dragTarget.dataset.routeIdx);
        const segIdx = Number(dragTarget.dataset.segIdx);
        const rt = (state.siteMap.draftRoutes || [])[rIdx];
        if (!rt || !Array.isArray(rt.points)) return;
        pushSiteMapUndo();
        rt.points.splice(segIdx + 1, 0, [mx, my]);
        state.siteMap.selectedRouteIdx = rIdx;
        state.siteMap.selectedRoutePtIdx = segIdx + 1;
        syncSiteMapStudioControls();
        state.siteMap.dragging = { kind: "route-pt", routeIdx: rIdx, ptIdx: segIdx + 1 };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "road-pt") {
        const rIdx = Number(dragTarget.dataset.roadIdx);
        const pIdx = Number(dragTarget.dataset.ptIdx);
        const r = (state.siteMap.draftRoads || [])[rIdx];
        if (!r || !r.points?.[pIdx]) return;
        pushSiteMapUndo();
        state.siteMap.selectedRoadIdx = rIdx;
        state.siteMap.selectedRoadPtIdx = pIdx;
        syncSiteMapStudioControls();
        state.siteMap.dragging = { kind: "road-pt", roadIdx: rIdx, ptIdx: pIdx };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "road-mid") {
        const rIdx = Number(dragTarget.dataset.roadIdx);
        const segIdx = Number(dragTarget.dataset.segIdx);
        const r = (state.siteMap.draftRoads || [])[rIdx];
        if (!r || !Array.isArray(r.points)) return;
        pushSiteMapUndo();
        r.points.splice(segIdx + 1, 0, [mx, my]);
        state.siteMap.selectedRoadIdx = rIdx;
        state.siteMap.selectedRoadPtIdx = segIdx + 1;
        syncSiteMapStudioControls();
        state.siteMap.dragging = { kind: "road-pt", roadIdx: rIdx, ptIdx: segIdx + 1 };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "unit-door") {
        const u = (state.siteMap.draftUnits || []).find(
          (x) => x.unit_id === dragTarget.dataset.dragId
        );
        if (!u) return;
        pushSiteMapUndo();
        state.siteMap.selectedNodeKind = "unit";
        state.siteMap.selectedNodeId = u.unit_id;
        syncSiteMapStudioControls();
        state.siteMap.dragging = {
          kind: "unit-door",
          unit: u,
          startX: mx,
          startY: my,
          origX: Number(u.door_x ?? u.map_x ?? 50),
          origY: Number(u.door_y ?? u.map_y ?? 50),
        };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "unit") {
        const u = (state.siteMap.draftUnits || []).find(
          (x) => x.unit_id === dragTarget.dataset.dragId
        );
        if (!u) return;
        pushSiteMapUndo();
        state.siteMap.selectedNodeKind = "unit";
        state.siteMap.selectedNodeId = u.unit_id;
        syncSiteMapStudioControls();
        state.siteMap.dragging = {
          kind: "unit",
          unit: u,
          startX: mx,
          startY: my,
          origX: Number(u.map_x),
          origY: Number(u.map_y),
          origDoorX: Number(u.door_x ?? u.map_x ?? 50),
          origDoorY: Number(u.door_y ?? u.map_y ?? 50),
        };
        renderAbstractSvgMap(state.overview);
        return;
      }

      if (kind === "gate") {
        const g = (state.siteMap.draftGates || []).find(
          (x) => x.gate_id === dragTarget.dataset.dragId
        );
        if (!g) return;
        pushSiteMapUndo();
        state.siteMap.selectedNodeKind = "gate";
        state.siteMap.selectedNodeId = g.gate_id;
        syncSiteMapStudioControls();
        state.siteMap.dragging = {
          kind: "gate",
          gate: g,
          startX: mx,
          startY: my,
          origX: Number(g.x),
          origY: Number(g.y),
        };
        renderAbstractSvgMap(state.overview);
        return;
      }
    }

    if (walkwayLineEl && state.siteMap.subTool === "walkways") {
      state.siteMap.selectedWalkwayIdx = Number(walkwayLineEl.dataset.walkwaySelect);
      state.siteMap.selectedWalkwayPtIdx = -1;
      syncSiteMapStudioControls();
      renderAbstractSvgMap(state.overview);
      return;
    }

    if (routeLineEl && state.siteMap.subTool === "routes") {
      state.siteMap.selectedRouteIdx = Number(routeLineEl.dataset.routeSelect);
      state.siteMap.selectedRoutePtIdx = -1;
      syncSiteMapStudioControls();
      renderAbstractSvgMap(state.overview);
      return;
    }

    if (roadLineEl && state.siteMap.subTool === "roads") {
      state.siteMap.selectedRoadIdx = Number(roadLineEl.dataset.roadSelect);
      state.siteMap.selectedRoadPtIdx = -1;
      syncSiteMapStudioControls();
      renderAbstractSvgMap(state.overview);
      return;
    }

    // Empty background drag in spots mode starts rubber-band marquee multi-car selection!
    if (
      state.siteMap.editMode &&
      state.siteMap.subTool === "spots" &&
      state.siteMap.marqueeMode !== false
    ) {
      evt.preventDefault();
      state.siteMap.dragging = {
        kind: "marquee-select",
        activeSvg: svg,
        additive: Boolean(evt.shiftKey || evt.metaKey || evt.ctrlKey),
        baseIds: getSelectedSpots().map((s) => s.spot_id),
        startWorld: [wx, wy],
        curWorld: [wx, wy],
        startClientX: evt.clientX,
        startClientY: evt.clientY,
        movedPx: 0,
      };
      return;
    }

    // Empty background drag pans smoothly
    startPanDrag();
  });

  window.addEventListener("mousemove", (evt) => {
    if (!state.overview) return;
    const metrics = getSiteMapMetrics(state.overview.site_map);

    // Live hover badge & interactive camera placement preview when not dragging
    if (!state.siteMap.dragging) {
      if (state.siteMap.pendingCameraPlacement) {
        let hitSvg = null;
        if (refSvg && state.siteMap.compareSplit) {
          const rRect = refSvg.getBoundingClientRect();
          if (
            rRect.width > 5 &&
            evt.clientX >= rRect.left &&
            evt.clientX <= rRect.right &&
            evt.clientY >= rRect.top &&
            evt.clientY <= rRect.bottom
          ) {
            hitSvg = refSvg;
          }
        }
        if (!hitSvg) {
          const mRect = svg.getBoundingClientRect();
          if (
            mRect.width > 5 &&
            evt.clientX >= mRect.left &&
            evt.clientX <= mRect.right &&
            evt.clientY >= mRect.top &&
            evt.clientY <= mRect.bottom
          ) {
            hitSvg = svg;
          }
        }
        if (hitSvg) {
          const wPt = clientToSiteMapWorld(hitSvg, evt);
          state.siteMap.cursorWorld = wPt;
          const snapGrp = findHoveredMapSpotGroup(wPt, metrics);
          renderCameraDropPreviewDom(state.siteMap.pendingCameraPlacement, wPt, null, snapGrp);
        } else {
          clearCameraDropPreviewDom();
        }
      }
      if (
        state.siteMap.editMode &&
        (state.siteMap.drawingRoad ||
          state.siteMap.drawingRoute ||
          state.siteMap.drawingWalkway)
      ) {
        const [wx, wy] = clientToSiteMapWorld(svg, evt);
        if (state.siteMap.drawingWalkway) {
          const snapRes = snapWalkwayPointToNetwork(metrics.nx(wx), metrics.ny(wy));
          state.siteMap.walkwaySnapTarget = snapRes.snapped ? snapRes : null;
          state.siteMap.cursorWorld = [metrics.px(snapRes.pt[0]), metrics.py(snapRes.pt[1])];
        } else {
          state.siteMap.walkwaySnapTarget = null;
          state.siteMap.cursorWorld = [wx, wy];
        }
        renderAbstractSvgMap(state.overview);
      }
      if (hoverBadge) {
        const spotEl = evt.target?.closest?.("[data-spot-tip]");
        if (spotEl) {
          hoverBadge.textContent = spotEl.dataset.spotTip;
          hoverBadge.classList.remove("hidden");
        } else {
          hoverBadge.classList.add("hidden");
        }
      }
      return;
    }

    const d = state.siteMap.dragging;
    if (d.kind === "pan") {
      const dxPx = evt.clientX - d.startClientX;
      const dyPx = evt.clientY - d.startClientY;
      d.movedPx = Math.max(d.movedPx || 0, Math.hypot(dxPx, dyPx));
      const dxWorld = dxPx / d.ctmA;
      const dyWorld = dyPx / d.ctmD;
      state.siteMap.camCenterX = d.origCamX - dxWorld;
      state.siteMap.camCenterY = d.origCamY - dyWorld;
      applySiteMapViewBox(svg, metrics.worldW, metrics.worldH);
      return;
    }

    if (d.kind === "unbound-cam-drag") {
      const moveDist = Math.hypot(evt.clientX - d.startClientX, evt.clientY - d.startClientY);
      if (moveDist > 5) {
        d.moved = true;
        const ghostEl = getOrCreateFloatingVmodelGhost();
        let hitSvg = null;
        if (refSvg && state.siteMap.compareSplit) {
          const rRect = refSvg.getBoundingClientRect();
          if (
            rRect.width > 5 &&
            evt.clientX >= rRect.left &&
            evt.clientX <= rRect.right &&
            evt.clientY >= rRect.top &&
            evt.clientY <= rRect.bottom
          ) {
            hitSvg = refSvg;
          }
        }
        if (!hitSvg) {
          const mRect = svg.getBoundingClientRect();
          if (
            mRect.width > 5 &&
            evt.clientX >= mRect.left &&
            evt.clientX <= mRect.right &&
            evt.clientY >= mRect.top &&
            evt.clientY <= mRect.bottom
          ) {
            hitSvg = svg;
          }
        }

        if (hitSvg) {
          const wPt = clientToSiteMapWorld(hitSvg, evt);
          const snapGrp = findHoveredMapSpotGroup(wPt, metrics);
          d.hoverSvg = hitSvg;
          d.hoverWorld = wPt;
          d.snapGroup = snapGrp;
          ghostEl.textContent = snapGrp
            ? `🔗 松手关联「${d.spec.baseCode}」→ 平面组 ${snapGrp.group_code}（按实标 ${d.spec.count} 位对齐）`
            : `✨ 松手在此自动绘制并绑定「${d.spec.baseCode}」（${
                d.spec.orientation === "horizontal" ? "横向" : "纵向"
              } ${d.spec.count} 车位）`;
          renderCameraDropPreviewDom(d.spec, wPt, null, snapGrp);
        } else {
          d.hoverSvg = null;
          d.hoverWorld = null;
          d.snapGroup = null;
          ghostEl.textContent = `📷 拖动「${d.spec.baseCode}」到下方态势平面图空白处自动绘车，或落到已有车位组上关联`;
          clearCameraDropPreviewDom();
        }
        ghostEl.style.left = `${evt.clientX}px`;
        ghostEl.style.top = `${evt.clientY}px`;
        ghostEl.classList.remove("hidden");
      }
      return;
    }

    if (d.kind === "pending-cam-span-drag") {
      const targetSvg = d.activeSvg || svg;
      const wPt = clientToSiteMapWorld(targetSvg, evt);
      d.currentWorld = wPt;
      const dist = Math.hypot(wPt[0] - d.anchorWorld[0], wPt[1] - d.anchorWorld[1]);
      if (dist >= 1.2) {
        d.snapGroup = null;
        renderCameraDropPreviewDom(d.spec, d.anchorWorld, wPt, null);
      } else {
        renderCameraDropPreviewDom(d.spec, d.anchorWorld, null, d.snapGroup);
      }
      return;
    }

    if (d.kind === "palette-vmodel-drag") {
      const moveDist = Math.hypot(evt.clientX - d.startClientX, evt.clientY - d.startClientY);
      if (moveDist > 4) {
        if (!d.moved) {
          d.moved = true;
          state.siteMap.paletteModel = d.modelKey;
          if (!state.siteMap.editMode) enterSiteMapEditMode();
          if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
        }
        const ghostEl = getOrCreateFloatingVmodelGhost();
        const curN = Math.max(
          1,
          Math.min(40, Number(document.getElementById("spotPlaceCountInput")?.value || 1))
        );
        ghostEl.textContent = `🚗 ${d.label} × ${curN}辆 · 滚轮增减 · 松手新建独立组`;
        ghostEl.style.left = `${evt.clientX}px`;
        ghostEl.style.top = `${evt.clientY}px`;
        ghostEl.classList.remove("hidden");

        let hitSvg = null;
        if (refSvg && state.siteMap.compareSplit) {
          const rRect = refSvg.getBoundingClientRect();
          if (
            rRect.width > 5 &&
            evt.clientX >= rRect.left &&
            evt.clientX <= rRect.right &&
            evt.clientY >= rRect.top &&
            evt.clientY <= rRect.bottom
          ) {
            hitSvg = refSvg;
          }
        }
        if (!hitSvg) {
          const mRect = svg.getBoundingClientRect();
          if (
            mRect.width > 5 &&
            evt.clientX >= mRect.left &&
            evt.clientX <= mRect.right &&
            evt.clientY >= mRect.top &&
            evt.clientY <= mRect.bottom
          ) {
            hitSvg = svg;
          }
        }

        if (hitSvg) {
          const wPt = clientToSiteMapWorld(hitSvg, evt);
          d.hoverSvg = hitSvg;
          d.hoverWorld = wPt;
          state.siteMap.syncCursorWorld = wPt;
          updateSyncCrosshairDom(wPt);
          updateSpotPlacePreviewDom(wPt, null, "palette", d.modelKey);
        } else {
          d.hoverSvg = null;
          d.hoverWorld = null;
          updateSpotPlacePreviewDom(null);
        }
      }
      return;
    }

    if (d.kind === "designated-span-drag") {
      const targetSvg = d.activeSvg || svg;
      const wPt = clientToSiteMapWorld(targetSvg, evt);
      d.currentWorld = wPt;
      state.siteMap.syncCursorWorld = wPt;
      updateSyncCrosshairDom(wPt);
      const dist = Math.hypot(wPt[0] - d.anchorWorld[0], wPt[1] - d.anchorWorld[1]);
      updateSpotPlacePreviewDom(d.anchorWorld, dist >= 1.0 ? wPt : null, d.mode);
      return;
    }

    if (d.kind === "marquee-select") {
      const targetSvg = d.activeSvg || svg;
      const wPt = clientToSiteMapWorld(targetSvg, evt);
      d.curWorld = wPt;
      if (state.siteMap.compareSplit) {
        state.siteMap.syncCursorWorld = wPt;
        updateSyncCrosshairDom(wPt);
      }
      d.movedPx = Math.max(
        d.movedPx || 0,
        Math.hypot(evt.clientX - d.startClientX, evt.clientY - d.startClientY)
      );
      if (d.movedPx >= 3) {
        const minWx = Math.min(d.startWorld[0], wPt[0]);
        const maxWx = Math.max(d.startWorld[0], wPt[0]);
        const minWy = Math.min(d.startWorld[1], wPt[1]);
        const maxWy = Math.max(d.startWorld[1], wPt[1]);
        const hits = (state.siteMap.draftSpots || []).filter((sp) =>
          spotIntersectsWorldBox(sp, minWx, minWy, maxWx, maxWy, metrics)
        );
        updateMarqueeBoxDom([minWx, minWy, maxWx, maxWy], hits.length);
      }
      return;
    }

    const coordSvg = d.activeSvg || svg;
    const [wx, wy] = clientToSiteMapWorld(coordSvg, evt);
    const mx = metrics.nx(wx);
    const my = metrics.ny(wy);
    if (state.siteMap.compareSplit) {
      state.siteMap.syncCursorWorld = [wx, wy];
    }

    if (d.kind === "spot-move-list") {
      if (d.startClientX !== undefined) {
        d.movedPx = Math.max(
          d.movedPx || 0,
          Math.hypot(evt.clientX - d.startClientX, evt.clientY - d.startClientY)
        );
      }
      if (!d.undoPushed && (d.movedPx || 0) >= 2 && d.preMoveSnapshot) {
        state.siteMap.undoStack.push(d.preMoveSnapshot);
        if (state.siteMap.undoStack.length > 40) state.siteMap.undoStack.shift();
        state.siteMap.redoStack = [];
        updateSiteMapUndoButtons();
        d.undoPushed = true;
      }
      const dx = mx - d.startX;
      const dy = my - d.startY;
      d.items.forEach(({ ref, origX, origY }) => {
        ref.map_x = +Math.min(98.8, Math.max(1.2, origX + dx)).toFixed(2);
        ref.map_y = +Math.min(98.8, Math.max(1.2, origY + dy)).toFixed(2);
        ref.map_custom = 1;
      });
    } else if (d.kind === "multi-spot-scale") {
      const curDist = Math.max(0.25, Math.hypot(wx - d.cxWorld, wy - d.cyWorld));
      const factor = Math.min(3.2, Math.max(0.35, curDist / (d.origBaseDist || 2.0)));
      (d.items || []).forEach(({ ref, origWx, origWy, origW, origH }) => {
        ref.map_w = +Math.min(14, Math.max(0.75, origW * factor)).toFixed(2);
        ref.map_h = +Math.min(14, Math.max(0.75, origH * factor)).toFixed(2);
        if (d.scaleSpacing) {
          const nxW = Math.min(
            metrics.worldW - 0.8,
            Math.max(0.8, d.cxWorld + (origWx - d.cxWorld) * factor)
          );
          const nyW = Math.min(
            metrics.worldH - 0.8,
            Math.max(0.8, d.cyWorld + (origWy - d.cyWorld) * factor)
          );
          ref.map_x = metrics.nx(nxW);
          ref.map_y = metrics.ny(nyW);
        }
        ref.map_custom = 1;
      });
      syncSpotTransformSlidersOnly(d.primarySpot);
    } else if (d.kind === "multi-spot-rotate") {
      const curDeg = (Math.atan2(wy - d.cyWorld, wx - d.cxWorld) * 180) / Math.PI;
      let deltaDeg = curDeg - d.startPointerDeg;
      if (evt.shiftKey) {
        deltaDeg = Math.round(deltaDeg / 15) * 15;
      }
      const rad = (deltaDeg * Math.PI) / 180;
      const cosA = Math.cos(rad);
      const sinA = Math.sin(rad);
      (d.items || []).forEach(({ ref, origWx, origWy, origAngle }) => {
        if (d.revolvePositions) {
          const dxW = origWx - d.cxWorld;
          const dyW = origWy - d.cyWorld;
          const nxW = Math.min(
            metrics.worldW - 0.8,
            Math.max(0.8, d.cxWorld + dxW * cosA - dyW * sinA)
          );
          const nyW = Math.min(
            metrics.worldH - 0.8,
            Math.max(0.8, d.cyWorld + dxW * sinA + dyW * cosA)
          );
          ref.map_x = metrics.nx(nxW);
          ref.map_y = metrics.ny(nyW);
        }
        ref.map_angle = normalizeDeg(origAngle + deltaDeg);
        ref.map_custom = 1;
      });
      syncSpotTransformSlidersOnly(d.primarySpot);
    } else if (d.kind === "spot-rotate") {
      let ang = (Math.atan2(wy - d.cyWorld, wx - d.cxWorld) * 180) / Math.PI + 90;
      if (evt.shiftKey && (!d.groupTargets || d.groupTargets.length <= 1)) {
        ang = Math.round(ang / 15) * 15;
      }
      const nextDeg = normalizeDeg(ang);
      const delta = nextDeg - Number(d.origAngle || 0);
      if (d.groupTargets && d.groupTargets.length > 1) {
        d.groupTargets.forEach(({ ref, origAngle }) => {
          ref.map_angle = normalizeDeg(origAngle + delta);
          ref.map_custom = 1;
        });
      } else {
        d.spot.map_angle = nextDeg;
        d.spot.map_custom = 1;
      }
      syncSpotTransformSlidersOnly(d.spot);
    } else if (d.kind === "spot-scale") {
      const curDist = Math.max(0.25, Math.hypot(wx - d.cxWorld, wy - d.cyWorld));
      const factor = Math.min(3.0, Math.max(0.42, curDist / (d.origBaseDist || 1.5)));
      (d.items || []).forEach(({ ref, origW, origH }) => {
        ref.map_w = +Math.min(14, Math.max(0.8, origW * factor)).toFixed(2);
        ref.map_h = +Math.min(14, Math.max(0.8, origH * factor)).toFixed(2);
        ref.map_custom = 1;
      });
      syncSpotTransformSlidersOnly(d.spot);
    } else if (
      d.kind === "spot-resize" ||
      d.kind === "spot-resize-w" ||
      d.kind === "spot-resize-h"
    ) {
      const rad = (Number(d.spot.map_angle || 0) * Math.PI) / 180;
      const vx = wx - d.cxWorld;
      const vy = wy - d.cyWorld;
      const localX = vx * Math.cos(rad) + vy * Math.sin(rad);
      const localY = -vx * Math.sin(rad) + vy * Math.cos(rad);
      if (d.kind === "spot-resize" || d.kind === "spot-resize-w") {
        d.spot.map_w = +Math.min(14, Math.max(0.85, Math.abs(localX) * 2)).toFixed(2);
      }
      if (d.kind === "spot-resize" || d.kind === "spot-resize-h") {
        d.spot.map_h = +Math.min(14, Math.max(0.85, Math.abs(localY) * 2)).toFixed(2);
      }
      d.spot.map_custom = 1;
      syncSpotTransformSlidersOnly(d.spot);
    } else if (d.kind === "route-pt") {
      const rt = (state.siteMap.draftRoutes || [])[d.routeIdx];
      if (rt && rt.points?.[d.ptIdx]) {
        rt.points[d.ptIdx] = [
          +Math.min(99.2, Math.max(0.8, mx)).toFixed(2),
          +Math.min(99.2, Math.max(0.8, my)).toFixed(2),
        ];
      }
    } else if (d.kind === "spot-group-line") {
      let dxW = wx - d.anchorWorldX;
      let dyW = wy - d.anchorWorldY;
      const dist = Math.hypot(dxW, dyW);
      if (dist >= 1.0) {
        let ang = (Math.atan2(dyW, dxW) * 180) / Math.PI;
        if (evt.shiftKey) {
          ang = Math.round(ang / 15) * 15;
          const r = (ang * Math.PI) / 180;
          dxW = Math.cos(r) * dist;
          dyW = Math.sin(r) * dist;
        }
        const n = d.groupSpots.length;
        d.groupSpots.forEach((sp, idx) => {
          const t = n === 1 ? 0 : idx / (n - 1);
          const sx = Math.min(metrics.worldW - 1, Math.max(1, d.anchorWorldX + dxW * t));
          const sy = Math.min(metrics.worldH - 1, Math.max(1, d.anchorWorldY + dyW * t));
          sp.map_x = metrics.nx(sx);
          sp.map_y = metrics.ny(sy);
          sp.map_angle = normalizeDeg(ang);
          sp.map_custom = 1;
        });
      }
    } else if (d.kind === "walkway-pt") {
      const wk = (state.siteMap.draftWalkways || [])[d.walkwayIdx];
      if (wk && wk.points?.[d.ptIdx]) {
        const snapRes = snapWalkwayPointToNetwork(mx, my, d.walkwayIdx, d.ptIdx);
        state.siteMap.walkwaySnapTarget = snapRes.snapped ? snapRes : null;
        wk.points[d.ptIdx] = snapRes.pt;
      }
    } else if (d.kind === "road-pt") {
      const r = (state.siteMap.draftRoads || [])[d.roadIdx];
      if (r && r.points?.[d.ptIdx]) {
        r.points[d.ptIdx] = [
          +Math.min(99.2, Math.max(0.8, mx)).toFixed(2),
          +Math.min(99.2, Math.max(0.8, my)).toFixed(2),
        ];
      }
    } else if (d.kind === "unit-door") {
      d.unit.door_x = +Math.min(98.8, Math.max(1.2, d.origX + (mx - d.startX))).toFixed(2);
      d.unit.door_y = +Math.min(98.8, Math.max(1.2, d.origY + (my - d.startY))).toFixed(2);
      d.unit.door_custom = 1;
      syncSiteMapStudioControls();
    } else if (d.kind === "unit") {
      const dxMove = mx - d.startX;
      const dyMove = my - d.startY;
      d.unit.map_x = +Math.min(98, Math.max(2, d.origX + dxMove)).toFixed(2);
      d.unit.map_y = +Math.min(98, Math.max(2, d.origY + dyMove)).toFixed(2);
      if (!d.unit.door_custom) {
        d.unit.door_x = +Math.min(98.8, Math.max(1.2, d.origDoorX + dxMove)).toFixed(2);
        d.unit.door_y = +Math.min(98.8, Math.max(1.2, d.origDoorY + dyMove)).toFixed(2);
      }
      syncSiteMapStudioControls();
    } else if (d.kind === "gate") {
      d.gate.x = +Math.min(98.5, Math.max(1.5, d.origX + (mx - d.startX))).toFixed(2);
      d.gate.y = +Math.min(98.5, Math.max(1.5, d.origY + (my - d.startY))).toFixed(2);
    }

    renderAbstractSvgMap(state.overview);
  });

  window.addEventListener("mouseup", (evt) => {
    const d = state.siteMap.dragging;
    state.siteMap.dragging = null;
    if (!d) return;

    if (d.kind === "marquee-select") {
      updateMarqueeBoxDom(null);
      if ((d.movedPx || 0) < 4) {
        if (!d.additive && getSelectedSpots().length > 0) {
          setSelectedSpotIds([]);
          syncSiteMapStudioControls();
          if (state.overview) renderAbstractSvgMap(state.overview);
        }
        return;
      }
      if (state.overview) {
        const metrics = getSiteMapMetrics(state.overview.site_map);
        const minWx = Math.min(d.startWorld[0], d.curWorld[0]);
        const maxWx = Math.max(d.startWorld[0], d.curWorld[0]);
        const minWy = Math.min(d.startWorld[1], d.curWorld[1]);
        const maxWy = Math.max(d.startWorld[1], d.curWorld[1]);
        const hitIds = (state.siteMap.draftSpots || [])
          .filter((sp) => spotIntersectsWorldBox(sp, minWx, minWy, maxWx, maxWy, metrics))
          .map((sp) => sp.spot_id);
        const nextIds = d.additive ? [...(d.baseIds || []), ...hitIds] : hitIds;
        setSelectedSpotIds(nextIds, hitIds[0] || nextIds[0] || null);
        syncSiteMapStudioControls();
        renderAbstractSvgMap(state.overview);
      }
      return;
    }

    if (d.kind === "spot-move-list" && d.narrowToSpotOnClick && (d.movedPx || 0) < 3) {
      setSelectedSpotIds([d.narrowToSpotOnClick], d.narrowToSpotOnClick);
      syncSiteMapStudioControls();
      if (state.overview) renderAbstractSvgMap(state.overview);
      return;
    }

    if (d.kind === "unbound-cam-drag") {
      const ghostEl = document.getElementById("floatingVmodelDragGhost");
      ghostEl?.classList.add("hidden");
      clearCameraDropPreviewDom();
      if (d.moved) {
        state.siteMap.suppressBannerClickOnce = true;
        setTimeout(() => {
          state.siteMap.suppressBannerClickOnce = false;
        }, 80);
        if (d.hoverWorld) {
          commitCameraDropOrClickPlacement(d.spec, d.hoverWorld, null, d.snapGroup);
        }
      } else if (!d.fromPlaceBtn) {
        // Clicking the card body itself enters interactive 1-click placement mode!
        startInteractiveCameraPlacement(d.spec.cameraId);
      }
      return;
    }

    if (d.kind === "pending-cam-span-drag") {
      clearCameraDropPreviewDom();
      const dist = Math.hypot(
        d.currentWorld[0] - d.anchorWorld[0],
        d.currentWorld[1] - d.anchorWorld[1]
      );
      const endWorld = dist >= 1.2 ? d.currentWorld : null;
      commitCameraDropOrClickPlacement(
        d.spec,
        d.anchorWorld,
        endWorld,
        endWorld ? null : d.snapGroup
      );
      return;
    }

    if (d.kind === "palette-vmodel-drag") {
      const ghostEl = document.getElementById("floatingVmodelDragGhost");
      ghostEl?.classList.add("hidden");
      updateSpotPlacePreviewDom(null);
      const mKey = d.modelKey || "sedan";
      const preset = VEHICLE_MODELS_CLIENT[mKey] || VEHICLE_MODELS_CLIENT.sedan;
      state.siteMap.paletteModel = mKey;

      if (d.moved) {
        if (d.hoverWorld) {
          setDesignatedPlaceMode(null);
          commitDesignatedSpots({
            mode: "palette",
            modelKey: mKey,
            anchorWorld: d.hoverWorld,
            endWorld: null,
          });
        } else {
          syncSiteMapStudioControls();
        }
      } else {
        // Plain click on .vmodel-chip in Section 1 prepares the NEXT independent group cleanly
        // without mutating an already-placed group! (Hold Alt+Click to convert selected stalls' model.)
        if (!state.siteMap.editMode) enterSiteMapEditMode();
        if (state.siteMap.subTool !== "spots") setSiteMapSubTool("spots");
        const targets = getSpotTransformTargets(d.shiftKey);
        if (d.altKey && targets.length) {
          pushSiteMapUndo();
          targets.forEach((sp) => {
            sp.vehicle_model = preset.key;
            sp.map_w = preset.w;
            sp.map_h = preset.h;
            sp.compact_only = preset.compact_only ? 1 : 0;
            if (preset.angleOffset && Math.abs(sp.map_angle % 45) < 1) {
              sp.map_angle = normalizeDeg(sp.map_angle + preset.angleOffset);
            }
            sp.map_custom = 1;
          });
          syncSiteMapStudioControls();
          if (state.overview) renderAbstractSvgMap(state.overview);
        } else {
          finishCurrentSpotGroup({ armNextPlace: true });
        }
      }
      return;
    }

    if (d.kind === "designated-span-drag") {
      updateSpotPlacePreviewDom(null);
      const dist = Math.hypot(
        d.currentWorld[0] - d.anchorWorld[0],
        d.currentWorld[1] - d.anchorWorld[1]
      );
      const endWorld = dist >= 1.0 ? d.currentWorld : null;
      if (!evt?.shiftKey) {
        setDesignatedPlaceMode(null);
      }
      commitDesignatedSpots({
        mode: d.mode || "spots",
        anchorWorld: d.anchorWorld,
        endWorld,
      });
      return;
    }

    const pendingTagClick = state.siteMap.pendingSpotTagClick;
    state.siteMap.pendingSpotTagClick = null;
    if (
      d.kind === "pan" &&
      d.button === 0 &&
      (d.movedPx || 0) < 5 &&
      !state.siteMap.editMode &&
      pendingTagClick
    ) {
      handleBrowseSpotTagClick(
        pendingTagClick.spotId,
        pendingTagClick.additive,
        pendingTagClick.clientX,
        pendingTagClick.clientY
      );
      return;
    }

    if (
      d.kind === "pan" &&
      d.button === 0 &&
      (d.movedPx || 0) < 3 &&
      state.siteMap.editMode &&
      !state.siteMap.spacePressed &&
      getSelectedSpots().length > 0
    ) {
      // Single click on empty map background deselects active stall(s) cleanly
      setSelectedSpotIds([]);
      syncSiteMapStudioControls();
      if (state.overview) renderAbstractSvgMap(state.overview);
    }
  });

  // Keyboard shortcuts while Tab 1 Site-Map Edit Mode is active
  window.addEventListener("keydown", (evt) => {
    const tag = evt.target?.tagName;
    const isInput = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (evt.code === "Space" && !isInput) {
      state.siteMap.spacePressed = true;
    }
    if (!isInput && state.siteMap.pendingCameraPlacement) {
      if (evt.key === "Escape") {
        evt.preventDefault();
        cancelInteractiveCameraPlacement();
        return;
      }
      if (!evt.metaKey && !evt.ctrlKey && evt.key.toLowerCase() === "h") {
        evt.preventDefault();
        const spec = state.siteMap.pendingCameraPlacement;
        spec.orientation = spec.orientation === "horizontal" ? "vertical" : "horizontal";
        renderCameraPlacementHud();
        if (state.siteMap.cursorWorld) {
          renderCameraDropPreviewDom(spec, state.siteMap.cursorWorld, null, null);
        }
        return;
      }
      if (!evt.metaKey && !evt.ctrlKey && evt.key.toLowerCase() === "r") {
        evt.preventDefault();
        const spec = state.siteMap.pendingCameraPlacement;
        spec.angleOffset = ((Number(spec.angleOffset || 0) + 90) % 360);
        renderCameraPlacementHud();
        if (state.siteMap.cursorWorld) {
          renderCameraDropPreviewDom(spec, state.siteMap.cursorWorld, null, null);
        }
        return;
      }
    }
    if (!state.siteMap.editMode || isInput) return;
    if (!document.getElementById("tab-driver")?.classList.contains("active")) return;

    if ((evt.metaKey || evt.ctrlKey) && evt.key.toLowerCase() === "z") {
      evt.preventDefault();
      if (evt.shiftKey) redoSiteMapEdit();
      else undoSiteMapEdit();
      return;
    }
    if (
      (evt.metaKey || evt.ctrlKey) &&
      evt.key.toLowerCase() === "a" &&
      state.siteMap.subTool === "spots"
    ) {
      const all = state.siteMap.draftSpots || [];
      if (all.length) {
        evt.preventDefault();
        setSelectedSpotIds(
          all.map((s) => s.spot_id),
          all[0].spot_id
        );
        syncSiteMapStudioControls();
        if (state.overview) renderAbstractSvgMap(state.overview);
      }
      return;
    }
    if (
      !evt.metaKey &&
      !evt.ctrlKey &&
      !evt.altKey &&
      (state.siteMap.dragging?.kind === "palette-vmodel-drag" ||
        state.siteMap.placeMode === "spots")
    ) {
      if (/^[1-9]$/.test(evt.key)) {
        evt.preventDefault();
        setSpotPlaceCountAndMode(Number(evt.key), "fixed");
        return;
      }
      if (evt.key === "ArrowUp" || evt.key === "=") {
        evt.preventDefault();
        const cur = Number(document.getElementById("spotPlaceCountInput")?.value || 1);
        setSpotPlaceCountAndMode(cur + 1, "fixed");
        return;
      }
      if (evt.key === "ArrowDown" || evt.key === "-") {
        evt.preventDefault();
        const cur = Number(document.getElementById("spotPlaceCountInput")?.value || 1);
        setSpotPlaceCountAndMode(Math.max(1, cur - 1), "fixed");
        return;
      }
    }
    if (
      evt.key === "Enter" &&
      !state.siteMap.drawingRoute &&
      !state.siteMap.drawingRoad &&
      state.siteMap.subTool === "spots" &&
      getSelectedSpots().length > 0
    ) {
      evt.preventDefault();
      finishCurrentSpotGroup({ armNextPlace: false });
      return;
    }
    if (!evt.metaKey && !evt.ctrlKey && evt.key.toLowerCase() === "r") {
      if (state.siteMap.placeMode) {
        evt.preventDefault();
        flipPlaceDirection();
        return;
      }
    }
    if (evt.key === "Escape" && state.siteMap.placeMode) {
      evt.preventDefault();
      setDesignatedPlaceMode(null);
      return;
    }
    if (
      evt.key === "Escape" &&
      state.siteMap.subTool === "spots" &&
      getSelectedSpots().length >= 1
    ) {
      evt.preventDefault();
      finishCurrentSpotGroup({ armNextPlace: false });
      return;
    }
    if (
      evt.key === "Escape" &&
      (state.siteMap.drawingRoad ||
        state.siteMap.drawingRoute ||
        state.siteMap.drawingWalkway)
    ) {
      state.siteMap.drawingRoad = false;
      state.siteMap.newRoadPts = [];
      state.siteMap.drawingRoute = false;
      state.siteMap.newRoutePts = [];
      state.siteMap.drawingWalkway = false;
      state.siteMap.newWalkwayPts = [];
      state.siteMap.walkwaySnapTarget = null;
      document.getElementById("btnMapFinishRoad")?.classList.add("hidden");
      document.getElementById("btnMapFinishRoute")?.classList.add("hidden");
      document.getElementById("btnMapFinishWalkway")?.classList.add("hidden");
      renderAbstractSvgMap(state.overview);
      return;
    }
    if (evt.key === "Enter" && state.siteMap.drawingWalkway) {
      evt.preventDefault();
      finishDrawingWalkway();
      return;
    }
    if (evt.key === "Enter" && state.siteMap.drawingRoute) {
      evt.preventDefault();
      finishDrawingRoute();
      return;
    }
    if (evt.key === "Enter" && state.siteMap.drawingRoad) {
      evt.preventDefault();
      finishDrawingRoad();
      return;
    }
    if (evt.key === "Delete" || evt.key === "Backspace") {
      if (state.siteMap.subTool === "spots" && getSelectedSpots().length > 0) {
        evt.preventDefault();
        deleteSelectedMapSpot();
      } else if (
        state.siteMap.subTool === "walkways" &&
        state.siteMap.selectedWalkwayIdx >= 0 &&
        state.siteMap.selectedWalkwayPtIdx >= 0
      ) {
        evt.preventDefault();
        document.getElementById("btnMapDeleteWalkwayPt")?.click();
      } else if (
        state.siteMap.subTool === "routes" &&
        state.siteMap.selectedRouteIdx >= 0 &&
        state.siteMap.selectedRoutePtIdx >= 0
      ) {
        evt.preventDefault();
        document.getElementById("btnMapDeleteRoutePt")?.click();
      } else if (
        state.siteMap.subTool === "roads" &&
        state.siteMap.selectedRoadIdx >= 0 &&
        state.siteMap.selectedRoadPtIdx >= 0
      ) {
        evt.preventDefault();
        document.getElementById("btnMapDeleteRoadPt")?.click();
      }
    }
  });

  window.addEventListener("keyup", (evt) => {
    if (evt.code === "Space") state.siteMap.spacePressed = false;
  });
}

function findOverlappingRoadForRouteClient(rt, roads) {
  const rPts = rt?.points || [];
  if (rPts.length < 2 || !Array.isArray(roads) || !roads.length) return -1;
  let bestRoadIdx = -1;
  let bestMeanDist = Infinity;
  roads.forEach((road, idx) => {
    const segPts = road?.points || [];
    if (segPts.length < 2) return;
    let total = 0;
    for (const [px, py] of rPts) {
      let minSeg = Infinity;
      for (let i = 0; i < segPts.length - 1; i++) {
        const [x1, y1] = segPts[i];
        const [x2, y2] = segPts[i + 1];
        const dx = x2 - x1;
        const dy = y2 - y1;
        const l2 = dx * dx + dy * dy;
        const t = l2 > 0.0001 ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / l2)) : 0;
        const d = Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
        if (d < minSeg) minSeg = d;
      }
      total += minSeg;
    }
    const meanD = total / rPts.length;
    if (meanD < bestMeanDist) {
      bestMeanDist = meanD;
      bestRoadIdx = idx;
    }
  });
  return bestMeanDist <= 3.8 ? bestRoadIdx : -1;
}

function enterSiteMapEditMode() {
  if (!state.overview) return;
  const smap = state.overview.site_map || {};
  const topo = smap.topology || {};
  const defaultRoads = [
    {
      road_id: "R_MAIN",
      name: "2F 车行环路",
      one_way: true,
      direction: "oneway",
      points: [[8, 83], [88, 83], [88, 22], [12, 22]],
      width: 5.2,
    },
  ];
  const defaultGates = [
    { gate_id: "south", name: "南门入口", x: 47, y: 96.5, w: 8.5, h: 2.8 },
    { gate_id: "east", name: "东门", x: 95.5, y: 52, w: 6.5, h: 2.8 },
  ];

  const activeRoads = topo.roads?.length ? topo.roads : defaultRoads;
  const activeWalkways = topo.walkways || [];
  const flatSpots = [];
  (state.overview.zones || []).forEach((z) => {
    (z.spots || []).forEach((sp) => {
      if (sp.map_placed === false) return;
      flatSpots.push(
        JSON.parse(
          JSON.stringify({
            ...sp,
            zone_id: sp.zone_id || z.zone_id,
            zone_name: sp.zone_name || z.name,
            group_name: sp.group_name || "",
            near_unit_id: sp.near_unit_id || "",
          })
        )
      );
    });
  });

  state.siteMap.editMode = true;
  state.siteMap.draftSpots = flatSpots;
  state.siteMap.draftZones = JSON.parse(JSON.stringify(state.overview.zones || []));
  state.siteMap.draftUnits = JSON.parse(JSON.stringify(state.overview.units || [])).map((u) => {
    if (u.door_x == null || u.door_y == null) {
      const [dx, dy] = projectPointToSegmentsClient(
        Number(u.map_x || 50),
        Number(u.map_y || 50),
        [...activeWalkways, ...activeRoads]
      );
      u.door_x = dx;
      u.door_y = dy;
    }
    return u;
  });
  const clonedRoads = JSON.parse(
    JSON.stringify(topo.roads?.length ? topo.roads : defaultRoads)
  ).map((r) => ({
    ...r,
    one_way: r.one_way !== false && r.direction !== "twoway",
    direction: r.one_way === false || r.direction === "twoway" ? "twoway" : "oneway",
  }));

  // Absorb legacy overlapping routes into unified 2F roads so no duplicate double-polyline remains
  const keptRoutes = [];
  (topo.routes || []).forEach((rt) => {
    const matchRoadIdx = findOverlappingRoadForRouteClient(rt, clonedRoads);
    if (matchRoadIdx >= 0) {
      if (rt.one_way !== undefined) {
        clonedRoads[matchRoadIdx].one_way = Boolean(rt.one_way);
        clonedRoads[matchRoadIdx].direction = rt.one_way ? "oneway" : "twoway";
      }
    } else {
      keptRoutes.push(rt);
    }
  });

  state.siteMap.draftRoads = clonedRoads;
  state.siteMap.draftRoutes = JSON.parse(JSON.stringify(keptRoutes));
  state.siteMap.draftWalkways = JSON.parse(JSON.stringify(topo.walkways || []));
  state.siteMap.draftSpanMeters = Number(
    smap.estimated_span_meters || topo.estimated_span_meters || 185
  );
  state.siteMap.draftGates = JSON.parse(
    JSON.stringify(topo.gates?.length ? topo.gates : defaultGates)
  );
  state.siteMap.draftGreens = JSON.parse(JSON.stringify(topo.greens || []));
  state.siteMap.deletedSpotIds = [];
  state.siteMap.deletedUnitIds = [];
  state.siteMap.undoStack = [];
  state.siteMap.redoStack = [];
  state.siteMap.selectedSpotIds = [];
  state.siteMap.selectedSpotId = null;
  state.siteMap.selectedGroupCamId = null;
  state.siteMap.selectedRouteIdx = state.siteMap.draftRoutes.length ? 0 : -1;
  state.siteMap.selectedRoutePtIdx = -1;
  state.siteMap.selectedRoadIdx = state.siteMap.draftRoads.length ? 0 : -1;
  state.siteMap.selectedRoadPtIdx = -1;
  state.siteMap.selectedWalkwayIdx = state.siteMap.draftWalkways.length ? 0 : -1;
  state.siteMap.selectedWalkwayPtIdx = -1;
  state.siteMap.walkwaySnapTarget = null;
  state.siteMap.selectedNodeKind = state.siteMap.draftUnits[0] ? "unit" : null;
  state.siteMap.selectedNodeId = state.siteMap.draftUnits[0]?.unit_id || null;

  // Auto-collapse left voice guidance rail during editing to maximize side-by-side workspace
  if (!state.siteMap.driverCollapsed) {
    state.siteMap.driverCollapsed = true;
    state.siteMap.driverAutoCollapsedByEdit = true;
  }

  // Default into Left-Right comparison view when entering edit mode unless user explicitly chose single
  state.siteMap.savedBrowseBackdropAlpha = state.siteMap.backdropAlpha || 0.58;
  if (!state.siteMap.userToggledCompare) {
    state.siteMap.compareSplit = true;
  }

  if (state.siteMap.backdropDataUrl && !state.siteMap.userToggledBackdrop) {
    state.siteMap.showBackdrop = true;
    state.siteMap.backdropAlpha = state.siteMap.compareSplit ? 0.2 : 0.58;
  }

  document.getElementById("btnToggleEditMap").textContent = "退出微调";
  syncMapWorkspaceLayout();
  updateSiteMapHeaderUI(state.overview.site_map);
  setSiteMapSubTool(state.siteMap.subTool || "spots");
}

function exitSiteMapEditMode(reRender = true) {
  state.siteMap.editMode = false;
  state.siteMap.draftSpots = null;
  state.siteMap.draftZones = null;
  state.siteMap.draftUnits = null;
  state.siteMap.draftRoads = null;
  state.siteMap.draftRoutes = null;
  state.siteMap.draftWalkways = null;
  state.siteMap.draftGates = null;
  state.siteMap.draftGreens = null;
  state.siteMap.deletedSpotIds = [];
  state.siteMap.deletedUnitIds = [];
  state.siteMap.selectedSpotIds = [];
  state.siteMap.dragging = null;
  setDesignatedPlaceMode(null);
  document.getElementById("floatingVmodelDragGhost")?.classList.add("hidden");
  state.siteMap.drawingRoad = false;
  state.siteMap.newRoadPts = [];
  state.siteMap.drawingRoute = false;
  state.siteMap.newRoutePts = [];
  state.siteMap.drawingWalkway = false;
  state.siteMap.newWalkwayPts = [];
  state.siteMap.walkwaySnapTarget = null;
  state.siteMap.undoStack = [];
  state.siteMap.redoStack = [];
  state.siteMap.syncCursorWorld = null;

  if (state.siteMap.driverAutoCollapsedByEdit) {
    state.siteMap.driverCollapsed = false;
    state.siteMap.driverAutoCollapsedByEdit = false;
  }
  if (!state.siteMap.userToggledCompare) {
    state.siteMap.compareSplit = false;
  }
  if (!state.siteMap.userToggledBackdrop) {
    state.siteMap.backdropAlpha = state.siteMap.savedBrowseBackdropAlpha || 0.58;
  }

  document.getElementById("btnToggleEditMap").textContent = "微调与编辑";
  syncMapWorkspaceLayout();
  updateSiteMapHeaderUI(state.overview?.site_map);
  if (reRender && state.overview) renderAbstractSvgMap(state.overview);
}

async function uploadAndGenerateSiteMap(dataUrl) {
  const wasEditing = Boolean(state.siteMap.editMode);
  const loading = document.getElementById("siteMapLoadingOverlay");
  const btnUpload = document.getElementById("btnUploadSiteMap");
  const noteEl = document.getElementById("siteMapStatusNote");
  loading.classList.remove("hidden");
  btnUpload.disabled = true;
  if (noteEl) noteEl.textContent = "正在调用多模态视觉与几何引擎解析小区平面图...";

  try {
    const res = await api("/api/sitemap/auto-generate", {
      method: "POST",
      body: JSON.stringify({ image_base64: dataUrl }),
    });
    if (res.site_map?.backdrop_jpeg_b64) {
      state.siteMap.backdropDataUrl = res.site_map.backdrop_jpeg_b64;
      state.siteMap.backdropUpdatedAt = res.site_map.updated_at || Date.now();
      state.siteMap.showBackdrop = true;
    }
    exitSiteMapEditMode(false);
    resetSiteMapZoom();
    await refreshOverview();
    if (wasEditing) {
      enterSiteMapEditMode();
    }
  } catch (err) {
    alert(`平面图生成失败：${err.message}`);
  } finally {
    loading.classList.add("hidden");
    btnUpload.disabled = false;
  }
}

function shortenSpotLabel(label, fallbackIdx) {
  const s = String(label || "").trim();
  const m = s.match(/(\d{1,3})$/);
  if (m) return m[1].padStart(2, "0");
  if (s.length <= 4) return s;
  return s.slice(-3) || String(fallbackIdx + 1).padStart(2, "0");
}

function renderAbstractSvgMap(data) {
  const svg = document.getElementById("communitySvg");
  if (!svg || !data) return;
  const selectedUnitId = document.getElementById("unitSelect")?.value || "";
  const smap = data.site_map || {};
  const metrics = getSiteMapMetrics(smap);
  const { worldW, worldH, px, py } = metrics;

  applySiteMapViewBox(svg, worldW, worldH);

  const zoom = state.siteMap.zoom || 1.0;
  const invScale = Math.pow(zoom, -0.52);

  const topo = smap.topology || {};
  const isEditing = state.siteMap.editMode;
  const subTool = state.siteMap.subTool;

  const roads =
    isEditing && state.siteMap.draftRoads
      ? state.siteMap.draftRoads
      : topo.roads?.length
      ? topo.roads
      : [{ road_id: "R_MAIN", points: [[8, 83], [88, 83], [88, 22], [12, 22]], width: 5.2 }];

  const routesRaw =
    isEditing && state.siteMap.draftRoutes
      ? state.siteMap.draftRoutes
      : topo.routes || [];
  const routes = routesRaw.filter((rt) => findOverlappingRoadForRouteClient(rt, roads) < 0);

  const walkways =
    isEditing && state.siteMap.draftWalkways
      ? state.siteMap.draftWalkways
      : topo.walkways || [];

  const gates =
    isEditing && state.siteMap.draftGates
      ? state.siteMap.draftGates
      : topo.gates?.length
      ? topo.gates
      : [
          { gate_id: "south", name: "南门入口", x: 47, y: 96.5, w: 8.5, h: 2.8 },
          { gate_id: "east", name: "东门", x: 95.5, y: 52, w: 6.5, h: 2.8 },
        ];

  const greens =
    isEditing && state.siteMap.draftGreens ? state.siteMap.draftGreens : topo.greens || [];

  const unitsToDraw =
    isEditing && state.siteMap.draftUnits ? state.siteMap.draftUnits : data.units || [];

  // Flatten live or draft individual parking spots (NO zone wrapper boxes; suppress unplaced camera spots!)
  let spotsToDraw = [];
  if (isEditing && state.siteMap.draftSpots) {
    spotsToDraw = state.siteMap.draftSpots;
  } else {
    (data.zones || []).forEach((z) => {
      (z.spots || []).forEach((sp) => {
        if (sp.map_placed === false) return;
        spotsToDraw.push({
          ...sp,
          zone_id: sp.zone_id || z.zone_id,
          zone_name: sp.zone_name || z.name,
        });
      });
    });
  }

  let html = `
    <rect x="0.25" y="0.25" width="${(worldW - 0.5).toFixed(2)}" height="${(
    worldH - 0.5
  ).toFixed(2)}" rx="2.6" fill="#F8FAFC" stroke="#E2E8F0" stroke-width="0.4"/>
  `;

  if (state.siteMap.showBackdrop && state.siteMap.backdropDataUrl) {
    const bgAlpha = Number(state.siteMap.backdropAlpha || 0.58).toFixed(2);
    html += `
      <image href="${state.siteMap.backdropDataUrl}" x="0.25" y="0.25" width="${(
      worldW - 0.5
    ).toFixed(2)}" height="${(worldH - 0.5).toFixed(
      2
    )}" preserveAspectRatio="none" opacity="${bgAlpha}"/>
    `;
  } else {
    for (const gr of greens) {
      html += `
        <rect x="${px(gr.x)}" y="${py(gr.y)}" width="${px(gr.w)}" height="${py(gr.h)}" rx="2.2"
              fill="rgba(52, 199, 89, 0.09)" stroke="rgba(52, 199, 89, 0.20)" stroke-width="0.3"/>
      `;
    }
  }

  // Helper for directional flow chevrons on unified 2F roads & routes (arc-length spaced)
  const renderChevronAlongSegments = (ptsArr, color, scaleMul = 1.0, twoWay = false) => {
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
        chevHtml += `<g transform="translate(${cx.toFixed(2)} ${cy.toFixed(2)}) rotate(${ang.toFixed(
          1
        )})">
          <polygon points="${(1.25 * s).toFixed(2)},0 ${(-0.85 * s).toFixed(2)},${(
          -0.85 * s
        ).toFixed(2)} ${(-0.38 * s).toFixed(2)},0 ${(-0.85 * s).toFixed(2)},${(
          0.85 * s
        ).toFixed(2)}" fill="${color}"/>
        </g>`;
        if (twoWay && segLen > 7.5) {
          const cx2 = x1 + dx * Math.min(0.86, t + 0.16);
          const cy2 = y1 + dy * Math.min(0.86, t + 0.16);
          chevHtml += `<g transform="translate(${cx2.toFixed(2)} ${cy2.toFixed(2)}) rotate(${(
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
    return chevHtml;
  };

  // 1. Unified 2F Vehicular Roads Layer (Road Surface + Flow Chevrons + Editor Handles)
  roads.forEach((r, rIdx) => {
    const ptsArr = (r.points || []).map(([rx, ry]) => [px(rx), py(ry)]);
    if (ptsArr.length < 2) return;
    const ptsStr = ptsArr.map(([x, y]) => `${x},${y}`).join(" ");
    const rw = Math.max(1.8, Number(r.width || 4.4));
    const isSelRoad = isEditing && subTool === "roads" && rIdx === state.siteMap.selectedRoadIdx;
    const isTwoWay = r.one_way === false || r.direction === "twoway";

    const roadFillStroke = state.siteMap.showBackdrop
      ? isSelRoad
        ? "rgba(0, 113, 227, 0.34)"
        : "rgba(148, 163, 184, 0.38)"
      : isSelRoad
      ? "rgba(0, 113, 227, 0.24)"
      : "#E2E8F0";

    const centerDashStroke = isSelRoad
      ? "#0071E3"
      : state.siteMap.showBackdrop
      ? "rgba(2, 132, 199, 0.62)"
      : "rgba(14, 165, 233, 0.45)";

    const chevColor = isSelRoad ? "#0071E3" : "rgba(2, 132, 199, 0.80)";
    const selectAttr =
      isEditing && subTool === "roads"
        ? `data-road-select="${rIdx}" style="cursor:pointer"`
        : "";

    html += `
      <g ${selectAttr}>
        <polyline points="${ptsStr}" fill="none" stroke="${roadFillStroke}"
                  stroke-width="${rw}" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${ptsStr}" fill="none" stroke="${centerDashStroke}"
                  stroke-width="${isSelRoad ? 0.52 : 0.40}" stroke-dasharray="1.6,1.3"/>
        ${renderChevronAlongSegments(ptsArr, chevColor, isSelRoad ? 1.12 : 0.92, isTwoWay)}
      </g>
    `;

    if (isEditing && subTool === "roads") {
      // Midpoint '+' insertion handles
      for (let i = 0; i < ptsArr.length - 1; i++) {
        const mxW = +((ptsArr[i][0] + ptsArr[i + 1][0]) / 2).toFixed(2);
        const myW = +((ptsArr[i][1] + ptsArr[i + 1][1]) / 2).toFixed(2);
        const mr = +(0.95 * invScale).toFixed(2);
        html += `
          <g class="road-mid-handle" data-drag-kind="road-mid" data-road-idx="${rIdx}" data-seg-idx="${i}">
            <circle cx="${mxW}" cy="${myW}" r="${mr}" fill="#FFFFFF" stroke="#0071E3" stroke-width="${(
          0.32 * invScale
        ).toFixed(2)}" opacity="${isSelRoad ? 0.96 : 0.65}"/>
            <text x="${mxW}" y="${(myW + 0.36 * invScale).toFixed(2)}" font-size="${(
          1.15 * invScale
        ).toFixed(2)}" fill="#0071E3" font-weight="700" text-anchor="middle">＋</text>
          </g>
        `;
      }
      // Vertex handles (start node highlighted emerald so direction is immediately clear)
      ptsArr.forEach(([vx, vy], pIdx) => {
        const isSelPt = isSelRoad && pIdx === state.siteMap.selectedRoadPtIdx;
        const vr = +((isSelPt ? 1.35 : 1.08) * invScale).toFixed(2);
        const ptFill =
          pIdx === 0 && !isTwoWay
            ? "#16A34A"
            : isSelPt
            ? "#FF9500"
            : isSelRoad
            ? "#0071E3"
            : "#64748B";
        html += `
          <circle cx="${vx}" cy="${vy}" r="${vr}"
                  fill="${ptFill}"
                  stroke="#FFFFFF" stroke-width="${(0.38 * invScale).toFixed(2)}"
                  class="draggable-node" data-drag-kind="road-pt" data-road-idx="${rIdx}" data-pt-idx="${pIdx}">
            <title>${pIdx === 0 && !isTwoWay ? "单向驶入起点" : `车行道折点 #${pIdx + 1}`}（右键删除）</title>
          </circle>
        `;
      });
    }
  });

  // Active new-road drawing preview
  if (isEditing && state.siteMap.drawingRoad && state.siteMap.newRoadPts.length > 0) {
    const draftWorld = state.siteMap.newRoadPts.map(([rx, ry]) => [px(rx), py(ry)]);
    const previewPts = state.siteMap.cursorWorld
      ? [...draftWorld, state.siteMap.cursorWorld]
      : draftWorld;
    const pStr = previewPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
    html += `
      <polyline points="${pStr}" fill="none" stroke="rgba(0,113,227,0.45)" stroke-width="3.6" stroke-linecap="round" stroke-dasharray="1.8,1.2"/>
      ${renderChevronAlongSegments(previewPts, "#0071E3", 1.05, false)}
    `;
    draftWorld.forEach(([vx, vy], i) => {
      html += `<circle cx="${vx.toFixed(2)}" cy="${vy.toFixed(2)}" r="${(
        1.1 * invScale
      ).toFixed(2)}" fill="${i === 0 ? "#16A34A" : "#0071E3"}" stroke="#FFFFFF" stroke-width="0.35"/>`;
    });
  }

  // 1.3 2F Courtyard Pedestrian Walkways Layer (Walk-only footpaths + magnetic snap handles)
  walkways.forEach((wk, wIdx) => {
    const ptsArr = (wk.points || []).map(([wx, wy]) => [px(wx), py(wy)]);
    if (ptsArr.length < 2) return;
    const ptsStr = ptsArr.map(([x, y]) => `${x},${y}`).join(" ");
    const isSelWk =
      isEditing && subTool === "walkways" && wIdx === state.siteMap.selectedWalkwayIdx;
    const haloStroke = isSelWk ? "rgba(16, 185, 129, 0.28)" : "rgba(16, 185, 129, 0.15)";
    const dashStroke = isSelWk ? "#059669" : "#10B981";
    const selectAttr =
      isEditing && subTool === "walkways"
        ? `data-walkway-select="${wIdx}" style="cursor:pointer"`
        : "";
    html += `
      <g ${selectAttr}>
        <polyline points="${ptsStr}" fill="none" stroke="${haloStroke}" stroke-width="${
      isSelWk ? 2.15 : 1.55
    }" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${ptsStr}" fill="none" stroke="${dashStroke}" stroke-width="${
      isSelWk ? 0.62 : 0.46
    }" stroke-dasharray="1.05,0.85" stroke-linecap="round" stroke-linejoin="round"/>
      </g>
    `;

    if (isEditing && subTool === "walkways") {
      for (let i = 0; i < ptsArr.length - 1; i++) {
        const mxW = +((ptsArr[i][0] + ptsArr[i + 1][0]) / 2).toFixed(2);
        const myW = +((ptsArr[i][1] + ptsArr[i + 1][1]) / 2).toFixed(2);
        const mr = +(0.90 * invScale).toFixed(2);
        html += `
          <g class="road-mid-handle" data-drag-kind="walkway-mid" data-walkway-idx="${wIdx}" data-seg-idx="${i}">
            <circle cx="${mxW}" cy="${myW}" r="${mr}" fill="#FFFFFF" stroke="#10B981" stroke-width="${(
          0.30 * invScale
        ).toFixed(2)}" opacity="${isSelWk ? 0.96 : 0.68}"/>
            <text x="${mxW}" y="${(myW + 0.35 * invScale).toFixed(2)}" font-size="${(
          1.10 * invScale
        ).toFixed(2)}" fill="#059669" font-weight="700" text-anchor="middle">＋</text>
          </g>
        `;
      }
      ptsArr.forEach(([vx, vy], pIdx) => {
        const isSelPt = isSelWk && pIdx === state.siteMap.selectedWalkwayPtIdx;
        const vr = +((isSelPt ? 1.32 : 1.04) * invScale).toFixed(2);
        html += `
          <circle cx="${vx}" cy="${vy}" r="${vr}"
                  fill="${isSelPt ? "#FF9500" : isSelWk ? "#059669" : "#10B981"}"
                  stroke="#FFFFFF" stroke-width="${(0.36 * invScale).toFixed(2)}"
                  class="draggable-node" data-drag-kind="walkway-pt" data-walkway-idx="${wIdx}" data-pt-idx="${pIdx}">
            <title>2F 人行步道节点 #${pIdx + 1}（拖近道路或单元门自动磁吸 · 右键删除）</title>
          </circle>
        `;
      });
    }
  });

  // Active new-walkway drawing preview + magnetic snap ring
  if (isEditing && state.siteMap.drawingWalkway) {
    if (state.siteMap.newWalkwayPts.length > 0) {
      const draftWorld = state.siteMap.newWalkwayPts.map(([wx, wy]) => [px(wx), py(wy)]);
      const previewPts = state.siteMap.cursorWorld
        ? [...draftWorld, state.siteMap.cursorWorld]
        : draftWorld;
      const pStr = previewPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
      html += `
        <polyline points="${pStr}" fill="none" stroke="rgba(16, 185, 129, 0.25)" stroke-width="1.9" stroke-linecap="round"/>
        <polyline points="${pStr}" fill="none" stroke="#059669" stroke-width="0.62" stroke-linecap="round" stroke-dasharray="1.1,0.8"/>
      `;
      draftWorld.forEach(([vx, vy]) => {
        html += `<circle cx="${vx.toFixed(2)}" cy="${vy.toFixed(2)}" r="${(
          1.05 * invScale
        ).toFixed(2)}" fill="#10B981" stroke="#FFFFFF" stroke-width="0.34"/>`;
      });
    }
    if (state.siteMap.walkwaySnapTarget?.pt) {
      const sxW = px(state.siteMap.walkwaySnapTarget.pt[0]);
      const syW = py(state.siteMap.walkwaySnapTarget.pt[1]);
      html += `
        <g pointer-events="none">
          <circle cx="${sxW.toFixed(2)}" cy="${syW.toFixed(2)}" r="${(
        1.85 * invScale
      ).toFixed(2)}" fill="rgba(16, 185, 129, 0.18)" stroke="#059669" stroke-width="${(
        0.34 * invScale
      ).toFixed(2)}" stroke-dasharray="${(0.65 * invScale).toFixed(2)},${(
        0.45 * invScale
      ).toFixed(2)}"/>
        </g>
      `;
    }
  }

  // 1.5 Non-overlapping Legacy Routes Layer (if any distinct routes remain)

  routes.forEach((rt, rIdx) => {
    const ptsArr = (rt.points || []).map(([rx, ry]) => [px(rx), py(ry)]);
    if (ptsArr.length < 2) return;
    const ptsStr = ptsArr.map(([x, y]) => `${x},${y}`).join(" ");
    const isSelRt = isEditing && subTool === "routes" && rIdx === state.siteMap.selectedRouteIdx;
    const strokeCol = isSelRt ? "#0284C7" : "rgba(2, 132, 199, 0.78)";
    const haloCol = isSelRt ? "rgba(14, 165, 233, 0.24)" : "rgba(14, 165, 233, 0.12)";
    const selectAttr =
      isEditing && subTool === "routes"
        ? `data-route-select="${rIdx}" style="cursor:pointer"`
        : "";
    html += `
      <g ${selectAttr}>
        <polyline points="${ptsStr}" fill="none" stroke="${haloCol}" stroke-width="${
      isSelRt ? 2.4 : 1.7
    }" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${ptsStr}" fill="none" stroke="${strokeCol}" stroke-width="${
      isSelRt ? 0.72 : 0.52
    }" stroke-dasharray="1.8,1.1" stroke-linecap="round" stroke-linejoin="round"/>
        ${renderChevronAlongSegments(
          ptsArr,
          isSelRt ? "#0369A1" : "#0284C7",
          isSelRt ? 1.15 : 0.95,
          rt.one_way === false
        )}
      </g>
    `;

    if (isEditing && subTool === "routes") {
      for (let i = 0; i < ptsArr.length - 1; i++) {
        const mxW = +((ptsArr[i][0] + ptsArr[i + 1][0]) / 2).toFixed(2);
        const myW = +((ptsArr[i][1] + ptsArr[i + 1][1]) / 2).toFixed(2);
        const mr = +(0.92 * invScale).toFixed(2);
        html += `
          <g class="road-mid-handle" data-drag-kind="route-mid" data-route-idx="${rIdx}" data-seg-idx="${i}">
            <circle cx="${mxW}" cy="${myW}" r="${mr}" fill="#FFFFFF" stroke="#0284C7" stroke-width="${(
          0.32 * invScale
        ).toFixed(2)}" opacity="${isSelRt ? 0.96 : 0.68}"/>
            <text x="${mxW}" y="${(myW + 0.36 * invScale).toFixed(2)}" font-size="${(
          1.12 * invScale
        ).toFixed(2)}" fill="#0284C7" font-weight="700" text-anchor="middle">＋</text>
          </g>
        `;
      }
      ptsArr.forEach(([vx, vy], pIdx) => {
        const isSelPt = isSelRt && pIdx === state.siteMap.selectedRoutePtIdx;
        const vr = +((isSelPt ? 1.38 : 1.08) * invScale).toFixed(2);
        const fillPt =
          pIdx === 0
            ? "#16A34A"
            : isSelPt
            ? "#FF9500"
            : isSelRt
            ? "#0284C7"
            : "#475569";
        html += `
          <circle cx="${vx}" cy="${vy}" r="${vr}"
                  fill="${fillPt}" stroke="#FFFFFF" stroke-width="${(0.38 * invScale).toFixed(2)}"
                  class="draggable-node" data-drag-kind="route-pt" data-route-idx="${rIdx}" data-pt-idx="${pIdx}">
            <title>${pIdx === 0 ? "行车起点" : `路线节点 #${pIdx + 1}`}（右键删除）</title>
          </circle>
        `;
      });
    }
  });

  // Active new-route drawing preview
  if (isEditing && state.siteMap.drawingRoute && state.siteMap.newRoutePts.length > 0) {
    const draftWorld = state.siteMap.newRoutePts.map(([rx, ry]) => [px(rx), py(ry)]);
    const previewPts = state.siteMap.cursorWorld
      ? [...draftWorld, state.siteMap.cursorWorld]
      : draftWorld;
    const pStr = previewPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
    html += `
      <polyline points="${pStr}" fill="none" stroke="rgba(2, 132, 199, 0.85)" stroke-width="0.85" stroke-linecap="round" stroke-dasharray="1.6,1.0"/>
      ${renderChevronAlongSegments(previewPts, "#0284C7", 1.1, false)}
    `;
    draftWorld.forEach(([vx, vy], i) => {
      html += `<circle cx="${vx.toFixed(2)}" cy="${vy.toFixed(2)}" r="${(
        1.12 * invScale
      ).toFixed(2)}" fill="${i === 0 ? "#16A34A" : "#0284C7"}" stroke="#FFFFFF" stroke-width="0.35"/>`;
    });
  }

  // Active Driver Recommended Guidance Route Highlight (Browse mode — Navigation Blue to distinguish from Emerald pedestrian walkways)
  if (
    !isEditing &&
    state.lastRecommendedRoute?.points &&
    state.lastRecommendedRoute.points.length >= 2
  ) {
    const recPts = state.lastRecommendedRoute.points.map(([rx, ry]) => [px(rx), py(ry)]);
    const recStr = recPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
    const [sx0, sy0] = recPts[0];
    const gateLabel = state.lastRecommendedRoute.entry_gate_name
      ? `🚗 ${state.lastRecommendedRoute.entry_gate_name}行车引导`
      : "🚗 进场行车引导";
    const gPillW = Math.max(7.2, gateLabel.length * 1.12 + 1.6) * invScale;
    const gPillH = 1.92 * invScale;
    const gLblY = Math.max(gPillH * 0.7, sy0 - 1.55 * invScale);
    html += `
      <g class="guidance-route-group" pointer-events="none">
        <polyline points="${recStr}" fill="none" stroke="rgba(0, 113, 227, 0.22)" stroke-width="${(
      1.85 * invScale
    ).toFixed(2)}" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${recStr}" fill="none" stroke="#0071E3" stroke-width="${(
      0.68 * invScale
    ).toFixed(2)}" stroke-dasharray="${(1.35 * invScale).toFixed(2)},${(
      0.75 * invScale
    ).toFixed(2)}" class="active-guidance-route" stroke-linecap="round" stroke-linejoin="round"/>
        ${renderChevronAlongSegments(recPts, "#1D4ED8", 1.05 * invScale, false)}
        <circle cx="${sx0.toFixed(2)}" cy="${sy0.toFixed(2)}" r="${(0.78 * invScale).toFixed(
      2
    )}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(0.28 * invScale).toFixed(2)}"/>
        <rect x="${(sx0 - gPillW / 2).toFixed(2)}" y="${(gLblY - gPillH / 2).toFixed(
      2
    )}" width="${gPillW.toFixed(2)}" height="${gPillH.toFixed(2)}" rx="${(
      gPillH / 2
    ).toFixed(2)}" fill="rgba(0, 113, 227, 0.92)" stroke="#FFFFFF" stroke-width="${(
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

  // 2. Individual Parking Stalls Layer (Zero overlapping zone boxes!)
  const selectedSpotsInStudio =
    isEditing && subTool === "spots" ? getSelectedSpots() : [];
  const selectedSpotSet = new Set(selectedSpotsInStudio.map((s) => s.spot_id));

  spotsToDraw.forEach((sp, idx) => {
    const sx = px(sp.map_x ?? 50);
    const sy = py(sp.map_y ?? 50);
    const w = Math.max(0.85, Number(sp.map_w || (sp.spot_type === "elastic_strip" ? 7.8 : 1.75)));
    const h = Math.max(0.85, Number(sp.map_h || (sp.spot_type === "elastic_strip" ? 2.05 : 3.25)));
    const angle = Number(sp.map_angle || 0);

    const effSt = sp.effective_status || sp.status || "FREE";
    const obsType = sp.obstacle_type || "none";
    let fill = "#24B83B";
    if (effSt === "FREE" && obsType === "movable") fill = "#0D9488";
    else if (effSt === "FREE" && obsType === "pedestrian") fill = "#0284C7";
    else if (effSt === "OCCUPIED") fill = "#F03226";
    else if (effSt === "BLOCKED") fill = "#F58A00";
    else if (effSt === "UNKNOWN") fill = "#64748B";

    const isRecZone =
      !isEditing &&
      (state.lastRecommendedGroupCode
        ? sp.group_code === state.lastRecommendedGroupCode
        : state.lastRecommendedZoneId && sp.zone_id === state.lastRecommendedZoneId) &&
      effSt === "FREE";
    const isSelSpot = selectedSpotSet.has(sp.spot_id);
    const isSingleSelSpot = isSelSpot && selectedSpotsInStudio.length === 1;
    const isMultiSelMember = isSelSpot && selectedSpotsInStudio.length >= 2;
    const isSelGroup =
      isEditing &&
      subTool === "spots" &&
      getSpotGroupKey(sp) === state.siteMap.selectedGroupCamId;

    const isUnassigned = !sp.camera_id || sp.camera_id === "UNASSIGNED";
    const statusCn = isUnassigned
      ? "待接入摄像头(规划态)"
      : effSt === "FREE" && obsType === "movable"
      ? "可移轻物(可停)"
      : effSt === "FREE" && obsType === "pedestrian"
      ? "临停行人(让行可停)"
      : effSt === "FREE"
      ? "空闲可停"
      : effSt === "OCCUPIED"
      ? "已停车辆"
      : effSt === "BLOCKED"
      ? "死锁禁停"
      : "待抓拍/离线";

    if (isUnassigned) {
      fill = "#475569";
    }

    const spTags = Array.isArray(sp.tags) ? sp.tags : [];
    const spMeta = sp.tag_meta || {};
    const isEvSpot = Boolean(sp.is_ev || spTags.includes("ev_charging"));
    const isRentSpot = Boolean(sp.is_rentable || spTags.includes("rentable"));
    const isPrivSpot = spTags.includes("private");
    const isWideSpot = spTags.includes("large_friendly");
    const isAccSpot = spTags.includes("accessible");
    const activeFilter = state.siteMap.tagFilter || "all";
    const filterMatched =
      activeFilter === "all" || spTags.includes(activeFilter) || (activeFilter === "ev_charging" && isEvSpot);
    const isTagPopSel = Boolean(
      state.tagPopover?.open && state.tagPopover.spotIds?.includes(sp.spot_id)
    );

    const pendTxt = sp.pending_status ? ` · 防抖复核中(${sp.pending_status})` : "";
    const hintTxt = sp.action_hint ? ` · ${sp.action_hint}` : "";
    const evTip = isEvSpot
      ? ` · ⚡新能源充电位${spMeta.charger_power ? `(${spMeta.charger_power})` : ""}`
      : "";
    const rentTip = isRentSpot
      ? ` · 🤝对外招租${
          [spMeta.rental_price, spMeta.rental_window].filter(Boolean).length
            ? `(${[spMeta.rental_price, spMeta.rental_window].filter(Boolean).join(" / ")})`
            : ""
        }`
      : "";
    const privTip = isPrivSpot && !isRentSpot ? " · 🔒产权专属位" : "";
    const grpTag = sp.group_code ? `[${sp.group_code}] ` : "";
    const camTag = isUnassigned ? " · ⏳待绑定相机" : ` · 📷${sp.camera_id}`;
    const tipStr = `${grpTag}车位 ${sp.label}（${sp.zone_name || sp.zone_id}）· ${statusCn}${evTip}${rentTip}${privTip}${camTag}${pendTxt}${hintTxt}（点击管理标签）`;

    const dragAttrs =
      isEditing && subTool === "spots"
        ? `class="draggable-node" data-drag-kind="spot" data-spot-id="${escapeHtml(
            sp.spot_id
          )}"`
        : !isEditing
        ? `class="map-spot-clickable" data-spot-click="${escapeHtml(sp.spot_id)}"`
        : "";

    const outerStroke = isSelSpot || isTagPopSel
      ? "#0071E3"
      : isSelGroup
      ? "rgba(0, 113, 227, 0.85)"
      : isEvSpot
      ? "#059669"
      : isUnassigned
      ? "#F59E0B"
      : "rgba(15, 23, 42, 0.36)";
    const outerStrokeW = isSelSpot || isTagPopSel ? 0.38 : isSelGroup ? 0.28 : isEvSpot ? 0.28 : isUnassigned ? 0.26 : 0.20;
    const outerDash = isUnassigned && !isSelSpot && !isTagPopSel ? `stroke-dasharray="0.65,0.4"` : "";
    const dimOpacityAttr = !filterMatched ? `opacity="0.22"` : "";

    html += `<g transform="translate(${sx.toFixed(2)} ${sy.toFixed(2)}) rotate(${angle.toFixed(
      1
    )})" ${dragAttrs} ${dimOpacityAttr} data-spot-tip="${escapeHtml(tipStr)}">`;

    if (isTagPopSel) {
      html += `<rect x="${(-w / 2 - 0.48).toFixed(2)}" y="${(-h / 2 - 0.48).toFixed(
        2
      )}" width="${(w + 0.96).toFixed(2)}" height="${(h + 0.96).toFixed(
        2
      )}" rx="0.68" fill="rgba(0, 113, 227, 0.22)" stroke="#0071E3" stroke-width="0.34"/>`;
    } else if (activeFilter !== "all" && filterMatched) {
      html += `<rect x="${(-w / 2 - 0.44).toFixed(2)}" y="${(-h / 2 - 0.44).toFixed(
        2
      )}" width="${(w + 0.88).toFixed(2)}" height="${(h + 0.88).toFixed(
        2
      )}" rx="0.64" fill="rgba(16, 185, 129, 0.22)" stroke="#10B981" stroke-width="0.30"/>`;
    } else if (isRecZone) {
      html += `<rect x="${(-w / 2 - 0.45).toFixed(2)}" y="${(-h / 2 - 0.45).toFixed(
        2
      )}" width="${(w + 0.9).toFixed(2)}" height="${(h + 0.9).toFixed(
        2
      )}" rx="0.65" fill="rgba(40, 205, 65, 0.28)" stroke="#28CD41" stroke-width="0.30"/>`;
    }

    // Crisp stall rectangle with high-contrast keyline + subtle inner white rim
    html += `
      <rect x="${(-w / 2).toFixed(2)}" y="${(-h / 2).toFixed(2)}" width="${w.toFixed(
      2
    )}" height="${h.toFixed(2)}" rx="0.36"
            fill="${fill}" stroke="${outerStroke}" stroke-width="${outerStrokeW}" ${outerDash}/>
      <rect x="${(-w / 2 + 0.14).toFixed(2)}" y="${(-h / 2 + 0.14).toFixed(2)}" width="${Math.max(
      0.2,
      w - 0.28
    ).toFixed(2)}" height="${Math.max(0.2, h - 0.28).toFixed(2)}" rx="0.26"
            fill="none" stroke="rgba(255,255,255,0.72)" stroke-width="0.14"/>
    `;

    // Top-down vehicle model silhouette + orientation nose indicator
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
      // Horizontal / Parallel Car Silhouette (nose pointing +X right along driving direction)
      const cw = w * 0.76;
      const ch = h * 0.68;
      html += `
        <rect x="${(-cw / 2).toFixed(2)}" y="${(-ch / 2).toFixed(2)}" width="${cw.toFixed(
        2
      )}" height="${ch.toFixed(2)}" rx="${(ch * 0.28).toFixed(
        2
      )}" fill="rgba(255,255,255,0.16)" stroke="rgba(255,255,255,0.58)" stroke-width="0.13"/>
        <polygon points="${(cw * 0.10).toFixed(2)},${(-ch * 0.36).toFixed(2)} ${(
        cw * 0.32
      ).toFixed(2)},${(-ch * 0.28).toFixed(2)} ${(cw * 0.32).toFixed(2)},${(
        ch * 0.28
      ).toFixed(2)} ${(cw * 0.10).toFixed(2)},${(ch * 0.36).toFixed(
        2
      )}" fill="rgba(255,255,255,0.34)"/>
        <line x1="${(w / 2 - 0.34).toFixed(2)}" y1="${(-h * 0.26).toFixed(
        2
      )}" x2="${(w / 2 - 0.34).toFixed(2)}" y2="${(h * 0.26).toFixed(
        2
      )}" stroke="rgba(255,255,255,0.88)" stroke-width="0.20" stroke-linecap="round"/>
      `;
    } else {
      // Vertical / Angled Car Silhouette (nose pointing -Y top edge)
      const cw = w * 0.70;
      const ch = h * 0.76;
      html += `
        <rect x="${(-cw / 2).toFixed(2)}" y="${(-ch / 2).toFixed(2)}" width="${cw.toFixed(
        2
      )}" height="${ch.toFixed(2)}" rx="${(cw * 0.28).toFixed(
        2
      )}" fill="rgba(255,255,255,0.16)" stroke="rgba(255,255,255,0.58)" stroke-width="0.13"/>
        <polygon points="${(-cw * 0.36).toFixed(2)},${(-ch * 0.10).toFixed(2)} ${(
        cw * 0.36
      ).toFixed(2)},${(-ch * 0.10).toFixed(2)} ${(cw * 0.28).toFixed(2)},${(
        -ch * 0.32
      ).toFixed(2)} ${(-cw * 0.28).toFixed(2)},${(-ch * 0.32).toFixed(
        2
      )}" fill="rgba(255,255,255,0.34)"/>
        <line x1="${(-w * 0.28).toFixed(2)}" y1="${(-h / 2 + 0.36).toFixed(
        2
      )}" x2="${(w * 0.28).toFixed(2)}" y2="${(-h / 2 + 0.36).toFixed(
        2
      )}" stroke="rgba(255,255,255,0.88)" stroke-width="0.20" stroke-linecap="round"/>
      `;
    }

    if (sp.pending_status) {
      html += `<rect x="${(-w / 2 - 0.22).toFixed(2)}" y="${(-h / 2 - 0.22).toFixed(
        2
      )}" width="${(w + 0.44).toFixed(2)}" height="${(h + 0.44).toFixed(
        2
      )}" rx="0.45" fill="none" stroke="#5856D6" stroke-width="0.34" stroke-dasharray="0.65,0.42"/>`;
    }

    // Upright stall label
    const minDim = Math.min(w, h);
    if (sp.spot_type === "elastic_strip") {
      const lblTxt = `${sp.label || "顺停"} 余${sp.free_units ?? 0}/${sp.capacity || 1}`;
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

    // Upright EV Lightning Badge (⚡) & Secondary Operational Badge (租 / 专 / 宽 / ♿)
    const badgeR = Math.max(0.56, Math.min(0.82, minDim * 0.38));
    if (isEvSpot) {
      const bx = +(w * 0.38).toFixed(2);
      const by = +(-h * 0.38).toFixed(2);
      const bs = (badgeR / 0.68).toFixed(2);
      html += `<g transform="translate(${bx} ${by}) rotate(${(-angle).toFixed(1)}) scale(${bs})" pointer-events="none">
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
      html += `<g transform="translate(${sbx} ${sby}) rotate(${(-angle).toFixed(1)})" pointer-events="none">
        <circle cx="0" cy="0" r="${badgeR.toFixed(2)}" fill="${secBadge.bg}" stroke="#FFFFFF" stroke-width="0.15"/>
        <text x="0" y="${(badgeR * 0.38).toFixed(2)}" font-size="${(badgeR * 1.08).toFixed(2)}" fill="#FFFFFF" font-weight="800" text-anchor="middle">${secBadge.txt}</text>
      </g>`;
    }

    if (isMultiSelMember) {
      html += `<rect x="${(-w / 2 - 0.34).toFixed(2)}" y="${(-h / 2 - 0.34).toFixed(
        2
      )}" width="${(w + 0.68).toFixed(2)}" height="${(h + 0.68).toFixed(
        2
      )}" rx="0.52" fill="rgba(0, 113, 227, 0.22)" stroke="#0071E3" stroke-width="0.32" pointer-events="none"/>`;
    }

    // Single-spot Rotation & 4-Corner Proportional Scale + Width/Length Handles
    if (isSingleSelSpot) {
      html += buildSelectedSpotHandlesSvg(sp, w, h, invScale);
    }

    html += `</g>`;
  });

  // 2a. Multi-Car Unified Transform Bounding Box (4-Corner Proportional Scale + Group Rotate + Group Move)
  if (isEditing && subTool === "spots" && selectedSpotsInStudio.length >= 2) {
    html += buildMultiSelectTransformBoxSvg(selectedSpotsInStudio, metrics, invScale);
  }

  // 2b. Group Row Spine + Whole-Row Move & Tail Line-Up Handles (only when 1 stall inside a multi-stall group is selected)
  if (
    isEditing &&
    subTool === "spots" &&
    selectedSpotsInStudio.length === 1 &&
    state.siteMap.selectedGroupCamId
  ) {
    const gKey = state.siteMap.selectedGroupCamId;
    const gSpots = spotsToDraw.filter((s) => getSpotGroupKey(s) === gKey);
    if (gSpots.length >= 2) {
      const gPts = gSpots.map((s) => [px(s.map_x), py(s.map_y)]);
      const spinePts = gPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
      const gcx = gPts.reduce((a, p) => a + p[0], 0) / gPts.length;
      const gcy = gPts.reduce((a, p) => a + p[1], 0) / gPts.length;

      const first = gPts[0];
      const last = gPts[gPts.length - 1];
      const dx = last[0] - first[0];
      const dy = last[1] - first[1];
      const dLen = Math.max(0.01, Math.hypot(dx, dy));
      const ux = dx / dLen;
      const uy = dy / dLen;
      const nxV = -uy;
      const nyV = ux;

      // Offset group move pill neatly beside the row so it doesn't cover individual stalls
      const pillX = gcx + nxV * (2.9 * invScale);
      const pillY = gcy + nyV * (2.9 * invScale);
      const pillW = 8.4 * invScale;
      const pillH = 2.2 * invScale;

      // Tail line-up handle just past the last stall
      const tailX = last[0] + ux * (2.15 * invScale);
      const tailY = last[1] + uy * (2.15 * invScale);

      html += `
        <polyline points="${spinePts}" fill="none" stroke="rgba(0,113,227,0.55)" stroke-width="${(
        0.28 * invScale
      ).toFixed(2)}" stroke-dasharray="${(0.8 * invScale).toFixed(2)},${(
        0.6 * invScale
      ).toFixed(2)}" pointer-events="none"/>
        <line x1="${gcx.toFixed(2)}" y1="${gcy.toFixed(2)}" x2="${pillX.toFixed(
        2
      )}" y2="${pillY.toFixed(2)}" stroke="rgba(0,113,227,0.45)" stroke-width="${(
        0.22 * invScale
      ).toFixed(2)}" pointer-events="none"/>
        <g class="draggable-node" data-drag-kind="spot-group-move" data-group-key="${escapeHtml(
          gKey
        )}">
          <rect x="${(pillX - pillW / 2).toFixed(2)}" y="${(pillY - pillH / 2).toFixed(
        2
      )}" width="${pillW.toFixed(2)}" height="${pillH.toFixed(2)}" rx="${(
        0.95 * invScale
      ).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
        0.28 * invScale
      ).toFixed(2)}"/>
          <text x="${pillX.toFixed(2)}" y="${(pillY + 0.42 * invScale).toFixed(
        2
      )}" font-size="${(1.18 * invScale).toFixed(
        2
      )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">✥ 整排(${gSpots.length})</text>
          <title>按住拖动：整排 ${gSpots.length} 个车位同步平移</title>
        </g>
        <g class="lineup-handle" data-drag-kind="spot-group-line" data-group-key="${escapeHtml(
          gKey
        )}">
          <line x1="${last[0].toFixed(2)}" y1="${last[1].toFixed(2)}" x2="${tailX.toFixed(
        2
      )}" y2="${tailY.toFixed(2)}" stroke="#FF9500" stroke-width="${(
        0.32 * invScale
      ).toFixed(2)}"/>
          <circle cx="${tailX.toFixed(2)}" cy="${tailY.toFixed(2)}" r="${(
        0.95 * invScale
      ).toFixed(2)}" fill="#FF9500" stroke="#FFFFFF" stroke-width="${(
        0.32 * invScale
      ).toFixed(2)}"/>
          <text x="${tailX.toFixed(2)}" y="${(tailY + 0.36 * invScale).toFixed(
        2
      )}" font-size="${(1.05 * invScale).toFixed(
        2
      )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">⤡</text>
          <title>按住拖拽：以首车位为起点，将整排 ${
            gSpots.length
          } 个车位沿鼠标方向等距一字排开并自动对齐角度</title>
        </g>
      `;
    }
  }

  // 2c. Compact Soft-Lock Badges, Walking Path from 🚪 Unit Door & Named Group Badges (Browse mode)
  if (!isEditing) {
    // Active walking path from selected unit's 🚪 entrance door to nearest/recommended stall group
    let walkObj =
      state.lastDoorWalkPath && state.lastDoorWalkPath.unit_id === selectedUnitId
        ? state.lastDoorWalkPath
        : null;
    if (!walkObj && selectedUnitId && Array.isArray(data.spot_groups)) {
      const candidates = data.spot_groups.filter(
        (g) => g.map_placed !== false && g.selected_door_walk?.path_points?.length >= 2
      );
      const pref = candidates
        .slice()
        .sort((a, b) => {
          const aFree = Number(a.effective_free || 0) > 0 ? 0 : 1;
          const bFree = Number(b.effective_free || 0) > 0 ? 0 : 1;
          if (aFree !== bFree) return aFree - bFree;
          return (
            Number(a.selected_door_walk?.walk_meters ?? 9999) -
            Number(b.selected_door_walk?.walk_meters ?? 9999)
          );
        })[0];
      if (pref) {
        walkObj = {
          unit_id: selectedUnitId,
          path_points: pref.selected_door_walk.path_points,
          walk_meters: pref.selected_door_walk.walk_meters,
          walk_seconds: pref.selected_door_walk.walk_seconds,
          label: pref.group_name || pref.group_code,
        };
      }
    }

    if (walkObj?.path_points?.length >= 2) {
      const wPts = walkObj.path_points.map(([wx, wy]) => [px(wx), py(wy)]);
      const wStr = wPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
      let totalArc = 0;
      for (let i = 0; i < wPts.length - 1; i++) {
        totalArc += Math.hypot(wPts[i + 1][0] - wPts[i][0], wPts[i + 1][1] - wPts[i][1]);
      }
      let halfArc = totalArc * 0.5;
      let mxLbl = (wPts[0][0] + wPts[wPts.length - 1][0]) / 2;
      let myLbl = (wPts[0][1] + wPts[wPts.length - 1][1]) / 2;
      for (let i = 0; i < wPts.length - 1; i++) {
        const seg = Math.hypot(wPts[i + 1][0] - wPts[i][0], wPts[i + 1][1] - wPts[i][1]);
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
          <rect x="${(mxLbl - pillW / 2).toFixed(2)}" y="${(myLbl - pillH / 2).toFixed(
        2
      )}" width="${pillW.toFixed(2)}" height="${pillH.toFixed(2)}" rx="${(
        pillH / 2
      ).toFixed(2)}" fill="rgba(15, 118, 110, 0.94)" stroke="#FFFFFF" stroke-width="${(
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

    // Clickable Named Stall Group Chips (click directly on map to rename & bind camera!)
    (data.spot_groups || []).forEach((grp) => {
      if (grp.map_placed === false || !grp.spot_count) return;
      const gSpots = spotsToDraw.filter(
        (s) => (s.group_code || "") === grp.group_code
      );
      if (!gSpots.length) return;
      const gPts = gSpots.map((s) => [px(s.map_x), py(s.map_y)]);
      const gcx = gPts.reduce((a, p) => a + p[0], 0) / gPts.length;
      const minGY = Math.min(...gPts.map((p) => p[1])) - 2.45 * invScale;
      const dispName = grp.group_name || grp.group_code;
      const shortDisp = dispName.length > 9 ? `${dispName.slice(0, 8)}…` : dispName;
      const statusIcon = grp.is_bound ? `📷余${grp.effective_free}` : "⏳绑机位";
      const chipTxt = `${shortDisp} · ${statusIcon}`;
      const bw = Math.max(7.4, chipTxt.length * 1.06 + 1.8) * invScale;
      const bh = 1.95 * invScale;
      const bgFill = grp.is_bound
        ? "rgba(15, 23, 42, 0.84)"
        : "rgba(180, 83, 9, 0.92)";
      const strokeCol = grp.is_bound ? "rgba(255,255,255,0.86)" : "#FDE68A";
      html += `
        <g class="spot-group-badge-hit" data-group-badge="${escapeHtml(grp.group_code)}">
          <rect x="${(gcx - bw / 2).toFixed(2)}" y="${(minGY - bh / 2).toFixed(
        2
      )}" width="${bw.toFixed(2)}" height="${bh.toFixed(2)}" rx="${(
        bh / 2
      ).toFixed(2)}" fill="${bgFill}" stroke="${strokeCol}" stroke-width="${(
        0.24 * invScale
      ).toFixed(2)}"/>
          <text x="${gcx.toFixed(2)}" y="${(minGY + 0.36 * invScale).toFixed(
        2
      )}" font-size="${(1.02 * invScale).toFixed(
        2
      )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">${escapeHtml(
        chipTxt
      )}</text>
          <title>点击命名「${escapeHtml(
            grp.group_name || grp.group_code
          )}」或一对一绑定摄像头机位</title>
        </g>
      `;
    });

    const groupLocksByZone = {};
    (data.spot_groups || []).forEach((grp) => {
      if (grp.map_placed === false || !grp.spot_count || !(grp.soft_locked > 0)) return;
      const gSpots = spotsToDraw.filter((s) => (s.group_code || "") === grp.group_code);
      if (!gSpots.length) return;
      groupLocksByZone[grp.zone_id] =
        (groupLocksByZone[grp.zone_id] || 0) + Number(grp.soft_locked || 0);
      const gPts = gSpots.map((s) => [px(s.map_x), py(s.map_y)]);
      const gcx = gPts.reduce((a, p) => a + p[0], 0) / gPts.length;
      const minGY = Math.min(...gPts.map((p) => p[1])) - 4.65 * invScale;
      const pillW = 6.2 * invScale;
      const pillH = 1.9 * invScale;
      html += `
        <g pointer-events="none">
          <rect x="${(gcx - pillW / 2).toFixed(2)}" y="${(minGY - pillH / 2).toFixed(
        2
      )}" width="${pillW.toFixed(2)}" height="${pillH.toFixed(2)}" rx="${(
        pillH / 2
      ).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
        0.24 * invScale
      ).toFixed(2)}"/>
          <text x="${gcx.toFixed(2)}" y="${(minGY + 0.36 * invScale).toFixed(
        2
      )}" font-size="${(1.06 * invScale).toFixed(
        2
      )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">在途×${
        grp.soft_locked
      }</text>
        </g>
      `;
    });

    (data.zones || []).forEach((z) => {
      const remLocked =
        Number(z.soft_locked || 0) - Number(groupLocksByZone[z.zone_id] || 0);
      const placedZSpots = (z.spots || []).filter((s) => s.map_placed !== false);
      if (remLocked > 0 && placedZSpots.length) {
        const zPts = placedZSpots.map((s) => [px(s.map_x ?? z.map_x), py(s.map_y ?? z.map_y)]);
        const cx = zPts.reduce((a, p) => a + p[0], 0) / zPts.length;
        const minY = Math.min(...zPts.map((p) => p[1])) - 4.4 * invScale;
        html += `
          <g pointer-events="none">
            <rect x="${(cx - 3.9).toFixed(2)}" y="${(minY - 1.2).toFixed(
          2
        )}" width="7.8" height="2.3" rx="1.1" fill="#0071E3" stroke="#FFFFFF" stroke-width="0.25"/>
            <text x="${cx.toFixed(2)}" y="${(minY + 0.35).toFixed(
          2
        )}" font-size="1.35" fill="#FFFFFF" font-weight="700" text-anchor="middle">在途×${remLocked}</text>
          </g>
        `;
      }
    });
  }

  // 3. Compact Entrance Gate Pills
  for (const g of gates) {
    const gx = px(Number(g.x ?? 50));
    const gy = py(Number(g.y ?? 96));
    const labelStr = g.name || "出入口";
    const gw = Math.max(5.8, Math.min(11.5, labelStr.length * 1.65 + 2.2)) * invScale;
    const gh = 2.45 * invScale;
    const isSelGate =
      isEditing &&
      subTool === "nodes" &&
      state.siteMap.selectedNodeKind === "gate" &&
      state.siteMap.selectedNodeId === g.gate_id;
    const gDrag =
      isEditing && subTool === "nodes"
        ? `class="draggable-node" data-drag-kind="gate" data-drag-id="${escapeHtml(
            g.gate_id
          )}"`
        : "";

    html += `
      <g ${gDrag}>
        <rect x="${(gx - gw / 2).toFixed(2)}" y="${(gy - gh / 2).toFixed(
      2
    )}" width="${gw.toFixed(2)}" height="${gh.toFixed(2)}" rx="${(gh / 2).toFixed(2)}"
              fill="#0071E3" stroke="${isSelGate ? "#FF9500" : "#FFFFFF"}" stroke-width="${(
      (isSelGate ? 0.48 : 0.30) * invScale
    ).toFixed(2)}"/>
        <text x="${gx.toFixed(2)}" y="${(gy + 0.46 * invScale).toFixed(2)}" font-size="${(
      1.32 * invScale
    ).toFixed(2)}" fill="#FFFFFF" font-weight="600" text-anchor="middle">${escapeHtml(
      labelStr
    )}</text>
      </g>
    `;
  }

  // 4. Compact Building Unit Badges + Draggable 🚪 Unit Entrance Door Pins
  for (const u of unitsToDraw) {
    const isHome = u.unit_id === selectedUnitId;
    const isSelUnit =
      isEditing &&
      subTool === "nodes" &&
      state.siteMap.selectedNodeKind === "unit" &&
      state.siteMap.selectedNodeId === u.unit_id;
    const ux = px(u.map_x);
    const uy = py(u.map_y);
    const dx = px(u.door_x ?? u.map_x);
    const dy = py(u.door_y ?? u.map_y);
    const compactLabel = formatCompactUnitBadgeLabel(u, unitsToDraw);
    const shortText = `${isHome ? "★ " : ""}${compactLabel}`;
    const uw = Math.max(5.6, shortText.length * 1.28 + 1.8) * invScale;
    const uh = 2.55 * invScale;

    const uAttr =
      isEditing && subTool === "nodes"
        ? `class="draggable-node" data-drag-kind="unit" data-drag-id="${escapeHtml(
            u.unit_id
          )}"`
        : !isEditing
        ? `class="clickable-unit" data-unit-click="${escapeHtml(u.unit_id)}"`
        : "";

    const doorAttr =
      isEditing && subTool === "nodes"
        ? `class="unit-door-pin is-draggable" data-drag-kind="unit-door" data-drag-id="${escapeHtml(
            u.unit_id
          )}"`
        : !isEditing
        ? `class="unit-door-pin clickable-unit" data-unit-click="${escapeHtml(u.unit_id)}"`
        : `class="unit-door-pin" pointer-events="none"`;

    const doorDistPx = Math.hypot(dx - ux, dy - uy);
    if (doorDistPx > 0.35) {
      html += `
        <line x1="${ux.toFixed(2)}" y1="${uy.toFixed(2)}" x2="${dx.toFixed(
        2
      )}" y2="${dy.toFixed(2)}" stroke="${
        isHome || isSelUnit ? "#0D9488" : "rgba(13, 148, 136, 0.62)"
      }" stroke-width="${((isHome || isSelUnit ? 0.34 : 0.24) * invScale).toFixed(
        2
      )}" stroke-dasharray="${(0.65 * invScale).toFixed(2)},${(
        0.45 * invScale
      ).toFixed(2)}" pointer-events="none"/>
      `;
    }

    const doorR = (isHome || isSelUnit ? 1.08 : 0.88) * invScale;
    html += `
      <g ${doorAttr}>
        <circle cx="${dx.toFixed(2)}" cy="${dy.toFixed(2)}" r="${doorR.toFixed(
      2
    )}" fill="${isHome ? "#0D9488" : isSelUnit ? "#F59E0B" : "#14B8A6"}" stroke="#FFFFFF" stroke-width="${(
      0.28 * invScale
    ).toFixed(2)}"/>
        <text x="${dx.toFixed(2)}" y="${(dy + 0.36 * invScale).toFixed(
      2
    )}" font-size="${(0.95 * invScale).toFixed(
      2
    )}" fill="#FFFFFF" text-anchor="middle" font-weight="800">门</text>
        <title>🚪 ${escapeHtml(
          u.name || compactLabel
        )} 单元门入户点${
      isEditing && subTool === "nodes"
        ? "（按住可拖拽到真实单元门口，步行距离以此为准）"
        : "（步行测距基准点，点击切换为目的地单元）"
    }</title>
      </g>
      <g ${uAttr}>
        <rect x="${(ux - uw / 2).toFixed(2)}" y="${(uy - uh / 2).toFixed(
      2
    )}" width="${uw.toFixed(2)}" height="${uh.toFixed(2)}" rx="${(0.85 * invScale).toFixed(2)}"
              fill="${isHome ? "#0071E3" : "rgba(255, 255, 255, 0.94)"}"
              stroke="${
                isSelUnit ? "#FF9500" : isHome ? "#FFFFFF" : "#475569"
              }" stroke-width="${((isSelUnit ? 0.48 : 0.34) * invScale).toFixed(2)}"/>
        <text x="${ux.toFixed(2)}" y="${(uy + 0.48 * invScale).toFixed(2)}" font-size="${(
      1.32 * invScale
    ).toFixed(2)}"
              fill="${isHome ? "#FFFFFF" : "#0F172A"}" text-anchor="middle" font-weight="700">
          ${escapeHtml(shortText)}
        </text>
      </g>
    `;
  }

  html += `<g id="mainMarqueeBox" pointer-events="none"></g>`;
  html += `<g id="mainSpotPlacePreview" pointer-events="none"></g>`;
  html += `<g id="mainSyncCrosshair" pointer-events="none"></g>`;
  svg.innerHTML = html;

  if (state.siteMap.compareSplit) {
    renderReferenceFloorPlanSvg(data, {
      metrics,
      zoom,
      invScale,
      roads,
      routes,
      walkways,
      spotsToDraw,
      unitsToDraw,
      gates,
      isEditing,
    });
  }
  updateSyncCrosshairDom(state.siteMap.syncCursorWorld);
  if (state.siteMap.placeMode && state.siteMap.syncCursorWorld && !state.siteMap.dragging) {
    updateSpotPlacePreviewDom(state.siteMap.syncCursorWorld, null, state.siteMap.placeMode);
  }
}

function buildMultiSelectTransformBoxSvg(selectedSpots, metrics, invScale) {
  const geom = computeMultiSelectBoxGeometry(selectedSpots, metrics, invScale);
  if (!geom) return "";
  const { minX, maxX, minY, maxY, bw, bh, cxWorld, cyWorld } = geom;
  const stemY = minY - 2.15 * invScale;
  const hr = +(0.84 * invScale).toFixed(2);
  const cr = +(0.68 * invScale).toFixed(2);
  const pillW = Math.max(9.6, 10.8) * invScale;
  const pillH = 2.15 * invScale;
  const pillY = maxY + 1.85 * invScale;

  const corners = [
    [minX, minY, "nw"],
    [maxX, minY, "ne"],
    [minX, maxY, "sw"],
    [maxX, maxY, "se"],
  ];
  const cornerMarkup = corners
    .map(
      ([cx, cy, pos]) => `
      <circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${cr}" fill="#FFFFFF" stroke="#FF9500" stroke-width="${(
        0.34 * invScale
      ).toFixed(
        2
      )}" class="multi-scale-handle" data-drag-kind="multi-spot-scale" data-corner="${pos}">
        <title>按住四角拖拽：${selectedSpots.length} 辆已选车同步等比缩放 ⤡</title>
      </circle>`
    )
    .join("");

  const movePillW = 10.6 * invScale;
  const donePillW = 6.8 * invScale;
  const pillGap = 0.65 * invScale;
  const totalPillsW = movePillW + pillGap + donePillW;
  const movePillCx = cxWorld - totalPillsW / 2 + movePillW / 2;
  const donePillCx = cxWorld + totalPillsW / 2 - donePillW / 2;

  return `
    <g class="multi-select-transform-box">
      <rect x="${minX.toFixed(2)}" y="${minY.toFixed(2)}" width="${bw.toFixed(
    2
  )}" height="${bh.toFixed(2)}" rx="${(0.65 * invScale).toFixed(
    2
  )}" fill="rgba(0, 113, 227, 0.06)" stroke="#0071E3" stroke-width="${(
    0.34 * invScale
  ).toFixed(2)}" stroke-dasharray="${(0.95 * invScale).toFixed(2)},${(
    0.62 * invScale
  ).toFixed(2)}" pointer-events="none"/>
      <line x1="${cxWorld.toFixed(2)}" y1="${minY.toFixed(
    2
  )}" x2="${cxWorld.toFixed(2)}" y2="${stemY.toFixed(
    2
  )}" stroke="#0071E3" stroke-width="${(0.32 * invScale).toFixed(
    2
  )}" pointer-events="none"/>
      <circle cx="${cxWorld.toFixed(2)}" cy="${stemY.toFixed(
    2
  )}" r="${hr}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
    0.32 * invScale
  ).toFixed(2)}" class="multi-rotate-handle" data-drag-kind="multi-spot-rotate">
        <title>按住旋转手柄：${selectedSpots.length} 辆已选车同步旋转 ↻（Shift 按 15° 吸附）</title>
      </circle>
      ${cornerMarkup}
      <g class="multi-move-pill" data-drag-kind="multi-spot-move">
        <rect x="${(movePillCx - movePillW / 2).toFixed(2)}" y="${(pillY - pillH / 2).toFixed(
    2
  )}" width="${movePillW.toFixed(2)}" height="${pillH.toFixed(2)}" rx="${(
    0.95 * invScale
  ).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
    0.28 * invScale
  ).toFixed(2)}"/>
        <text x="${movePillCx.toFixed(2)}" y="${(pillY + 0.42 * invScale).toFixed(
    2
  )}" font-size="${(1.10 * invScale).toFixed(
    2
  )}" fill="#FFFFFF" font-weight="700" text-anchor="middle">✥ 已选 ${
    selectedSpots.length
  } 辆 · 拖角缩放</text>
        <title>按住拖动：已选 ${selectedSpots.length} 辆车同步平移</title>
      </g>
      <g class="multi-finish-pill" data-drag-kind="finish-spot-group">
        <rect x="${(donePillCx - donePillW / 2).toFixed(2)}" y="${(pillY - pillH / 2).toFixed(
    2
  )}" width="${donePillW.toFixed(2)}" height="${pillH.toFixed(2)}" rx="${(
    0.95 * invScale
  ).toFixed(2)}" fill="#059669" stroke="#FFFFFF" stroke-width="${(
    0.28 * invScale
  ).toFixed(2)}"/>
        <text x="${donePillCx.toFixed(2)}" y="${(pillY + 0.42 * invScale).toFixed(
    2
  )}" font-size="${(1.10 * invScale).toFixed(
    2
  )}" fill="#FFFFFF" font-weight="800" text-anchor="middle">✓ 完成本组</text>
        <title>点击锁定本组车位，随后可继续拖放下一组独立车位</title>
      </g>
    </g>
  `;
}

function buildSelectedSpotHandlesSvg(sp, w, h, invScale) {
  const stemY = -h / 2 - 1.65 * invScale;
  const hr = +(0.72 * invScale).toFixed(2);
  const cr = +(0.52 * invScale).toFixed(2);
  const barT = +(0.46 * invScale).toFixed(2);
  const barL = +(1.05 * invScale).toFixed(2);
  const sid = escapeHtml(sp.spot_id);
  const corners = [
    [-w / 2, -h / 2],
    [w / 2, -h / 2],
    [-w / 2, h / 2],
  ];
  const cornerHtml = corners
    .map(
      ([cx, cy]) => `
        <circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${cr}" fill="#FFFFFF" stroke="#FF9500" stroke-width="${(
        0.28 * invScale
      ).toFixed(2)}" class="resize-handle" data-drag-kind="spot-scale" data-spot-id="${sid}">
          <title>拖拽顶角等比缩放车型大小 ⤡</title>
        </circle>`
    )
    .join("");

  return `
    <line x1="0" y1="${(-h / 2).toFixed(2)}" x2="0" y2="${stemY.toFixed(
    2
  )}" stroke="#0071E3" stroke-width="${(0.30 * invScale).toFixed(2)}" pointer-events="none"/>
    <circle cx="0" cy="${stemY.toFixed(2)}" r="${hr}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
    0.28 * invScale
  ).toFixed(2)}" class="rotate-handle" data-drag-kind="spot-rotate" data-spot-id="${sid}">
      <title>拖拽自由旋转车头方向 ↻（按住 Shift 按 15° 吸附）</title>
    </circle>
    ${cornerHtml}
    <rect x="${(w / 2 - barT / 2).toFixed(2)}" y="${(-barL / 2).toFixed(
    2
  )}" width="${barT}" height="${barL}" rx="0.2" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
    0.22 * invScale
  ).toFixed(2)}" class="resize-w-handle" data-drag-kind="spot-resize-w" data-spot-id="${sid}">
      <title>左右拖拽单独调整车位宽度 ↔</title>
    </rect>
    <rect x="${(-barL / 2).toFixed(2)}" y="${(h / 2 - barT / 2).toFixed(
    2
  )}" width="${barL}" height="${barT}" rx="0.2" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
    0.22 * invScale
  ).toFixed(2)}" class="resize-h-handle" data-drag-kind="spot-resize-h" data-spot-id="${sid}">
      <title>上下拖拽单独调整车位长度 ↕</title>
    </rect>
    <rect x="${(w / 2 - 0.48 * invScale).toFixed(2)}" y="${(
    h / 2 -
    0.48 * invScale
  ).toFixed(2)}" width="${(0.96 * invScale).toFixed(2)}" height="${(
    0.96 * invScale
  ).toFixed(2)}" rx="0.22" fill="#FFFFFF" stroke="#0071E3" stroke-width="${(
    0.28 * invScale
  ).toFixed(2)}" class="resize-handle" data-drag-kind="spot-resize" data-spot-id="${sid}">
      <title>拖拽右下角自由调整长宽尺寸</title>
    </rect>
  `;
}

function renderReferenceFloorPlanSvg(data, ctx) {
  const refSvg = document.getElementById("communityRefSvg");
  if (!refSvg || !data) return;
  const { metrics, invScale, roads, routes, walkways, spotsToDraw, unitsToDraw, isEditing } = ctx;
  const { worldW, worldH, px, py } = metrics;

  let refHtml = `
    <rect x="0.25" y="0.25" width="${(worldW - 0.5).toFixed(2)}" height="${(
    worldH - 0.5
  ).toFixed(2)}" rx="2.6" fill="#F1F5F9" stroke="#CBD5E1" stroke-width="0.4"/>
  `;

  if (state.siteMap.backdropDataUrl) {
    refHtml += `
      <image href="${state.siteMap.backdropDataUrl}" x="0.25" y="0.25" width="${(
      worldW - 0.5
    ).toFixed(2)}" height="${(worldH - 0.5).toFixed(
      2
    )}" preserveAspectRatio="none" opacity="1"/>
    `;
  } else {
    // Architectural blueprint coordinate reference grid when no original image has been uploaded yet
    for (let gx = 10; gx < 100; gx += 10) {
      const xPos = px(gx);
      refHtml += `<line x1="${xPos}" y1="0.4" x2="${xPos}" y2="${(worldH - 0.4).toFixed(
        2
      )}" stroke="rgba(148,163,184,0.28)" stroke-width="0.2" stroke-dasharray="0.9,0.9"/>`;
    }
    for (let gy = 10; gy < 100; gy += 10) {
      const yPos = py(gy);
      refHtml += `<line x1="0.4" y1="${yPos}" x2="${(worldW - 0.4).toFixed(
        2
      )}" y2="${yPos}" stroke="rgba(148,163,184,0.28)" stroke-width="0.2" stroke-dasharray="0.9,0.9"/>`;
    }
    // Baseline saved roads & units in light blueprint tone so user can still compare draft edits against baseline
    const baseRoads = data.site_map?.topology?.roads || roads || [];
    baseRoads.forEach((r) => {
      const pts = (r.points || []).map(([rx, ry]) => `${px(rx)},${py(ry)}`).join(" ");
      if (pts) {
        refHtml += `<polyline points="${pts}" fill="none" stroke="rgba(148,163,184,0.45)" stroke-width="${Math.max(
          1.8,
          Number(r.width || 4.4)
        )}" stroke-linecap="round" stroke-linejoin="round"/>`;
      }
    });
    (data.units || []).forEach((u) => {
      const ux = px(u.map_x);
      const uy = py(u.map_y);
      refHtml += `
        <g opacity="0.68" pointer-events="none">
          <rect x="${(ux - 3.6).toFixed(2)}" y="${(uy - 1.35).toFixed(
        2
      )}" width="7.2" height="2.7" rx="0.7" fill="#E2E8F0" stroke="#94A3B8" stroke-width="0.25"/>
          <text x="${ux.toFixed(2)}" y="${(uy + 0.42).toFixed(
        2
      )}" font-size="1.25" fill="#475569" font-weight="600" text-anchor="middle">${escapeHtml(
        u.unit_id
      )}栋</text>
        </g>
      `;
    });
    const cx = (worldW / 2).toFixed(2);
    const cy = (worldH / 2).toFixed(2);
    refHtml += `
      <g pointer-events="none">
        <rect x="${(worldW / 2 - 28).toFixed(2)}" y="${(worldH / 2 - 4.2).toFixed(
      2
    )}" width="56" height="8.4" rx="2" fill="rgba(255,255,255,0.9)" stroke="#CBD5E1" stroke-width="0.3"/>
        <text x="${cx}" y="${(worldH / 2 - 0.6).toFixed(
      2
    )}" font-size="1.65" fill="#0F172A" font-weight="700" text-anchor="middle">尚未上传小区原始平面图</text>
        <text x="${cx}" y="${(worldH / 2 + 2.1).toFixed(
      2
    )}" font-size="1.25" fill="#64748B" text-anchor="middle">点击左栏右上角「上传平面图」即可在此 100% 高清对照原图拖拽摆位</text>
      </g>
    `;
  }

  // Optional non-intrusive alignment wireframes over the reference floor plan
  if (state.siteMap.refWireframe) {
    (roads || []).forEach((r, idx) => {
      const pts = (r.points || []).map(([rx, ry]) => `${px(rx)},${py(ry)}`).join(" ");
      if (!pts) return;
      const isSelR = isEditing && state.siteMap.subTool === "roads" && idx === state.siteMap.selectedRoadIdx;
      refHtml += `
        <polyline points="${pts}" fill="none" stroke="${
        isSelR ? "rgba(245,158,11,0.92)" : "rgba(14,165,233,0.72)"
      }" stroke-width="${((isSelR ? 0.45 : 0.30) * invScale).toFixed(
        2
      )}" stroke-dasharray="${(1.1 * invScale).toFixed(2)},${(0.75 * invScale).toFixed(
        2
      )}" stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>
      `;
    });
    (walkways || []).forEach((wk, idx) => {
      const pts = (wk.points || []).map(([wx, wy]) => `${px(wx)},${py(wy)}`).join(" ");
      if (!pts) return;
      const isSelWk =
        isEditing && state.siteMap.subTool === "walkways" && idx === state.siteMap.selectedWalkwayIdx;
      refHtml += `
        <polyline points="${pts}" fill="none" stroke="${
        isSelWk ? "rgba(5,150,105,0.95)" : "rgba(16,185,129,0.78)"
      }" stroke-width="${((isSelWk ? 0.42 : 0.28) * invScale).toFixed(
        2
      )}" stroke-dasharray="${(0.85 * invScale).toFixed(2)},${(0.65 * invScale).toFixed(
        2
      )}" stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>
      `;
    });

    const refSelectedSpots =
      isEditing && state.siteMap.subTool === "spots" ? getSelectedSpots() : [];
    const refSelectedSet = new Set(refSelectedSpots.map((s) => s.spot_id));

    (spotsToDraw || []).forEach((sp, idx) => {
      const sx = px(sp.map_x ?? 50);
      const sy = py(sp.map_y ?? 50);
      const w = Math.max(0.85, Number(sp.map_w || 1.75));
      const h = Math.max(0.85, Number(sp.map_h || 3.25));
      const ang = Number(sp.map_angle || 0);
      const isSel = refSelectedSet.has(sp.spot_id);
      const isSingleSel = isSel && refSelectedSpots.length === 1;
      const isGrp =
        isEditing &&
        !isSel &&
        state.siteMap.selectedGroupCamId &&
        getSpotGroupKey(sp) === state.siteMap.selectedGroupCamId;
      const shortNo = shortenSpotLabel(sp.label, idx);
      const strokeCol = isSel
        ? "#F59E0B"
        : isGrp
        ? "#0071E3"
        : "rgba(2, 132, 199, 0.88)";
      const fillCol = isSel
        ? "rgba(245, 158, 11, 0.22)"
        : isGrp
        ? "rgba(0, 113, 227, 0.10)"
        : "rgba(14, 165, 233, 0.05)";
      const dashAttr = isSel
        ? ""
        : `stroke-dasharray="${(0.65 * invScale).toFixed(2)},${(0.45 * invScale).toFixed(2)}"`;
      const interactiveAttr = isEditing
        ? `data-ref-spot-id="${escapeHtml(sp.spot_id)}" style="cursor:grab"`
        : `pointer-events="none"`;

      const isParallel = w > h * 1.2;
      const noseMarkup = isParallel
        ? `<line x1="${(w * 0.36).toFixed(2)}" y1="${(-h * 0.34).toFixed(2)}" x2="${(
            w * 0.36
          ).toFixed(2)}" y2="${(h * 0.34).toFixed(
            2
          )}" stroke="${strokeCol}" stroke-width="${(0.28 * invScale).toFixed(
            2
          )}" stroke-linecap="round" pointer-events="none"/>`
        : `<line x1="${(-w * 0.34).toFixed(2)}" y1="${(-h * 0.36).toFixed(2)}" x2="${(
            w * 0.34
          ).toFixed(2)}" y2="${(-h * 0.36).toFixed(
            2
          )}" stroke="${strokeCol}" stroke-width="${(0.28 * invScale).toFixed(
            2
          )}" stroke-linecap="round" pointer-events="none"/>`;

      refHtml += `
        <g transform="translate(${sx.toFixed(2)}, ${sy.toFixed(2)}) rotate(${ang.toFixed(
        1
      )})" ${interactiveAttr}>
          <rect x="${(-w / 2).toFixed(2)}" y="${(-h / 2).toFixed(
        2
      )}" width="${w.toFixed(2)}" height="${h.toFixed(
        2
      )}" rx="0.35" fill="${fillCol}" stroke="${strokeCol}" stroke-width="${(
        (isSel ? 0.44 : 0.26) * invScale
      ).toFixed(2)}" ${dashAttr}/>
          ${noseMarkup}
          <text x="0" y="${(0.35 * invScale).toFixed(2)}" font-size="${(
        Math.min(w, h) * 0.46
      ).toFixed(
        2
      )}" fill="${strokeCol}" font-weight="700" text-anchor="middle" pointer-events="none">${escapeHtml(
        shortNo
      )}</text>
          ${isSingleSel ? buildSelectedSpotHandlesSvg(sp, w, h, invScale) : ""}
        </g>
      `;
    });

    if (isEditing && state.siteMap.subTool === "spots" && refSelectedSpots.length >= 2) {
      refHtml += buildMultiSelectTransformBoxSvg(refSelectedSpots, metrics, invScale);
    }

    // Subtle unit & 🚪 door pins when backdrop image is shown so buildings & entrances align easily
    if (state.siteMap.backdropDataUrl) {
      (unitsToDraw || []).forEach((u) => {
        const ux = px(u.map_x);
        const uy = py(u.map_y);
        const dx = px(u.door_x ?? u.map_x);
        const dy = py(u.door_y ?? u.map_y);
        refHtml += `
          <g pointer-events="none" opacity="0.86">
            <line x1="${ux.toFixed(2)}" y1="${uy.toFixed(2)}" x2="${dx.toFixed(
          2
        )}" y2="${dy.toFixed(2)}" stroke="#0D9488" stroke-width="${(
          0.22 * invScale
        ).toFixed(2)}" stroke-dasharray="${(0.55 * invScale).toFixed(2)},${(
          0.4 * invScale
        ).toFixed(2)}"/>
            <circle cx="${ux.toFixed(2)}" cy="${uy.toFixed(2)}" r="${(
          0.68 * invScale
        ).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(
          0.24 * invScale
        ).toFixed(2)}"/>
            <circle cx="${dx.toFixed(2)}" cy="${dy.toFixed(2)}" r="${(
          0.62 * invScale
        ).toFixed(2)}" fill="#0D9488" stroke="#FFFFFF" stroke-width="${(
          0.22 * invScale
        ).toFixed(2)}"/>
          </g>
        `;
      });
    }
    // Live rubber-band preview when tracing a new route or road on either pane
    if (isEditing && state.siteMap.drawingRoute && state.siteMap.newRoutePts.length > 0) {
      const previewPts = state.siteMap.newRoutePts.map(([rx, ry]) => [px(rx), py(ry)]);
      if (state.siteMap.cursorWorld) previewPts.push(state.siteMap.cursorWorld);
      const ptsStr = previewPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
      refHtml += `<polyline points="${ptsStr}" fill="none" stroke="#10B981" stroke-width="${(
        0.48 * invScale
      ).toFixed(2)}" stroke-dasharray="${(0.9 * invScale).toFixed(2)},${(
        0.6 * invScale
      ).toFixed(2)}" pointer-events="none"/>`;
    }
    if (isEditing && state.siteMap.drawingRoad && state.siteMap.newRoadPts.length > 0) {
      const previewPts = state.siteMap.newRoadPts.map(([rx, ry]) => [px(rx), py(ry)]);
      if (state.siteMap.cursorWorld) previewPts.push(state.siteMap.cursorWorld);
      const ptsStr = previewPts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
      refHtml += `<polyline points="${ptsStr}" fill="none" stroke="#0071E3" stroke-width="${(
        0.55 * invScale
      ).toFixed(2)}" stroke-dasharray="${(1.0 * invScale).toFixed(2)},${(
        0.7 * invScale
      ).toFixed(2)}" pointer-events="none"/>`;
    }
  }

  refHtml += `<g id="refMarqueeBox" pointer-events="none"></g>`;
  refHtml += `<g id="refSpotPlacePreview" pointer-events="none"></g>`;
  refHtml += `<g id="refSyncCrosshair" pointer-events="none"></g>`;
  refSvg.innerHTML = refHtml;
}

function updateSpotPlacePreviewDom(
  anchorWorld,
  endWorld = null,
  sourceMode = "spots",
  overrideModelKey = null
) {
  const mainG = document.getElementById("mainSpotPlacePreview");
  const refG = document.getElementById("refSpotPlacePreview");
  if (!anchorWorld || !state.overview || typeof window.__computeDesignatedSpotsClient !== "function") {
    if (mainG) mainG.innerHTML = "";
    if (refG) refG.innerHTML = "";
    return;
  }

  const items = window.__computeDesignatedSpotsClient(
    anchorWorld,
    endWorld,
    sourceMode,
    overrideModelKey
  );
  if (!items.length) {
    if (mainG) mainG.innerHTML = "";
    if (refG) refG.innerHTML = "";
    return;
  }

  const invScale = Math.pow(state.siteMap.zoom || 1.0, -0.52);
  let markup = "";

  if (endWorld) {
    markup += `
      <line x1="${anchorWorld[0].toFixed(2)}" y1="${anchorWorld[1].toFixed(
      2
    )}" x2="${endWorld[0].toFixed(2)}" y2="${endWorld[1].toFixed(
      2
    )}" stroke="#0071E3" stroke-width="${(0.36 * invScale).toFixed(
      2
    )}" stroke-dasharray="${(0.85 * invScale).toFixed(2)},${(0.55 * invScale).toFixed(2)}"/>
      <circle cx="${anchorWorld[0].toFixed(2)}" cy="${anchorWorld[1].toFixed(2)}" r="${(
      0.55 * invScale
    ).toFixed(2)}" fill="#0071E3" stroke="#FFFFFF" stroke-width="${(0.22 * invScale).toFixed(2)}"/>
      <circle cx="${endWorld[0].toFixed(2)}" cy="${endWorld[1].toFixed(2)}" r="${(
      0.55 * invScale
    ).toFixed(2)}" fill="#10B981" stroke="#FFFFFF" stroke-width="${(0.22 * invScale).toFixed(2)}"/>
    `;
  }

  items.forEach((it, idx) => {
    const { wx, wy, w, h, angle } = it;
    const isParallel = w > h * 1.2;
    const nose = isParallel
      ? `<line x1="${(w * 0.36).toFixed(2)}" y1="${(-h * 0.34).toFixed(2)}" x2="${(
          w * 0.36
        ).toFixed(2)}" y2="${(h * 0.34).toFixed(2)}" stroke="#0284C7" stroke-width="${(
          0.32 * invScale
        ).toFixed(2)}" stroke-linecap="round"/>`
      : `<line x1="${(-w * 0.34).toFixed(2)}" y1="${(-h * 0.36).toFixed(2)}" x2="${(
          w * 0.34
        ).toFixed(2)}" y2="${(-h * 0.36).toFixed(2)}" stroke="#0284C7" stroke-width="${(
          0.32 * invScale
        ).toFixed(2)}" stroke-linecap="round"/>`;
    const numTxt = String(idx + 1).padStart(2, "0");
    markup += `
      <g transform="translate(${wx.toFixed(2)}, ${wy.toFixed(2)}) rotate(${angle.toFixed(
      1
    )})" opacity="0.9">
        <rect x="${(-w / 2).toFixed(2)}" y="${(-h / 2).toFixed(
      2
    )}" width="${w.toFixed(2)}" height="${h.toFixed(
      2
    )}" rx="0.4" fill="rgba(16, 185, 129, 0.24)" stroke="#059669" stroke-width="${(
      0.36 * invScale
    ).toFixed(2)}" stroke-dasharray="${(0.7 * invScale).toFixed(2)},${(
      0.45 * invScale
    ).toFixed(2)}"/>
        ${nose}
        <text x="0" y="${(0.35 * invScale).toFixed(2)}" font-size="${(
      Math.min(w, h) * 0.45
    ).toFixed(2)}" fill="#065F46" font-weight="800" text-anchor="middle">${numTxt}</text>
      </g>
    `;
  });

  // Floating count & independent group badge above the preview cars
  const nextGroupLbl =
    sourceMode === "routes"
      ? items[0]?.cfg?.parsed?.code || items[0]?.cfg?.rawCode || allocateNextGroupCode()
      : allocateNextGroupCode();
  const minPreviewY = Math.min(...items.map((it) => it.wy - Math.max(it.w, it.h) * 0.6));
  const avgPreviewX = items.reduce((s, it) => s + it.wx, 0) / items.length;
  const badgeY = Math.max(2.2, minPreviewY - 1.55 * invScale);
  const badgeW = 9.4 * invScale;
  const badgeH = 1.95 * invScale;
  markup += `
    <g pointer-events="none">
      <rect x="${(avgPreviewX - badgeW / 2).toFixed(2)}" y="${(
    badgeY - badgeH / 2
  ).toFixed(2)}" width="${badgeW.toFixed(2)}" height="${badgeH.toFixed(2)}" rx="${(
    0.85 * invScale
  ).toFixed(2)}" fill="#059669" stroke="#FFFFFF" stroke-width="${(0.24 * invScale).toFixed(
    2
  )}"/>
      <text x="${avgPreviewX.toFixed(2)}" y="${(badgeY + 0.38 * invScale).toFixed(
    2
  )}" font-size="${(1.06 * invScale).toFixed(
    2
  )}" fill="#FFFFFF" font-weight="800" text-anchor="middle">新组 ${escapeHtml(
    nextGroupLbl
  )} · ${items.length} 辆</text>
    </g>
  `;

  if (mainG) mainG.innerHTML = markup;
  if (refG) refG.innerHTML = markup;
}

function updateSyncCrosshairDom(worldPt) {
  const refG = document.getElementById("refSyncCrosshair");
  const mainG = document.getElementById("mainSyncCrosshair");
  if (!state.siteMap.compareSplit || !worldPt || !state.overview) {
    if (refG) refG.innerHTML = "";
    if (mainG) mainG.innerHTML = "";
    return;
  }
  const { worldW, worldH } = getSiteMapMetrics(state.overview.site_map);
  const [wx, wy] = worldPt;
  if (wx < 0 || wy < 0 || wx > worldW || wy > worldH) {
    if (refG) refG.innerHTML = "";
    if (mainG) mainG.innerHTML = "";
    return;
  }
  const invScale = Math.pow(state.siteMap.zoom || 1.0, -0.52);
  const sw = (0.22 * invScale).toFixed(2);
  const dash = `${(0.85 * invScale).toFixed(2)},${(0.65 * invScale).toFixed(2)}`;
  const rOuter = (1.15 * invScale).toFixed(2);
  const rDot = (0.28 * invScale).toFixed(2);
  const crossMarkup = `
    <line x1="0" y1="${wy.toFixed(2)}" x2="${worldW.toFixed(2)}" y2="${wy.toFixed(
    2
  )}" stroke="rgba(0, 113, 227, 0.42)" stroke-width="${sw}" stroke-dasharray="${dash}"/>
    <line x1="${wx.toFixed(2)}" y1="0" x2="${wx.toFixed(2)}" y2="${worldH.toFixed(
    2
  )}" stroke="rgba(0, 113, 227, 0.42)" stroke-width="${sw}" stroke-dasharray="${dash}"/>
    <circle cx="${wx.toFixed(2)}" cy="${wy.toFixed(
    2
  )}" r="${rOuter}" fill="rgba(0, 113, 227, 0.08)" stroke="#0071E3" stroke-width="${(
    0.28 * invScale
  ).toFixed(2)}"/>
    <circle cx="${wx.toFixed(2)}" cy="${wy.toFixed(2)}" r="${rDot}" fill="#FF9500"/>
  `;
  if (refG) refG.innerHTML = crossMarkup;
  if (mainG) mainG.innerHTML = crossMarkup;
}

function renderZoneCards(zones) {
  const wrap = document.getElementById("zoneCardsGrid");
  if (!wrap) return;
  const allZones = zones || [];
  const cameras = state.overview?.cameras || [];
  const gates = state.overview?.site_map?.topology?.gates || [];
  const gateNameById = Object.fromEntries(gates.map((g) => [g.gate_id, g.name]));

  const listToRender = allZones.filter((z) =>
    matchesGovSearch(
      z.zone_id,
      z.name,
      z.description,
      formatZoneGateLabelClient(z.gate_access || z.gate, gates),
      ...(z.spots || []).map((s) => s.label)
    )
  );

  if (!listToRender.length) {
    wrap.innerHTML = `<div class="bldg-empty-note" style="grid-column:1/-1;">未找到匹配的停车片区，可点击右上角「＋ 新增片区」手动补充。</div>`;
    return;
  }

  wrap.innerHTML = listToRender
    .map((z) => {
      const isRec = z.zone_id === state.lastRecommendedZoneId;
      const camCnt =
        z.camera_count ?? cameras.filter((c) => c.zone_id === z.zone_id).length;
      const gateLabel = formatZoneGateLabelClient(z.gate_access || z.gate, gates);
      const isDeadEnd = Boolean(z.is_dead_end || Number(z.dead_end_penalty || 0) >= 15);
      const pills = (z.spots || []).length
        ? (z.spots || [])
            .map((s) => {
              const effSt = s.effective_status || s.status;
              const obsType = s.obstacle_type || "none";
              let chipCls = `chip-${effSt}`;
              if (effSt === "FREE" && obsType === "movable") chipCls = "chip-MOVABLE";
              else if (effSt === "FREE" && obsType === "pedestrian") chipCls = "chip-PEDESTRIAN";

              const pCls = s.pending_status ? "chip-pending" : "";
              const extra =
                s.spot_type === "elastic_strip"
                  ? ` · 可停${s.free_units}/${s.capacity}辆`
                  : s.compact_only
                  ? " · 小车"
                  : effSt === "FREE" && obsType === "movable"
                  ? " · 移物可停"
                  : effSt === "FREE" && obsType === "pedestrian"
                  ? " · 有人让行"
                  : "";

              let pendTxt = "";
              if (s.pending_status) {
                if (
                  s.status === "FREE" &&
                  (s.pending_status === "OCCUPIED" || s.pending_status === "BLOCKED")
                ) {
                  pendTxt = " · 预扣复核中";
                } else if (s.pending_status === "FREE") {
                  pendTxt = " · 待复核释放";
                } else {
                  pendTxt = ` → 待确认(${s.pending_status})`;
                }
              }
              const tip = s.action_hint
                ? `${s.last_reason} | 建议：${s.action_hint}`
                : s.last_reason;
              return `<span class="spot-chip ${chipCls} ${pCls}" title="${escapeHtml(
                tip
              )}">${escapeHtml(s.label)}${extra}${pendTxt}</span>`;
            })
            .join("")
        : `<span class="subtle-text" style="font-size:12px;">暂未绑定车位（可将已接入机位切换至此片区，或在此新增机位）</span>`;

      const hintsHtml =
        (z.actionable_hints || []).length > 0
          ? `<div class="zone-action-hints">${z.actionable_hints
              .map(
                (h) =>
                  `<div class="hint-${escapeHtml(h.obstacle_type)}">💡 <strong>${escapeHtml(
                    h.label
                  )}</strong>：${escapeHtml(h.hint)}</div>`
              )
              .join("")}</div>`
          : "";

      const pendSummary =
        z.pending_confirming > 0 ? ` · 防抖复核${z.pending_confirming}位` : "";

      return `
        <div class="zone-tile ${isRec ? "recommended" : ""}">
          <div class="zone-tile-head">
            <span>${isRec ? "首选 · " : ""}${escapeHtml(z.name)} <code style="font-size:11px;font-weight:500;color:#6E6E73;">${escapeHtml(
        z.zone_id
      )}</code></span>
            <span style="color:${
              z.effective_free > 0
                ? "#1D9E31"
                : Number(z.total_capacity || 0) === 0
                ? "#6E6E73"
                : "#D70015"
            }">
              共 ${z.total_capacity} 位 · 净余 ${z.effective_free} 位${
        z.soft_locked > 0 ? ` (在途-${z.soft_locked})` : ""
      }
            </span>
          </div>
          <div class="zone-tile-meta-pills">
            <span class="badge-pill">🚗 通行大门：${escapeHtml(gateLabel)}</span>
            <span class="badge-pill ${camCnt > 0 ? "is-good" : ""}">📹 已接入 ${camCnt} 路机位</span>
            <span class="badge-pill">${isDeadEnd ? "⚠️ 尽头折返支路" : "✓ 顺畅贯通道"}</span>
          </div>
          <div class="zone-tile-sub">${escapeHtml(
            z.description || "人工补充/校准停车片区"
          )}${pendSummary}</div>
          <div class="spot-badges">${pills}</div>
          ${hintsHtml}
          <div class="zone-tile-footer">
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <button type="button" class="apple-mini-pill-btn is-accent js-zone-card-edit"
                      data-zone-id="${escapeHtml(z.zone_id)}">✎ 编辑片区</button>
              <button type="button" class="apple-mini-pill-btn js-zone-card-add-cam"
                      data-zone-id="${escapeHtml(z.zone_id)}">＋ 在此加机位</button>
            </div>
            <button type="button" class="apple-mini-pill-btn is-danger js-zone-card-delete"
                    data-zone-id="${escapeHtml(z.zone_id)}"
                    ${allZones.length <= 1 ? "disabled" : ""}>🗑 删除/归并</button>
          </div>
        </div>
      `;
    })
    .join("");
}

/* ==================== TAB 2: CALIBRATION CONSOLE (DIRECT MANIPULATION STUDIO) ==================== */
function cloneSpotsSnapshot(spots) {
  return JSON.parse(JSON.stringify(spots || []));
}

function pushCalibUndoSnapshot() {
  state.calib.undoStack.push(cloneSpotsSnapshot(state.calib.spots));
  if (state.calib.undoStack.length > 40) state.calib.undoStack.shift();
  state.calib.redoStack = [];
  markCalibDirty(true);
}

function markCalibDirty(isDirty) {
  state.calib.dirty = Boolean(isDirty);
  const badge = document.getElementById("calibDirtyBadge");
  if (badge) badge.classList.toggle("hidden", !state.calib.dirty);
  const undoBtn = document.getElementById("btnCalibUndo");
  const redoBtn = document.getElementById("btnCalibRedo");
  if (undoBtn) undoBtn.disabled = state.calib.undoStack.length === 0;
  if (redoBtn) redoBtn.disabled = state.calib.redoStack.length === 0;
}

function performCalibUndo() {
  if (!state.calib.undoStack.length) return;
  state.calib.redoStack.push(cloneSpotsSnapshot(state.calib.spots));
  state.calib.spots = state.calib.undoStack.pop();
  if (state.calib.selectedSpotIdx >= state.calib.spots.length) {
    state.calib.selectedSpotIdx = state.calib.spots.length - 1;
  }
  markCalibDirty(true);
  syncSelectedSpotInspector();
  renderSpotListSidebar();
  renderCalibCanvas();
}

function performCalibRedo() {
  if (!state.calib.redoStack.length) return;
  state.calib.undoStack.push(cloneSpotsSnapshot(state.calib.spots));
  state.calib.spots = state.calib.redoStack.pop();
  if (state.calib.selectedSpotIdx >= state.calib.spots.length) {
    state.calib.selectedSpotIdx = state.calib.spots.length - 1;
  }
  markCalibDirty(true);
  syncSelectedSpotInspector();
  renderSpotListSidebar();
  renderCalibCanvas();
}

function setCalibMode(modeName) {
  const prevMode = state.calib.mode;
  state.calib.mode = modeName;
  state.calib.draftPoints = [];
  state.calib.curveFrontPts = [];
  state.calib.curveBackPts = [];
  state.calib.curvePhase = "front";
  state.calib.previewTrapezoids = [];
  state.calib.hoveredTarget = null;

  document.querySelectorAll(".mode-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === modeName);
  });
  const drawMenu = document.getElementById("calibDrawMenu");
  const auxLbl = document.getElementById("calibAuxMenuLabel");
  const auxModeNames = {
    curved_row: "〰️ 弧形排布",
    perspective_row: "📐 直线均分",
    irregular_poly: "⬠ 单格4点",
    elastic_strip: "🚐 顺停带",
  };
  if (drawMenu) {
    drawMenu.open = false;
    const isAux = Boolean(auxModeNames[modeName]);
    drawMenu.classList.toggle("has-aux-mode", isAux);
    if (auxLbl) {
      auxLbl.textContent = isAux ? `${auxModeNames[modeName]} ▾` : "＋ 手绘建框";
    }
  }
  document.getElementById("btnToggleCropRoiMode")?.classList.toggle("active", modeName === "crop_roi");
  document.getElementById("btnToggleAnchorMode")?.classList.toggle("active", modeName === "anchor_points");
  document.getElementById("ctrlSmartEdit")?.classList.toggle("hidden", modeName !== "smart_edit");
  document.getElementById("ctrlCurvedRow")?.classList.toggle("hidden", modeName !== "curved_row");
  document.getElementById("ctrlPerspective")?.classList.toggle("hidden", modeName !== "perspective_row");
  document.getElementById("ctrlIrregular")?.classList.toggle("hidden", modeName !== "irregular_poly");
  document.getElementById("ctrlElastic")?.classList.toggle("hidden", modeName !== "elastic_strip");
  document.getElementById("ctrlCropRoi")?.classList.toggle("hidden", modeName !== "crop_roi");
  document.getElementById("ctrlAnchorPoints")?.classList.toggle("hidden", modeName !== "anchor_points");

  if (modeName === "crop_roi" || prevMode === "crop_roi") {
    syncCalibActiveBgImage();
  }

  updateCurvedHintUI();
  syncSelectedSpotInspector();
  updateCalibButtons();
  renderCalibCanvas();
}

function setCalibZoom(newZoom) {
  const z = Math.min(3.2, Math.max(1.0, Number(newZoom) || 1.0));
  state.calib.zoom = +z.toFixed(2);
  const inner = document.getElementById("calibZoomInner");
  if (inner) inner.style.width = `${Math.round(state.calib.zoom * 100)}%`;
  const lbl = document.getElementById("calibZoomLabel");
  if (lbl) lbl.textContent = `${Math.round(state.calib.zoom * 100)}%`;
}

function evalCatmullRomChainJS(ctrlPts, samplesPerSeg = 32) {
  const n = ctrlPts.length;
  if (n <= 1) return ctrlPts.map((p) => [...p]);
  if (n === 2) {
    const out = [];
    for (let s = 0; s <= samplesPerSeg; s++) {
      const t = s / samplesPerSeg;
      out.push([
        ctrlPts[0][0] * (1 - t) + ctrlPts[1][0] * t,
        ctrlPts[0][1] * (1 - t) + ctrlPts[1][1] * t,
      ]);
    }
    return out;
  }
  const dense = [];
  for (let i = 0; i < n - 1; i++) {
    const p0 =
      i === 0
        ? [2 * ctrlPts[0][0] - ctrlPts[1][0], 2 * ctrlPts[0][1] - ctrlPts[1][1]]
        : ctrlPts[i - 1];
    const p1 = ctrlPts[i];
    const p2 = ctrlPts[i + 1];
    const p3 =
      i + 2 >= n
        ? [2 * ctrlPts[n - 1][0] - ctrlPts[n - 2][0], 2 * ctrlPts[n - 1][1] - ctrlPts[n - 2][1]]
        : ctrlPts[i + 2];
    const maxS = i === n - 2 ? samplesPerSeg : samplesPerSeg - 1;
    for (let s = 0; s <= maxS; s++) {
      const t = s / samplesPerSeg;
      const t2 = t * t;
      const t3 = t2 * t;
      const x =
        0.5 *
        (2 * p1[0] +
          (-p0[0] + p2[0]) * t +
          (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
          (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3);
      const y =
        0.5 *
        (2 * p1[1] +
          (-p0[1] + p2[1]) * t +
          (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
          (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3);
      dense.push([x, y]);
    }
  }
  return dense;
}

function sampleCurveArcLengthJS(ctrlPts, uVals) {
  const dense = evalCatmullRomChainJS(ctrlPts, 36);
  const cum = [0];
  for (let i = 1; i < dense.length; i++) {
    cum.push(
      cum[i - 1] + Math.hypot(dense[i][0] - dense[i - 1][0], dense[i][1] - dense[i - 1][1])
    );
  }
  const total = cum[cum.length - 1] || 1;
  return uVals.map((u) => {
    const target = Math.max(0, Math.min(1, u)) * total;
    let lo = 0;
    let hi = cum.length - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] <= target) lo = mid;
      else hi = mid;
    }
    const segLen = cum[hi] - cum[lo];
    const ratio = segLen > 1e-6 ? (target - cum[lo]) / segLen : 0;
    return [
      +(dense[lo][0] + (dense[hi][0] - dense[lo][0]) * ratio).toFixed(1),
      +(dense[lo][1] + (dense[hi][1] - dense[lo][1]) * ratio).toFixed(1),
    ];
  });
}

function computeCurvedRowPreviewJS() {
  const fPts = state.calib.curveFrontPts;
  let bPts = state.calib.curveBackPts;
  if (fPts.length < 2 || bPts.length < 2) {
    state.calib.previewTrapezoids = [];
    return;
  }
  const dDirect =
    Math.hypot(fPts[0][0] - bPts[0][0], fPts[0][1] - bPts[0][1]) +
    Math.hypot(
      fPts[fPts.length - 1][0] - bPts[bPts.length - 1][0],
      fPts[fPts.length - 1][1] - bPts[bPts.length - 1][1]
    );
  const dFlip =
    Math.hypot(fPts[0][0] - bPts[bPts.length - 1][0], fPts[0][1] - bPts[bPts.length - 1][1]) +
    Math.hypot(fPts[fPts.length - 1][0] - bPts[0][0], fPts[fPts.length - 1][1] - bPts[0][1]);
  if (dFlip < dDirect) {
    bPts = [...bPts].reverse();
  }
  const count = Math.max(1, Math.min(35, Number(document.getElementById("curveSpotCount")?.value || 7)));
  const uList = [];
  for (let i = 0; i <= count; i++) uList.push(i / count);
  const fSampled = sampleCurveArcLengthJS(fPts, uList);
  const bSampled = sampleCurveArcLengthJS(bPts, uList);
  const quads = [];
  for (let i = 0; i < count; i++) {
    quads.push([bSampled[i], bSampled[i + 1], fSampled[i + 1], fSampled[i]]);
  }
  state.calib.previewTrapezoids = quads;
}

function updateCurvedHintUI() {
  const hint = document.getElementById("curvedStepHint");
  const nextBtn = document.getElementById("btnCurveNextPhase");
  const commitBtn = document.getElementById("btnCommitCurveRow");
  if (!hint || !nextBtn || !commitBtn) return;

  if (state.calib.curvePhase === "front") {
    hint.innerHTML = `<strong>第 1 步（描前路牙）：</strong>沿弯道<strong>前侧路牙弧线</strong>从左到右点击 <strong>2～8 个点</strong>（已点 ${state.calib.curveFrontPts.length} 点），再点「下一步：描后沿弧线」：`;
    nextBtn.disabled = state.calib.curveFrontPts.length < 2;
    nextBtn.textContent = "下一步：描后沿弧线";
  } else {
    hint.innerHTML = `<strong>第 2 步（描后沿灌木线）：</strong>沿车位<strong>后侧灌木/路缘弧线</strong>点击 <strong>2～8 个点</strong>（已点 ${state.calib.curveBackPts.length} 点，可拖拽圆点实时调弧度）：`;
    nextBtn.disabled = false;
    nextBtn.textContent = "重描前路牙";
  }
  commitBtn.disabled = state.calib.previewTrapezoids.length === 0;
}

function estimateSpotVehicleHeightPct(sp) {
  if (!sp || !Array.isArray(sp.polygon) || sp.polygon.length < 3) {
    return Math.round((state.calib.vehHeightScale || 0.32) * 100);
  }
  const gPts = sp.polygon.length === 4 ? orderQuadTlTrBrBlJS(sp.polygon) : sp.polygon;
  const vPoly = ensureSpotVehiclePolygon(sp);
  const vPts = vPoly.length === 4 ? orderQuadTlTrBrBlJS(vPoly) : vPoly;
  if (gPts.length >= 4 && vPts.length >= 4) {
    const topW = Math.hypot(gPts[1][0] - gPts[0][0], gPts[1][1] - gPts[0][1]);
    const botW = Math.hypot(gPts[2][0] - gPts[3][0], gPts[2][1] - gPts[3][1]);
    const leftH = Math.hypot(gPts[3][0] - gPts[0][0], gPts[3][1] - gPts[0][1]);
    const rightH = Math.hypot(gPts[2][0] - gPts[1][0], gPts[2][1] - gPts[1][1]);
    const charScale = Math.max(18, 0.58 * 0.5 * (leftH + rightH) + 0.42 * 0.5 * (topW + botW));
    const gTopY = 0.5 * (gPts[0][1] + gPts[1][1]);
    const vTopY = 0.5 * (vPts[0][1] + vPts[1][1]);
    const pct = Math.round(((gTopY - vTopY) / charScale) * 100);
    return Math.max(5, Math.min(95, pct));
  }
  return Math.round((state.calib.vehHeightScale || 0.32) * 100);
}

function syncSelectedSpotInspector() {
  const bar = document.getElementById("selectedSpotInspector");
  if (!bar) return;
  const idx = state.calib.selectedSpotIdx;
  const sp = idx >= 0 && idx < state.calib.spots.length ? state.calib.spots[idx] : null;
  if (!sp || state.calib.mode !== "smart_edit") {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  document.getElementById("selSpotLabel").value = sp.label || "";
  document.getElementById("selSpotStatus").value = sp.status || sp.initial_status || "FREE";
  document.getElementById("selSpotCompact").checked = Boolean(sp.compact_only);
  document.getElementById("btnSplitSpot").disabled = sp.polygon.length !== 4;
  document.getElementById("btnCloneSpotNext").disabled = sp.polygon.length !== 4;

  const hGroup = document.getElementById("selSpot3DHeightGroup");
  if (hGroup) {
    const canAdjust3D = state.calib.layerMode === "vehicle" || state.calib.layerMode === "dual";
    hGroup.classList.toggle("hidden", !canAdjust3D);
    if (canAdjust3D) {
      const pct = estimateSpotVehicleHeightPct(sp);
      const hSlider = document.getElementById("selSpotVehHeightSlider");
      const hVal = document.getElementById("selSpotVehHeightVal");
      if (hSlider) hSlider.value = String(pct);
      if (hVal) hVal.textContent = `${pct}%`;
    }
  }
}

function getPolyCentroid(poly) {
  const n = poly.length || 1;
  return [
    poly.reduce((s, p) => s + p[0], 0) / n,
    poly.reduce((s, p) => s + p[1], 0) / n,
  ];
}

function orderQuadTlTrBrBlJS(pts) {
  const sortedByY = [...pts].sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const top2 = sortedByY.slice(0, 2).sort((a, b) => a[0] - b[0]);
  const bot2 = sortedByY.slice(2, 4).sort((a, b) => a[0] - b[0]);
  return [top2[0], top2[1], bot2[1], bot2[0]];
}

function deriveVehicleEnvelopeJS(
  groundPoly,
  imgW = state.calib.imgW || 1280,
  imgH = state.calib.imgH || 720,
  heightScale = state.calib.vehHeightScale ?? 0.32,
  slantBias = state.calib.vehSlantBias ?? 0.0
) {
  if (!groundPoly || groundPoly.length < 3) return [];
  const W = Math.max(64, Number(imgW) || 1280);
  const H = Math.max(64, Number(imgH) || 720);
  const clampPt = (x, y) => [
    Math.max(0, Math.min(W - 1, Math.round(x))),
    Math.max(0, Math.min(H - 1, Math.round(y))),
  ];

  if (groundPoly.length !== 4) {
    const xs = groundPoly.map((p) => p[0]);
    const ys = groundPoly.map((p) => p[1]);
    const spanH = Math.max(18, Math.max(...ys) - Math.min(...ys));
    const spanW = Math.max(18, Math.max(...xs) - Math.min(...xs));
    const lift = spanH * heightScale * 0.85;
    const cy = ys.reduce((a, b) => a + b, 0) / ys.length;
    return groundPoly.map(([x, y]) => {
      const topWeight = y <= cy ? 1.0 : 0.35;
      return clampPt(x + spanW * slantBias * topWeight, y - lift * topWeight);
    });
  }

  const [tl, tr, br, bl] = orderQuadTlTrBrBlJS(groundPoly);
  const topW = Math.hypot(tr[0] - tl[0], tr[1] - tl[1]);
  const botW = Math.hypot(br[0] - bl[0], br[1] - bl[1]);
  const leftH = Math.hypot(bl[0] - tl[0], bl[1] - tl[1]);
  const rightH = Math.hypot(br[0] - tr[0], br[1] - tr[1]);
  const avgW = Math.max(16, 0.5 * (topW + botW));
  const avgH = Math.max(16, 0.5 * (leftH + rightH));

  const cyNorm = Math.max(0, Math.min(1, (tl[1] + tr[1] + br[1] + bl[1]) / (4 * H)));
  const perspGain = 0.78 + 0.42 * (1.0 - 0.55 * cyNorm);
  const charScale = 0.58 * avgH + 0.42 * avgW;
  const aspectHw = avgH / avgW;

  let backLift = Math.max(10, Math.min(H * 0.24, charScale * heightScale * perspGain));
  if (aspectHw > 1.2) {
    const sinTheta = Math.min(0.9, aspectHw / 2.15);
    const cosTheta = Math.sqrt(Math.max(0.16, 1 - sinTheta * sinTheta));
    const steepCap = Math.max(avgW * 0.22, avgW * (heightScale * 2.1) * cosTheta);
    backLift = Math.min(backLift, steepCap);
  }
  const frontLift = Math.max(4, backLift * 0.36);

  const cxNorm = (tl[0] + tr[0] + br[0] + bl[0]) / (4 * W) - 0.5;
  const autoSlant = cxNorm * 0.10 * avgW + slantBias * avgW;
  const sidePad = Math.max(3, avgW * 0.055);

  let topDx = tr[0] - tl[0];
  let topDy = tr[1] - tl[1];
  const topLen = Math.max(1, Math.hypot(topDx, topDy));
  topDx /= topLen;
  topDy /= topLen;

  let botDx = br[0] - bl[0];
  let botDy = br[1] - bl[1];
  const botLen = Math.max(1, Math.hypot(botDx, botDy));
  botDx /= botLen;
  botDy /= botLen;

  return [
    clampPt(tl[0] - topDx * sidePad + autoSlant, tl[1] - topDy * sidePad - backLift),
    clampPt(tr[0] + topDx * sidePad + autoSlant, tr[1] + topDy * sidePad - backLift),
    clampPt(br[0] + botDx * (sidePad * 0.65) + autoSlant * 0.35, br[1] + botDy * (sidePad * 0.65) - frontLift),
    clampPt(bl[0] - botDx * (sidePad * 0.65) + autoSlant * 0.35, bl[1] - botDy * (sidePad * 0.65) - frontLift),
  ];
}

function ensureSpotVehiclePolygon(sp) {
  if (!sp || !Array.isArray(sp.polygon) || sp.polygon.length < 3) return [];
  if (!Array.isArray(sp.vehicle_polygon) || sp.vehicle_polygon.length < 3) {
    sp.vehicle_polygon = deriveVehicleEnvelopeJS(sp.polygon);
    sp.vehicle_poly_custom = 0;
  }
  return sp.vehicle_polygon;
}

function syncLinkedVehiclePolyIfAuto(sp) {
  if (!sp || !Array.isArray(sp.polygon) || sp.polygon.length < 3) return;
  if (!sp.vehicle_poly_custom) {
    sp.vehicle_polygon = deriveVehicleEnvelopeJS(sp.polygon);
  }
}

function getEditableSpotPoly(sp) {
  if (!sp) return [];
  if (state.calib.layerMode === "vehicle") {
    return ensureSpotVehiclePolygon(sp);
  }
  return sp.polygon || [];
}

function setEditableSpotPoly(sp, newPoly) {
  if (!sp) return;
  if (state.calib.layerMode === "vehicle") {
    sp.vehicle_polygon = newPoly;
    sp.vehicle_poly_custom = 1;
  } else {
    // Strictly isolated: editing empty-ground polygon NEVER mutates existing vehicle_polygon
    sp.polygon = newPoly;
  }
}

function cropImageSourceIfNeeded(img, cropRoi, calTargetW = 0, calTargetH = 0) {
  if (!img || !cropRoi || !cropRoi.enabled || cropRoi.already_cropped) {
    return { img, width: img?.naturalWidth || 0, height: img?.naturalHeight || 0, dataUrl: null, wasCropped: false };
  }
  const rx = Math.max(0, Math.min(0.95, Number(cropRoi.x) || 0));
  const ry = Math.max(0, Math.min(0.95, Number(cropRoi.y) || 0));
  const rw = Math.max(0.05, Math.min(1 - rx, Number(cropRoi.w) || 1));
  const rh = Math.max(0.05, Math.min(1 - ry, Number(cropRoi.h) || 1));
  if (rw >= 0.998 && rh >= 0.998) {
    return { img, width: img.naturalWidth, height: img.naturalHeight, dataUrl: null, wasCropped: false };
  }
  const W = img.naturalWidth || img.width || 0;
  const H = img.naturalHeight || img.height || 0;
  if (W < 32 || H < 32) {
    return { img, width: W, height: H, dataUrl: null, wasCropped: false };
  }
  if (calTargetW > 0 && calTargetH > 0 && Math.abs(W - calTargetW) <= 4 && Math.abs(H - calTargetH) <= 4) {
    return { img, width: W, height: H, dataUrl: null, wasCropped: false };
  }
  const curAspect = W / H;
  const rawW = Number(cropRoi.raw_width || cropRoi.raw_size?.[0]) || 0;
  const rawH = Number(cropRoi.raw_height || cropRoi.raw_size?.[1]) || 0;
  if (calTargetW > 0 && calTargetH > 0 && rawW > 0 && rawH > 0) {
    const calAspect = calTargetW / calTargetH;
    const origAspect = rawW / rawH;
    if (
      Math.abs(curAspect - calAspect) / Math.max(0.1, calAspect) < 0.06 &&
      Math.abs(curAspect - origAspect) / Math.max(0.1, origAspect) > 0.10
    ) {
      return { img, width: W, height: H, dataUrl: null, wasCropped: false };
    }
  }
  if (rawW > 0 && rawH > 0) {
    const origAspect = rawW / rawH;
    const croppedAspect = origAspect * (rw / rh);
    // Already cropped to ROI aspect
    if (
      Math.abs(curAspect - croppedAspect) / Math.max(0.1, croppedAspect) < 0.08 &&
      Math.abs(curAspect - origAspect) / Math.max(0.1, origAspect) > 0.12
    ) {
      return { img, width: W, height: H, dataUrl: null, wasCropped: false };
    }
    // Aspect-mismatch guard: never apply a static crop_roi calibrated on a different
    // orientation/focal-frame (e.g. tall 3:4 vs wide 16:9), preventing stall clipping!
    if (Math.abs(curAspect - origAspect) / Math.max(0.1, origAspect) > 0.18) {
      return { img, width: W, height: H, dataUrl: null, wasCropped: false };
    }
  }
  const x0 = Math.max(0, Math.min(W - 16, Math.round(rx * W)));
  const y0 = Math.max(0, Math.min(H - 16, Math.round(ry * H)));
  const cw = Math.max(16, Math.min(W - x0, Math.round(rw * W)));
  const ch = Math.max(16, Math.min(H - y0, Math.round(rh * H)));
  const off = document.createElement("canvas");
  off.width = cw;
  off.height = ch;
  const octx = off.getContext("2d");
  octx.drawImage(img, x0, y0, cw, ch, 0, 0, cw, ch);
  return {
    img: off,
    width: cw,
    height: ch,
    dataUrl: off.toDataURL("image/jpeg", 0.9),
    wasCropped: true,
  };
}

function rescaleCalibSpotsToSize(newW, newH) {
  const oldW = Number(state.calib.imgW) || 0;
  const oldH = Number(state.calib.imgH) || 0;
  if (!newW || !newH || !oldW || !oldH) return;
  if (Math.abs(newW - oldW) <= 1 && Math.abs(newH - oldH) <= 1) return;
  const sx = newW / oldW;
  const sy = newH / oldH;
  const scalePoly = (poly) =>
    (poly || []).map(([x, y]) => [
      Math.max(0, Math.min(newW - 1, Math.round(x * sx))),
      Math.max(0, Math.min(newH - 1, Math.round(y * sy))),
    ]);
  state.calib.spots.forEach((sp) => {
    if (Array.isArray(sp.polygon) && sp.polygon.length >= 3) {
      sp.polygon = scalePoly(sp.polygon);
    }
    if (Array.isArray(sp.vehicle_polygon) && sp.vehicle_polygon.length >= 3) {
      sp.vehicle_polygon = scalePoly(sp.vehicle_polygon);
    }
  });
  state.calib.imgW = newW;
  state.calib.imgH = newH;
  if (state.calib.cameraMeta) {
    state.calib.cameraMeta.image_width = newW;
    state.calib.cameraMeta.image_height = newH;
  }
}

function syncCalibActiveBgImage() {
  const canvas = document.getElementById("calibCanvas");
  const mode = state.calib.layerMode || "dual";

  if (state.calib.mode === "crop_roi" && state.calib.fullFrameImg && canvas) {
    state.calib.bgImage = state.calib.fullFrameImg;
    canvas.width = state.calib.fullFrameW || state.calib.fullFrameImg.naturalWidth || 1280;
    canvas.height = state.calib.fullFrameH || state.calib.fullFrameImg.naturalHeight || 720;
    return;
  }

  let isServerPreview = false;
  if (mode === "vehicle") {
    if (state.calib.vehicleBgImage) {
      state.calib.bgImage = state.calib.vehicleBgImage;
      state.calib.bgDataUrl = state.calib.vehicleBgDataUrl;
      isServerPreview = Boolean(state.calib.vehicleBgIsServerPreview);
    } else {
      state.calib.bgImage = state.calib.groundBgImage || null;
      state.calib.bgDataUrl = state.calib.groundBgDataUrl || null;
      isServerPreview = Boolean(state.calib.groundBgIsServerPreview);
    }
  } else if (mode === "dual" && !state.calib.groundBgImage && state.calib.vehicleBgImage) {
    state.calib.bgImage = state.calib.vehicleBgImage;
    state.calib.bgDataUrl = state.calib.vehicleBgDataUrl;
    isServerPreview = Boolean(state.calib.vehicleBgIsServerPreview);
  } else {
    state.calib.bgImage = state.calib.groundBgImage || null;
    state.calib.bgDataUrl = state.calib.groundBgDataUrl || null;
    isServerPreview = Boolean(state.calib.groundBgIsServerPreview);
  }

  if (canvas) {
    const camW = Number(state.calib.cameraMeta?.image_width) || 0;
    const camH = Number(state.calib.cameraMeta?.image_height) || 0;
    if (isServerPreview && camW > 0 && camH > 0) {
      // Server templates are max_edge=960 previews (e.g. 720x960 for a 1280x1707 tall photo).
      // Keep canvas & spot coordinate space anchored to authoritative camera dimensions!
      state.calib.imgW = camW;
      state.calib.imgH = camH;
    } else if (state.calib.bgImage) {
      const natW = state.calib.bgImage.width || state.calib.bgImage.naturalWidth || state.calib.imgW || 1280;
      const natH = state.calib.bgImage.height || state.calib.bgImage.naturalHeight || state.calib.imgH || 720;
      state.calib.imgW = natW;
      state.calib.imgH = natH;
    }
    canvas.width = state.calib.imgW;
    canvas.height = state.calib.imgH;
  }

  const vehBadge = document.getElementById("vehTplStatusBadge");
  if (vehBadge) {
    if (state.calib.vehicleBgDataUrl) {
      vehBadge.className = "tpl-pill tpl-pill-ready";
      vehBadge.textContent = "已设独立有车实拍图";
    } else if (state.calib.groundBgDataUrl) {
      vehBadge.className = "tpl-pill tpl-pill-idle";
      vehBadge.textContent = "未单独上传(暂借地面底图)";
    } else {
      vehBadge.className = "tpl-pill tpl-pill-idle";
      vehBadge.textContent = "未设有车参考图";
    }
  }

  document
    .getElementById("vehicleLayerControls")
    ?.classList.toggle("hidden", mode !== "vehicle");
  document
    .getElementById("groundLayerQuickBadge")
    ?.classList.toggle("hidden", mode !== "ground");

  const saveBtn = document.getElementById("btnSaveCalibration");
  if (saveBtn) {
    saveBtn.textContent =
      mode === "ground"
        ? "💾 保存无车地面标定（不影响车辆层）"
        : mode === "vehicle"
        ? "💾 保存车辆停入标定（不影响地面层）"
        : "💾 保存双层完整标定配置";
  }
}

function setCalibLayerMode(layerMode) {
  state.calib.layerMode = layerMode || "dual";
  state.calib.hoveredTarget = null;
  document.querySelectorAll(".layer-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.layer === state.calib.layerMode);
  });

  // Sync per-layer magnetic weld: ground defaults ON (shared white paint lines),
  // 3D vehicle layer defaults OFF so all 4 edges, corners, and roof height adjust freely!
  const isVeh = state.calib.layerMode === "vehicle";
  state.calib.magneticWeld = isVeh
    ? Boolean(state.calib.vehicleMagneticWeld)
    : Boolean(state.calib.groundMagneticWeld);
  const magChk = document.getElementById("chkMagneticWeld");
  if (magChk) magChk.checked = state.calib.magneticWeld;
  const magLbl = document.getElementById("lblMagneticWeld");
  if (magLbl) {
    magLbl.textContent = isVeh ? "联动邻车边(默认关·四边自由)" : "磁吸联动相邻边";
  }

  syncCalibActiveBgImage();
  const hint = document.getElementById("dualLayerHint");
  if (hint) {
    if (state.calib.layerMode === "vehicle") {
      hint.innerHTML =
        "🚗 <strong>工作区 ②：车辆停入 3D 标定（四边与车高完全独立自由调整）</strong> · 默认已关闭邻框连带，可直接单拖<strong>任意一条边、四个角点或顶部橙色 ↕车高 手柄</strong>贴合不同车型的高度与透视轮廓，绝不改动地面框。";
    } else if (state.calib.layerMode === "ground") {
      hint.innerHTML =
        "🟢 <strong>工作区 ①：无车地面标定（独立设定，绝不干扰车辆框）</strong> · 已临时隐藏 3D 车辆轮廓，支持 AI 局部放大精扫与「🎯 地面特征精准贴合（白线/路牙/砖缝）」。";
    } else {
      hint.innerHTML =
        "🔬 <strong>工作区 ③：双层空间标定（默认）</strong> · 同屏展示智能裁剪后的<strong>无车地面基准框</strong>与<strong>3D 车辆空间包络棱柱</strong>，可直接拖拽角点/边线或橙色 <strong>↕车高</strong> 手柄调节 3D 高度，也可一键实测双层算力。";
    }
  }
  syncSelectedSpotInspector();
  updateCalibButtons();
  renderCalibCanvas();
}

function renderCameraMetaSummaryCard() {
  const card = document.getElementById("camMetaSummaryCard");
  if (!card) return;
  const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
  const cams = state.overview?.cameras || [];
  const cam = cams.find((c) => c.camera_id === camId);
  if (!cam) {
    card.innerHTML = `<div class="muted-hint">暂无选定机位，请点击上方「＋ 新增」创建摄像头机位。</div>`;
    return;
  }
  const parsed = cam.group_code
    ? parseCameraGroupCodesClient(cam.group_code, cam.mount_address || "")
    : null;
  const groupChips =
    parsed && parsed.groups.length
      ? parsed.groups
          .map(
            (g) =>
              `<span class="cam-code-chip" title="${escapeHtml(g.description_cn)}">${escapeHtml(
                g.code
              )} · ${escapeHtml(g.description_cn)}</span>`
          )
          .join(" ")
      : `<span class="cam-code-chip">${escapeHtml(cam.camera_id)}</span>`;
  const addrStr = cam.mount_address
    ? `📍 机位拍摄地址：<strong>${escapeHtml(cam.mount_address)}</strong>`
    : `📍 机位拍摄地址：<span style="color:var(--text-secondary)">未填写（点右上「🔌 接入配置」或「✎ 编辑」可补充）</span>`;
  const spotCnt = cam.spot_count ?? state.calib.spots.length ?? 0;
  const ingestCfg = cam.ingest_config || {};
  const ingestLine = ingestCfg.vendor_label
    ? `<div style="margin-top:4px;font-size:11px;color:#0369A1;">🔌 接入协议：<strong>${escapeHtml(
        ingestCfg.vendor_label
      )}</strong> · <code>${escapeHtml(
        ingestCfg.protocol_label || ingestCfg.protocol || "FTP推送"
      )}</code>${
        ingestCfg.host ? ` · <code>${escapeHtml(ingestCfg.host)}</code>` : ""
      }</div>`
    : "";

  card.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
      <strong style="color:#0F172A;font-size:12.5px;">🎥 ${escapeHtml(cam.name)}</strong>
      <span style="font-size:11px;color:var(--text-secondary);">ID: <code>${escapeHtml(
        cam.camera_id
      )}</code> · 已标 ${spotCnt} 位</span>
    </div>
    <div style="margin-top:5px;display:flex;gap:5px;flex-wrap:wrap;">${groupChips}</div>
    ${ingestLine}
    <div style="margin-top:5px;font-size:11.5px;color:#334155;">${addrStr}</div>
  `;
}

function openCameraCrudForm(mode = "create") {
  const panel = document.getElementById("camCrudFormPanel");
  if (!panel) return;
  state.calib.camFormMode = mode;
  const titleEl = document.getElementById("camCrudFormTitle");
  const grpInput = document.getElementById("camFormGroupCode");
  const addrInput = document.getElementById("camFormMountAddr");
  const nameInput = document.getElementById("camFormName");
  const idInput = document.getElementById("camFormId");

  if (mode === "edit") {
    const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
    const cam = (state.overview?.cameras || []).find((c) => c.camera_id === camId);
    if (!cam) return alert("当前没有可编辑的机位");
    state.calib.editingOriginalCamId = cam.camera_id;
    titleEl.textContent = `编辑摄像头机位（${cam.camera_id}）`;
    grpInput.value = cam.group_code || "";
    addrInput.value = cam.mount_address || "";
    nameInput.value = cam.name || "";
    idInput.value = cam.camera_id || "";
    nameInput.dataset.manual = "1";
    idInput.dataset.manual = "1";
    document.getElementById("calibZoneSelect").value = cam.zone_id;
  } else {
    state.calib.editingOriginalCamId = null;
    titleEl.textContent = "新增摄像头机位（编码规则自动解析）";
    grpInput.value = "";
    addrInput.value = "";
    nameInput.value = "";
    idInput.value = "";
    delete nameInput.dataset.manual;
    delete idInput.dataset.manual;
  }
  panel.classList.remove("hidden");
  updateCameraFormLivePreview(mode === "create");
  grpInput.focus();
}

function updateCameraFormLivePreview(autoPopulateFields = false) {
  const grpRaw = document.getElementById("camFormGroupCode")?.value?.trim() || "";
  const addrRaw = document.getElementById("camFormMountAddr")?.value?.trim() || "";
  const previewEl = document.getElementById("camRuleLivePreview");
  const nameInput = document.getElementById("camFormName");
  const idInput = document.getElementById("camFormId");
  if (!previewEl) return;

  if (!grpRaw) {
    previewEl.innerHTML =
      "💡 编码规则：<code>楼号</code> + <code>方位(F前/B后/L左/R右/S侧)</code> + <code>朝向(V竖/H横)</code> + <code>车位数</code>；一机多拍用 <code>+</code> 连接，如 <code>5LV7</code>、<code>10BH2</code>、<code>10BV7+10BH2</code>。";
    return;
  }

  const parsed = parseCameraGroupCodesClient(grpRaw, addrRaw);
  if (!parsed.valid) {
    previewEl.innerHTML = `<span style="color:#DC2626">⚠️ 编码格式待完善：请使用如 <code>5LV7</code>（5号楼左侧竖7位）或 <code>10BV7+10BH2</code>（支持英文 left/right/front/backend/vertical/horizontal）。</span>`;
    return;
  }

  previewEl.innerHTML = `✅ <strong>自动解析（共 ${parsed.total_spots} 个车位）</strong>：${escapeHtml(
    parsed.suggested_name
  )}<br/><span style="color:#475569">标准组码：<code>${escapeHtml(
    parsed.normalized_group_code
  )}</code> · 建议代号：<code>${escapeHtml(parsed.suggested_camera_id)}</code></span>`;

  if (autoPopulateFields || !nameInput.dataset.manual) {
    nameInput.value = parsed.suggested_name;
  }
  if (
    (autoPopulateFields || !idInput.dataset.manual) &&
    state.calib.camFormMode === "create"
  ) {
    idInput.value = parsed.suggested_camera_id;
  }
}

function getRotationHandlePos(poly, canvasW) {
  if (!poly || poly.length < 3) return null;
  const [cx, cy] = getPolyCentroid(poly);
  const rearMx = 0.5 * (poly[0][0] + poly[1][0]);
  const rearMy = 0.5 * (poly[0][1] + poly[1][1]);
  let vx = rearMx - cx;
  let vy = rearMy - cy;
  const len = Math.hypot(vx, vy) || 1;
  vx /= len;
  vy /= len;
  const stem = Math.max(22, canvasW * 0.022);
  return {
    anchorX: rearMx,
    anchorY: rearMy,
    x: rearMx + vx * stem,
    y: rearMy + vy * stem,
    cx,
    cy,
  };
}

function getRoofHeightHandlePos(poly, canvasW) {
  const canAdjust3D = state.calib.layerMode === "vehicle" || state.calib.layerMode === "dual";
  if (!poly || poly.length < 4 || !canAdjust3D) return null;
  let bestEdge = { pA: 0, pB: 1, avgY: Infinity };
  for (let i = 0; i < poly.length; i++) {
    const j = (i + 1) % poly.length;
    const dx = Math.abs(poly[j][0] - poly[i][0]);
    const len = Math.hypot(poly[j][0] - poly[i][0], poly[j][1] - poly[i][1]) || 1;
    const avgY = 0.5 * (poly[i][1] + poly[j][1]);
    // Prefer topmost horizontal-ish roof edge
    const scoreY = avgY + (dx / len < 0.32 ? 9999 : 0);
    if (scoreY < bestEdge.avgY) {
      bestEdge = { pA: i, pB: j, avgY: scoreY };
    }
  }
  const a = poly[bestEdge.pA];
  const b = poly[bestEdge.pB];
  const mx = 0.5 * (a[0] + b[0]);
  const my = 0.5 * (a[1] + b[1]);
  const stem = Math.max(16, canvasW * 0.015);
  return {
    pA: bestEdge.pA,
    pB: bestEdge.pB,
    anchorX: mx,
    anchorY: my,
    x: mx + Math.max(14, canvasW * 0.018),
    y: Math.max(14, my - stem),
  };
}

function hitTestCropRoi(mx, my, canvas) {
  const d = state.calib.cropDraft || { x: 0, y: 0.25, w: 1, h: 0.5 };
  const x0 = d.x * canvas.width;
  const y0 = d.y * canvas.height;
  const x1 = (d.x + d.w) * canvas.width;
  const y1 = (d.y + d.h) * canvas.height;
  const xm = 0.5 * (x0 + x1);
  const ym = 0.5 * (y0 + y1);
  const hitR = Math.max(14, (canvas.width * 0.015) / Math.sqrt(state.calib.zoom || 1));

  const handles = [
    { handle: "nw", x: x0, y: y0, cursor: "nwse-resize" },
    { handle: "ne", x: x1, y: y0, cursor: "nesw-resize" },
    { handle: "se", x: x1, y: y1, cursor: "nwse-resize" },
    { handle: "sw", x: x0, y: y1, cursor: "nesw-resize" },
    { handle: "n", x: xm, y: y0, cursor: "ns-resize" },
    { handle: "s", x: xm, y: y1, cursor: "ns-resize" },
    { handle: "w", x: x0, y: ym, cursor: "ew-resize" },
    { handle: "e", x: x1, y: ym, cursor: "ew-resize" },
  ];
  for (const h of handles) {
    if (Math.hypot(mx - h.x, my - h.y) <= hitR) {
      return { type: "crop_handle", ...h };
    }
  }
  const edgeTol = Math.max(10, hitR * 0.75);
  if (mx >= x0 && mx <= x1 && Math.abs(my - y0) <= edgeTol) {
    return { type: "crop_handle", handle: "n", cursor: "ns-resize" };
  }
  if (mx >= x0 && mx <= x1 && Math.abs(my - y1) <= edgeTol) {
    return { type: "crop_handle", handle: "s", cursor: "ns-resize" };
  }
  if (my >= y0 && my <= y1 && Math.abs(mx - x0) <= edgeTol) {
    return { type: "crop_handle", handle: "w", cursor: "ew-resize" };
  }
  if (my >= y0 && my <= y1 && Math.abs(mx - x1) <= edgeTol) {
    return { type: "crop_handle", handle: "e", cursor: "ew-resize" };
  }
  if (mx >= x0 && mx <= x1 && my >= y0 && my <= y1) {
    return { type: "crop_move", cursor: "move" };
  }
  return null;
}

function updateCropDraftReadoutUI() {
  const el = document.getElementById("cropDraftReadout");
  if (!el) return;
  const d = state.calib.cropDraft || { x: 0, y: 0, w: 1, h: 1 };
  const x0Pct = Math.round(d.x * 100);
  const x1Pct = Math.round((d.x + d.w) * 100);
  const y0Pct = Math.round(d.y * 100);
  const y1Pct = Math.round((d.y + d.h) * 100);
  const rawW = state.calib.fullFrameW || state.calib.cropRoi?.raw_width || state.calib.imgW || 1280;
  const rawH = state.calib.fullFrameH || state.calib.cropRoi?.raw_height || state.calib.imgH || 720;
  const cw = Math.round(rawW * d.w);
  const ch = Math.round(rawH * d.h);
  el.textContent = `X: ${x0Pct}%~${x1Pct}% · Y: ${y0Pct}%~${y1Pct}% (${cw}×${ch})`;
}

function updateCropRoiStripUI() {
  const strip = document.getElementById("cropRoiSummaryStrip");
  const badge = document.getElementById("cropRoiStatusBadge");
  const desc = document.getElementById("cropRoiSummaryText");
  const btnRestoreQuick = document.getElementById("btnRestoreFullFrameQuick");
  const btnDisable = document.getElementById("btnDisableCropRoi");
  if (!strip || !badge || !desc) return;

  const roi = state.calib.cropRoi || { enabled: false, x: 0, y: 0, w: 1, h: 1 };
  const enabled = Boolean(roi.enabled && (roi.w < 0.998 || roi.h < 0.998));
  strip.classList.toggle("is-active-crop", enabled);
  btnRestoreQuick?.classList.toggle("hidden", !enabled);
  if (btnDisable) btnDisable.disabled = !enabled;

  if (enabled) {
    const x0 = Math.round((roi.x || 0) * 100);
    const x1 = Math.round(((roi.x || 0) + (roi.w || 1)) * 100);
    const y0 = Math.round((roi.y || 0) * 100);
    const y1 = Math.round(((roi.y || 0) + (roi.h || 1)) * 100);
    badge.className = "tpl-pill tpl-pill-ready";
    badge.textContent = `✂️ 裁剪区 Y:${y0}%~${y1}%`;
    desc.textContent = `已锁定固定裁剪 X:${x0}%~${x1}% · Y:${y0}%~${y1}%（${state.calib.imgW}×${state.calib.imgH}）`;
  } else {
    badge.className = "tpl-pill tpl-pill-idle";
    badge.textContent = `全画幅 ${state.calib.imgW}×${state.calib.imgH}`;
    desc.textContent = "竖拍长图可点工具栏「✂️ 裁剪区」仅保留车位带。";
  }
}

function updateCalibGuardUI() {
  const bar = document.getElementById("calibGuardBar");
  const pill = document.getElementById("calibHealthPill");
  const nextLbl = document.getElementById("calibNextCheckLabel");
  const desc = document.getElementById("calibHealthDesc");
  const modeSel = document.getElementById("calibSchedMode");
  const timeInput = document.getElementById("calibSchedNightTime");
  const hoursSel = document.getElementById("calibSchedIntervalHours");
  const autoCompChk = document.getElementById("chkAutoCompensateShift");
  const btnShift = document.getElementById("btnApplyDriftShiftNow");
  if (!bar || !pill || !desc) return;

  const sched = state.calib.calibSchedule || {
    enabled: false,
    mode: "nightly",
    night_time: "03:00",
    interval_hours: 12,
    auto_compensate_shift: true,
  };
  const health = state.calib.calibHealth || { status: "unverified" };

  const effMode = sched.enabled ? sched.mode || "nightly" : "off";
  if (modeSel) modeSel.value = effMode;
  if (timeInput) {
    timeInput.value = sched.night_time || "03:00";
    timeInput.classList.toggle("hidden", effMode !== "nightly");
  }
  if (hoursSel) {
    const hVal = String(Math.round(Number(sched.interval_hours) || 12));
    if ([...hoursSel.options].some((o) => o.value === hVal)) hoursSel.value = hVal;
    hoursSel.classList.toggle("hidden", effMode !== "interval");
  }
  if (autoCompChk) autoCompChk.checked = sched.auto_compensate_shift !== false;

  if (nextLbl) {
    if (!sched.enabled) {
      nextLbl.textContent = "定时核对：未开启（支持设为每晚固定时刻或定频自动核对）";
    } else {
      let nextStr = "";
      if (sched.next_check_ts) {
        const dt = new Date(Number(sched.next_check_ts) * 1000);
        const pad = (n) => String(n).padStart(2, "0");
        nextStr = ` · 下次自动核对 ${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ${pad(
          dt.getHours()
        )}:${pad(dt.getMinutes())}`;
      }
      nextLbl.textContent =
        sched.mode === "nightly"
          ? `🌙 每晚 ${sched.night_time || "03:00"} 自动核对${nextStr}`
          : `⏱️ 每 ${sched.interval_hours || 12} 小时自动核对${nextStr}`;
    }
  }

  const st = health.status || "unverified";
  const driftPx = Number(health.drift_px || 0);
  bar.classList.toggle("is-drift-warn", st === "drifted");

  if (st === "healthy") {
    pill.className = "tpl-pill tpl-pill-ready";
    pill.textContent = `✅ 机位稳固 (${driftPx.toFixed(1)}px)`;
  } else if (st === "compensated") {
    pill.className = "tpl-pill tpl-pill-ready";
    pill.textContent = `🔧 已自动平移纠偏 (${driftPx.toFixed(1)}px)`;
  } else if (st === "minor_drift") {
    pill.className = "tpl-pill tpl-pill-warn";
    pill.textContent = `⚠️ 轻微偏移 ${driftPx.toFixed(1)}px`;
  } else if (st === "drifted") {
    pill.className = "tpl-pill tpl-pill-danger";
    pill.textContent = `🚨 疑似机位偏移 ${driftPx.toFixed(1)}px`;
  } else {
    pill.className = "tpl-pill tpl-pill-idle";
    pill.textContent = "待首次核对";
  }

  if (st === "drifted" || st === "minor_drift") {
    bar.classList.remove("hidden");
    document.getElementById("btnToggleGuardDrawer")?.classList.add("active");
  }

  if (health.message) {
    let timeTag = "";
    if (health.last_verified_ts) {
      const d = new Date(Number(health.last_verified_ts) * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      timeTag = `[${pad(d.getHours())}:${pad(d.getMinutes())}] `;
    }
    desc.textContent = `${timeTag}${health.message}`;
  } else {
    desc.textContent = "基于车位外静止参照物自动检测机位偏移并平移补偿。";
  }

  const showQuickCompensate =
    driftPx >= 2.2 && !health.compensated && (st === "minor_drift" || st === "drifted");
  btnShift?.classList.toggle("hidden", !showQuickCompensate);
}

async function openCropRoiEditor() {
  const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
  if (!camId) return;
  const saveMsg = document.getElementById("calibSaveMsg");
  try {
    const ff = await api(`/api/cameras/${encodeURIComponent(camId)}/full-frame`);
    const b64 = ff.full_frame_jpeg_b64 || state.calib.groundBgDataUrl;
    if (!b64) {
      alert("当前机位尚无参考底图，请先点击「载入底图」或「上传参考图 AI 自动标定」后再裁剪车位区域。");
      return;
    }
    await new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        state.calib.fullFrameImg = img;
        state.calib.fullFrameDataUrl = b64;
        state.calib.fullFrameW = Number(ff.raw_width) || img.naturalWidth || 1280;
        state.calib.fullFrameH = Number(ff.raw_height) || img.naturalHeight || 720;
        resolve();
      };
      img.onerror = () => resolve();
      img.src = b64;
    });

    const curRoi = ff.crop_roi || state.calib.cropRoi;
    if (curRoi && curRoi.enabled && (curRoi.w < 0.998 || curRoi.h < 0.998)) {
      state.calib.cropDraft = {
        x: Number(curRoi.x) || 0,
        y: Number(curRoi.y) || 0,
        w: Number(curRoi.w) || 1,
        h: Number(curRoi.h) || 1,
      };
    } else {
      const sug = await api(`/api/cameras/${encodeURIComponent(camId)}/suggest-crop-roi`, {
        method: "POST",
        body: JSON.stringify({}),
      }).catch(() => null);
      if (sug?.suggested_crop_roi) {
        state.calib.cropDraft = {
          x: sug.suggested_crop_roi.x,
          y: sug.suggested_crop_roi.y,
          w: sug.suggested_crop_roi.w,
          h: sug.suggested_crop_roi.h,
        };
      } else {
        state.calib.cropDraft = { x: 0.0, y: 0.25, w: 1.0, h: 0.50 };
      }
    }
    updateCropDraftReadoutUI();
    setCalibMode("crop_roi");
    if (saveMsg) {
      saveMsg.style.color = "#0284C7";
      saveMsg.textContent = "✂️ 已展开全画幅原图：拖动青色选框上下/左右边缘框住车位带，点击「应用固定裁剪」即可锁定同机位裁剪参数";
    }
  } catch (err) {
    alert(`打开车位区裁剪失败：${err.message}`);
  }
}

async function applyCropRoiToServer(enabled) {
  const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
  if (!camId) return;
  const saveMsg = document.getElementById("calibSaveMsg");
  const payload = enabled
    ? { enabled: true, ...state.calib.cropDraft }
    : { enabled: false, x: 0.0, y: 0.0, w: 1.0, h: 1.0 };
  try {
    const res = await api(`/api/cameras/${encodeURIComponent(camId)}/crop-roi`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await refreshOverview();
    await loadCameraSpotsForCalib(camId);
    if (saveMsg) {
      saveMsg.style.color = "#1D9E31";
      saveMsg.textContent = enabled
        ? `✅ 已应用机位固定裁剪（${res.image_width}×${res.image_height}）并无损换算 ${res.spots?.length || 0} 个车位框，后续抓拍将严格使用同一裁剪参数`
        : `↩ 已恢复全画幅原图（${res.image_width}×${res.image_height}）并无损还原车位坐标`;
    }
  } catch (err) {
    alert(`更新机位裁剪区域失败：${err.message}`);
  }
}

function deriveDefaultAnchorsJS(spots, imgW, imgH) {
  const w = Math.max(64, Number(imgW) || 1280);
  const h = Math.max(64, Number(imgH) || 720);
  const validSpots = Array.isArray(spots)
    ? spots.filter((s) => Array.isArray(s.polygon) && s.polygon.length >= 3)
    : [];
  if (!validSpots.length) {
    return [
      { id: "A1", label: "左后沿基点", x: +(w * 0.14).toFixed(1), y: +(h * 0.30).toFixed(1), auto: true },
      { id: "A2", label: "右后沿基点", x: +(w * 0.86).toFixed(1), y: +(h * 0.30).toFixed(1), auto: true },
      { id: "A3", label: "右前沿基点", x: +(w * 0.90).toFixed(1), y: +(h * 0.78).toFixed(1), auto: true },
      { id: "A4", label: "左前沿基点", x: +(w * 0.10).toFixed(1), y: +(h * 0.78).toFixed(1), auto: true },
    ];
  }
  const sorted = [...validSpots].sort((a, b) => {
    const ax = a.polygon.reduce((s, p) => s + p[0], 0) / a.polygon.length;
    const bx = b.polygon.reduce((s, p) => s + p[0], 0) / b.polygon.length;
    return ax - bx;
  });
  const pickCorners = (poly) => {
    const byY = [...poly].sort((p, q) => p[1] - q[1]);
    const top2 = byY.slice(0, 2).sort((p, q) => p[0] - q[0]);
    const bot2 = byY.slice(-2).sort((p, q) => p[0] - q[0]);
    return { tl: top2[0], tr: top2[top2.length - 1], bl: bot2[0], br: bot2[bot2.length - 1] };
  };
  const first = pickCorners(sorted[0].polygon);
  const last = pickCorners(sorted[sorted.length - 1].polygon);
  return [
    { id: "A1", label: "左后沿基点", x: +first.tl[0].toFixed(1), y: +first.tl[1].toFixed(1), auto: true },
    { id: "A2", label: "右后沿基点", x: +last.tr[0].toFixed(1), y: +last.tr[1].toFixed(1), auto: true },
    { id: "A3", label: "右前沿基点", x: +last.br[0].toFixed(1), y: +last.br[1].toFixed(1), auto: true },
    { id: "A4", label: "左前沿基点", x: +first.bl[0].toFixed(1), y: +first.bl[1].toFixed(1), auto: true },
  ];
}

function updateAnchorPointsReadoutUI() {
  const el = document.getElementById("anchorPointsReadout");
  if (!el) return;
  const pts = Array.isArray(state.calib.anchorPoints) ? state.calib.anchorPoints : [];
  const customCnt = pts.filter((p) => !p.auto).length;
  el.textContent = `已设 ${pts.length} 个地面定位锚点（${customCnt > 0 ? `${customCnt}个手动精标` : "四角自动基点"}）`;
}

function openAnchorPointsEditor() {
  if (state.calib.mode === "anchor_points") {
    setCalibMode(state.calib.spots.length > 0 ? "smart_edit" : "curved_row");
    return;
  }
  if (!Array.isArray(state.calib.anchorPoints) || state.calib.anchorPoints.length < 4) {
    state.calib.anchorPoints = deriveDefaultAnchorsJS(state.calib.spots, state.calib.imgW, state.calib.imgH);
  }
  updateAnchorPointsReadoutUI();
  setCalibMode("anchor_points");
  const saveMsg = document.getElementById("calibSaveMsg");
  if (saveMsg) {
    saveMsg.style.color = "#D97706";
    saveMsg.textContent = "📍 固定定位锚点模式：拖动金色菱形 A1~A4 到路牙拐点/灯杆底座/固定砖缝上（点击空白处可新增至多6个锚点），完成后点击「保存固定定位锚点」";
  }
}

async function resetAutoAnchorPoints() {
  const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
  if (!camId) return;
  try {
    const res = await api(`/api/cameras/${encodeURIComponent(camId)}/anchors`, {
      method: "POST",
      body: JSON.stringify({ reset_auto: true }),
    });
    state.calib.anchorPoints = Array.isArray(res.anchor_points)
      ? res.anchor_points
      : deriveDefaultAnchorsJS(state.calib.spots, state.calib.imgW, state.calib.imgH);
    updateAnchorPointsReadoutUI();
    renderCalibCanvas();
    const saveMsg = document.getElementById("calibSaveMsg");
    if (saveMsg) {
      saveMsg.style.color = "#1D9E31";
      saveMsg.textContent = `✅ 已按当前车位排四角重新生成并保存 ${state.calib.anchorPoints.length} 个地面定位锚点`;
    }
  } catch (err) {
    alert(`重置自动锚点失败：${err.message}`);
  }
}

async function saveAnchorPointsToServer() {
  const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
  if (!camId) return;
  const pts = Array.isArray(state.calib.anchorPoints) ? state.calib.anchorPoints : [];
  try {
    const res = await api(`/api/cameras/${encodeURIComponent(camId)}/anchors`, {
      method: "POST",
      body: JSON.stringify({ anchor_points: pts }),
    });
    state.calib.anchorPoints = Array.isArray(res.anchor_points) ? res.anchor_points : pts;
    updateAnchorPointsReadoutUI();
    renderCalibCanvas();
    const saveMsg = document.getElementById("calibSaveMsg");
    if (saveMsg) {
      saveMsg.style.color = "#1D9E31";
      saveMsg.textContent = `✅ 已保存 ${state.calib.anchorPoints.length} 个机位固定定位锚点，后续位移/倾斜/变焦抓拍将自动执行单应性透视对齐`;
    }
  } catch (err) {
    alert(`保存固定定位锚点失败：${err.message}`);
  }
}

function drawAnchorDiamondHandle(ctx, x, y, idText, subtitle = "", prominent = true) {
  ctx.save();
  const r = prominent ? 9.5 : 5.8;
  // Crosshair ticks
  ctx.strokeStyle = prominent ? "rgba(255, 214, 10, 0.96)" : "rgba(255, 214, 10, 0.72)";
  ctx.lineWidth = prominent ? 2.0 : 1.3;
  ctx.beginPath();
  ctx.moveTo(x - r * 1.65, y);
  ctx.lineTo(x + r * 1.65, y);
  ctx.moveTo(x, y - r * 1.65);
  ctx.lineTo(x, y + r * 1.65);
  ctx.stroke();

  // Gold diamond
  ctx.beginPath();
  ctx.moveTo(x, y - r);
  ctx.lineTo(x + r, y);
  ctx.lineTo(x, y + r);
  ctx.lineTo(x - r, y);
  ctx.closePath();
  ctx.fillStyle = prominent ? "#F59E0B" : "rgba(245, 158, 11, 0.85)";
  ctx.fill();
  ctx.strokeStyle = "#FFFFFF";
  ctx.lineWidth = prominent ? 2.2 : 1.5;
  ctx.stroke();

  // Label pill
  const tag = subtitle && prominent ? `📍${idText} ${subtitle}` : `📍${idText}`;
  ctx.font = prominent ? "700 12px -apple-system, BlinkMacSystemFont, sans-serif" : "600 10.5px -apple-system, BlinkMacSystemFont, sans-serif";
  const tw = ctx.measureText(tag).width;
  const pw = tw + (prominent ? 12 : 8);
  const ph = prominent ? 20 : 16;
  const px = x + r + 5;
  const py = y - ph / 2;
  ctx.fillStyle = prominent ? "rgba(24, 24, 27, 0.88)" : "rgba(24, 24, 27, 0.68)";
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(px, py, pw, ph, 5);
  else ctx.rect(px, py, pw, ph);
  ctx.fill();
  ctx.strokeStyle = "rgba(245, 158, 11, 0.78)";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.fillStyle = "#FDE68A";
  ctx.textAlign = "left";
  ctx.fillText(tag, px + (prominent ? 6 : 4), y + (prominent ? 4.2 : 3.5));
  ctx.restore();
}

function pointToSegmentDist(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  if (len2 < 1e-6) return Math.hypot(px - x1, py - y1);
  const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function isPointInPolygon(px, py, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    const intersect = yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi + 1e-9) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

function hitTestSmartEdit(mx, my, canvas) {
  const hitR = Math.max(11, (canvas.width * 0.011) / Math.sqrt(state.calib.zoom));
  const edgeTol = Math.max(8, (canvas.width * 0.0075) / Math.sqrt(state.calib.zoom));

  // 1. Rotation handle & 3D Roof-Height handle of currently selected spot
  const selIdx = state.calib.selectedSpotIdx;
  if (selIdx >= 0 && selIdx < state.calib.spots.length) {
    const selSpot = state.calib.spots[selIdx];
    const selPoly = getEditableSpotPoly(selSpot);
    if (state.calib.layerMode === "vehicle" || state.calib.layerMode === "dual") {
      const roofH = getRoofHeightHandlePos(ensureSpotVehiclePolygon(selSpot), canvas.width);
      if (roofH && Math.hypot(mx - roofH.x, my - roofH.y) <= hitR * 1.25) {
        return { type: "roof_height", spotIdx: selIdx, roofH };
      }
    }
    const rotH = getRotationHandlePos(selPoly, canvas.width);
    if (rotH && Math.hypot(mx - rotH.x, my - rotH.y) <= hitR * 1.15) {
      return { type: "rotate", spotIdx: selIdx, rotH };
    }
  }

  // 2. Corner vertices (check selected spot first so overlapping shared corners prioritize selected)
  const order = [];
  if (selIdx >= 0 && selIdx < state.calib.spots.length) order.push(selIdx);
  for (let i = 0; i < state.calib.spots.length; i++) {
    if (i !== selIdx) order.push(i);
  }
  for (const sIdx of order) {
    const poly = getEditableSpotPoly(state.calib.spots[sIdx]);
    for (let pIdx = 0; pIdx < poly.length; pIdx++) {
      if (Math.hypot(mx - poly[pIdx][0], my - poly[pIdx][1]) <= hitR) {
        return { type: "vertex", spotIdx: sIdx, ptIdx: pIdx };
      }
    }
  }

  // 3. Polygon edges / shared divider lines (all 4 edges freely draggable)
  for (const sIdx of order) {
    const poly = getEditableSpotPoly(state.calib.spots[sIdx]);
    for (let eIdx = 0; eIdx < poly.length; eIdx++) {
      const nextIdx = (eIdx + 1) % poly.length;
      const d = pointToSegmentDist(
        mx,
        my,
        poly[eIdx][0],
        poly[eIdx][1],
        poly[nextIdx][0],
        poly[nextIdx][1]
      );
      if (d <= edgeTol) {
        const isHorizontal =
          Math.abs(poly[nextIdx][0] - poly[eIdx][0]) >=
          Math.abs(poly[nextIdx][1] - poly[eIdx][1]);
        return { type: "edge", spotIdx: sIdx, pA: eIdx, pB: nextIdx, isHorizontal };
      }
    }
  }

  // 4. Polygon interior body
  for (const sIdx of order) {
    if (isPointInPolygon(mx, my, getEditableSpotPoly(state.calib.spots[sIdx]))) {
      return { type: "body", spotIdx: sIdx };
    }
  }
  return null;
}

function collectLinkedVertices(anchorPt, primarySpotIdx, primaryPtIdx, allowMagnetic) {
  const linked = [{ spotIdx: primarySpotIdx, ptIdx: primaryPtIdx }];
  if (!allowMagnetic) return linked;
  const magnetR = Math.max(14, state.calib.imgW * 0.014);
  state.calib.spots.forEach((sp, sIdx) => {
    const poly = getEditableSpotPoly(sp);
    poly.forEach((pt, pIdx) => {
      if (sIdx === primarySpotIdx && pIdx === primaryPtIdx) return;
      if (Math.hypot(pt[0] - anchorPt[0], pt[1] - anchorPt[1]) <= magnetR) {
        linked.push({ spotIdx: sIdx, ptIdx: pIdx });
      }
    });
  });
  return linked;
}

function weldAllNeighborDividersClient() {
  const spots = state.calib.spots;
  const isVeh = state.calib.layerMode === "vehicle";
  const getP = (s) => (isVeh ? ensureSpotVehiclePolygon(s) : s.polygon);
  const stdIdxs = spots
    .map((s, idx) => (s.spot_type === "standard" && getP(s).length === 4 ? idx : -1))
    .filter((idx) => idx >= 0);
  if (stdIdxs.length < 2) return;

  pushCalibUndoSnapshot();
  stdIdxs.sort((a, b) => getPolyCentroid(getP(spots[a]))[0] - getPolyCentroid(getP(spots[b]))[0]);

  const widths = stdIdxs.map((i) => {
    const p = getP(spots[i]);
    return (
      0.5 *
      (Math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1]) +
        Math.hypot(p[2][0] - p[3][0], p[2][1] - p[3][1]))
    );
  });
  widths.sort((a, b) => a - b);
  const medW = widths[Math.floor(widths.length / 2)] || 80;
  const tol = medW * 0.48;

  for (let k = 0; k < stdIdxs.length - 1; k++) {
    const lp = getP(spots[stdIdxs[k]]);
    const rp = getP(spots[stdIdxs[k + 1]]);
    const dTop = Math.hypot(lp[1][0] - rp[0][0], lp[1][1] - rp[0][1]);
    const dBot = Math.hypot(lp[2][0] - rp[3][0], lp[2][1] - rp[3][1]);
    if (dTop <= tol && dBot <= tol) {
      const mt = [
        Math.round(0.5 * (lp[1][0] + rp[0][0])),
        Math.round(0.5 * (lp[1][1] + rp[0][1])),
      ];
      const mb = [
        Math.round(0.5 * (lp[2][0] + rp[3][0])),
        Math.round(0.5 * (lp[2][1] + rp[3][1])),
      ];
      lp[1] = [...mt];
      rp[0] = [...mt];
      lp[2] = [...mb];
      rp[3] = [...mb];
      if (isVeh) {
        spots[stdIdxs[k]].vehicle_poly_custom = 1;
        spots[stdIdxs[k + 1]].vehicle_poly_custom = 1;
      }
    }
  }
  renderSpotListSidebar();
  renderCalibCanvas();
}

function autoRenumberSpotsClient() {
  if (!state.calib.spots.length) return;
  pushCalibUndoSnapshot();
  const selObj =
    state.calib.selectedSpotIdx >= 0 ? state.calib.spots[state.calib.selectedSpotIdx] : null;
  state.calib.spots.sort(
    (a, b) => getPolyCentroid(a.polygon)[0] - getPolyCentroid(b.polygon)[0]
  );
  state.calib.spots.forEach((sp, i) => {
    if (sp.spot_type !== "elastic_strip") {
      sp.label = String(i + 1).padStart(2, "0");
    }
  });
  if (selObj) state.calib.selectedSpotIdx = state.calib.spots.indexOf(selObj);
  syncSelectedSpotInspector();
  renderSpotListSidebar();
  renderCalibCanvas();
}

function splitSelectedSpotClient() {
  const idx = state.calib.selectedSpotIdx;
  if (idx < 0 || idx >= state.calib.spots.length) return;
  const sp = state.calib.spots[idx];
  if (sp.polygon.length !== 4) return;
  pushCalibUndoSnapshot();
  const [p0, p1, p2, p3] = sp.polygon;
  const midTop = [Math.round(0.5 * (p0[0] + p1[0])), Math.round(0.5 * (p0[1] + p1[1]))];
  const midBot = [Math.round(0.5 * (p3[0] + p2[0])), Math.round(0.5 * (p3[1] + p2[1]))];

  const leftPoly = [[...p0], [...midTop], [...midBot], [...p3]];
  const rightPoly = [[...midTop], [...p1], [...p2], [...midBot]];
  const leftSpot = {
    ...JSON.parse(JSON.stringify(sp)),
    spot_id: undefined,
    label: `${sp.label}-A`,
    polygon: leftPoly,
    vehicle_polygon: deriveVehicleEnvelopeJS(leftPoly),
    vehicle_poly_custom: 0,
  };
  const rightSpot = {
    ...JSON.parse(JSON.stringify(sp)),
    spot_id: undefined,
    label: `${sp.label}-B`,
    polygon: rightPoly,
    vehicle_polygon: deriveVehicleEnvelopeJS(rightPoly),
    vehicle_poly_custom: 0,
  };
  state.calib.spots.splice(idx, 1, leftSpot, rightSpot);
  // Auto renumber cleanly if labels are numeric
  if (/^\d+$/.test(String(sp.label))) {
    state.calib.spots.forEach((item, i) => {
      if (item.spot_type !== "elastic_strip") item.label = String(i + 1).padStart(2, "0");
    });
  }
  state.calib.selectedSpotIdx = idx;
  syncSelectedSpotInspector();
  renderSpotListSidebar();
  renderCalibCanvas();
}

function cloneSelectedSpotNextClient() {
  const idx = state.calib.selectedSpotIdx;
  if (idx < 0 || idx >= state.calib.spots.length) return;
  const sp = state.calib.spots[idx];
  if (sp.polygon.length !== 4) return;
  pushCalibUndoSnapshot();
  const [p0, p1, p2, p3] = sp.polygon;
  const clampX = (v) => Math.max(0, Math.min(state.calib.imgW - 1, Math.round(v)));
  const clampY = (v) => Math.max(0, Math.min(state.calib.imgH - 1, Math.round(v)));
  const newPoly = [
    [...p1],
    [clampX(p1[0] + (p1[0] - p0[0])), clampY(p1[1] + (p1[1] - p0[1]))],
    [clampX(p2[0] + (p2[0] - p3[0])), clampY(p2[1] + (p2[1] - p3[1]))],
    [...p2],
  ];
  const nextNum = String(state.calib.spots.length + 1).padStart(2, "0");
  const cloned = {
    label: nextNum,
    spot_type: sp.spot_type || "standard",
    compact_only: Boolean(sp.compact_only),
    total_meters: sp.total_meters || 5.2,
    capacity: 1,
    status: sp.status || "FREE",
    initial_status: sp.status || "FREE",
    polygon: newPoly,
    vehicle_polygon: deriveVehicleEnvelopeJS(newPoly),
    vehicle_poly_custom: 0,
  };
  state.calib.spots.splice(idx + 1, 0, cloned);
  state.calib.selectedSpotIdx = idx + 1;
  syncSelectedSpotInspector();
  renderSpotListSidebar();
  renderCalibCanvas();
}

function bindCalibrationConsole() {
  const canvas = document.getElementById("calibCanvas");
  const scrollStage = document.getElementById("calibStageScroll");

  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => setCalibMode(btn.dataset.mode));
  });
  document.querySelectorAll(".js-back-smart-edit").forEach((btn) => {
    btn.addEventListener("click", () => setCalibMode("smart_edit"));
  });
  document.getElementById("btnToggleGuardDrawer")?.addEventListener("click", (e) => {
    const bar = document.getElementById("calibGuardBar");
    if (!bar) return;
    const nowHidden = bar.classList.toggle("hidden");
    e.currentTarget?.classList.toggle("active", !nowHidden);
  });
  document.getElementById("cropRoiStatusBadge")?.addEventListener("click", () => {
    setCalibMode(state.calib.mode === "crop_roi" ? "smart_edit" : "crop_roi");
  });
  document.addEventListener("click", (evt) => {
    const drawMenu = document.getElementById("calibDrawMenu");
    if (drawMenu?.open && !drawMenu.contains(evt.target)) {
      drawMenu.open = false;
    }
  });

  // Dual-Layer Spatial Calibration controls (Ground Layer + 3D Vehicle Envelope Layer)
  document.querySelectorAll(".layer-btn").forEach((btn) => {
    btn.addEventListener("click", () => setCalibLayerMode(btn.dataset.layer));
  });

  const vehHeightSlider = document.getElementById("vehHeightScale");
  if (vehHeightSlider) {
    vehHeightSlider.addEventListener("input", () => {
      const v = Number(vehHeightSlider.value);
      state.calib.vehHeightScale = v / 100.0;
      document.getElementById("vehHeightScaleVal").textContent = `${v}%`;
      state.calib.spots.forEach((sp) => {
        if (!sp.vehicle_poly_custom) {
          sp.vehicle_polygon = deriveVehicleEnvelopeJS(sp.polygon);
        }
      });
      markCalibDirty(true);
      renderCalibCanvas();
    });
  }

  const vehSlantSlider = document.getElementById("vehSlantBias");
  if (vehSlantSlider) {
    vehSlantSlider.addEventListener("input", () => {
      const v = Number(vehSlantSlider.value);
      state.calib.vehSlantBias = v / 100.0;
      document.getElementById("vehSlantBiasVal").textContent = `${v > 0 ? "+" : ""}${v}%`;
      state.calib.spots.forEach((sp) => {
        if (!sp.vehicle_poly_custom) {
          sp.vehicle_polygon = deriveVehicleEnvelopeJS(sp.polygon);
        }
      });
      markCalibDirty(true);
      renderCalibCanvas();
    });
  }

  document.getElementById("chkShowGroundGhost")?.addEventListener("change", (e) => {
    state.calib.showGroundGhost = Boolean(e.target.checked);
    renderCalibCanvas();
  });

  const vehTplInput = document.getElementById("vehTemplateFileInput");
  document.getElementById("btnUploadVehTemplate")?.addEventListener("click", () => {
    vehTplInput?.click();
  });
  vehTplInput?.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const dataUrl = ev.target.result;
      const camId = document.getElementById("calibCameraSelect").value;
      const saveMsg = document.getElementById("calibSaveMsg");
      try {
        const res = await api(`/api/cameras/${encodeURIComponent(camId)}/vehicle-template`, {
          method: "POST",
          body: JSON.stringify({ image_base64: dataUrl }),
        });
        const storedUrl = res.vehicle_template_jpeg_b64 || dataUrl;
        await new Promise((resolve) => {
          const img = new Image();
          img.onload = () => {
            state.calib.vehicleBgImage = img;
            state.calib.vehicleBgDataUrl = storedUrl;
            state.calib.vehicleBgIsServerPreview = Boolean(res.vehicle_template_jpeg_b64);
            resolve();
          };
          img.onerror = () => resolve();
          img.src = storedUrl;
        });
        setCalibLayerMode("vehicle");
        if (saveMsg) {
          saveMsg.style.color = "#1D9E31";
          saveMsg.textContent = `✅ 已保存「车辆停入参考底图」（与无车地面模板完全独立隔离），现在可直接拖拽框定车辆轮廓`;
        }
      } catch (err) {
        alert(`上传有车停入参考图失败：${err.message}`);
      }
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  });

  document.getElementById("btnDeriveVehicleLayer")?.addEventListener("click", async () => {
    if (!state.calib.spots.length) return;
    pushCalibUndoSnapshot();
    const res = await api("/api/calibration/derive-vehicle-polygons", {
      method: "POST",
      body: JSON.stringify({
        spots: state.calib.spots,
        image_width: state.calib.imgW,
        image_height: state.calib.imgH,
        height_scale: state.calib.vehHeightScale,
        slant_bias: state.calib.vehSlantBias,
      }),
    });
    // Strictly update ONLY vehicle_polygon without altering ground polygon!
    const derivedList = res.spots || [];
    state.calib.spots.forEach((sp, i) => {
      if (derivedList[i]?.vehicle_polygon) {
        sp.vehicle_polygon = derivedList[i].vehicle_polygon;
        sp.vehicle_poly_custom = 0;
      }
    });
    setCalibLayerMode("vehicle");
    markCalibDirty(true);
    renderSpotListSidebar();
    renderCalibCanvas();
    const saveMsg = document.getElementById("calibSaveMsg");
    if (saveMsg) {
      saveMsg.style.color = "#0071E3";
      saveMsg.textContent = `🚗 已在车辆层生成 ${state.calib.spots.length} 个车体初稿框（地面框丝毫无损，微调后点保存车辆停入标定）`;
    }
  });

  document.getElementById("btnBenchmarkDualLayer")?.addEventListener("click", runDualLayerBenchmarkUI);
  document.getElementById("btnCloseDualBench")?.addEventListener("click", () => {
    document.getElementById("dualBenchPanel")?.classList.add("hidden");
  });

  document.getElementById("chkMagneticWeld").addEventListener("change", (e) => {
    const checked = Boolean(e.target.checked);
    state.calib.magneticWeld = checked;
    if (state.calib.layerMode === "vehicle") {
      state.calib.vehicleMagneticWeld = checked;
    } else {
      state.calib.groundMagneticWeld = checked;
    }
  });
  document.getElementById("btnWeldNeighbors").addEventListener("click", weldAllNeighborDividersClient);

  // Per-spot independent 3D vehicle height slider & single-spot 3D reset
  const selVehSlider = document.getElementById("selSpotVehHeightSlider");
  if (selVehSlider) {
    let sliderUndoSaved = false;
    selVehSlider.addEventListener("mousedown", () => {
      sliderUndoSaved = false;
    });
    selVehSlider.addEventListener("input", (e) => {
      const sp = state.calib.spots[state.calib.selectedSpotIdx];
      if (!sp) return;
      if (!sliderUndoSaved) {
        pushCalibUndoSnapshot();
        sliderUndoSaved = true;
      }
      const pct = Number(e.target.value);
      const valEl = document.getElementById("selSpotVehHeightVal");
      if (valEl) valEl.textContent = `${pct}%`;
      sp.vehicle_polygon = deriveVehicleEnvelopeJS(
        sp.polygon,
        state.calib.imgW,
        state.calib.imgH,
        pct / 100.0,
        state.calib.vehSlantBias
      );
      sp.vehicle_poly_custom = 1;
      markCalibDirty(true);
      renderSpotListSidebar();
      renderCalibCanvas();
    });
  }

  document.getElementById("btnResetSingleSpot3D")?.addEventListener("click", () => {
    const sp = state.calib.spots[state.calib.selectedSpotIdx];
    if (!sp) return;
    pushCalibUndoSnapshot();
    sp.vehicle_polygon = deriveVehicleEnvelopeJS(sp.polygon);
    sp.vehicle_poly_custom = 0;
    syncSelectedSpotInspector();
    renderSpotListSidebar();
    renderCalibCanvas();
  });

  // Per-camera Fixed Parking-Strip ROI Crop controls
  document.getElementById("btnToggleCropRoiMode")?.addEventListener("click", openCropRoiEditor);
  document.getElementById("btnOpenCropEditorQuick")?.addEventListener("click", openCropRoiEditor);
  document.getElementById("btnRestoreFullFrameQuick")?.addEventListener("click", () => {
    applyCropRoiToServer(false);
  });
  document.getElementById("btnDisableCropRoi")?.addEventListener("click", () => {
    applyCropRoiToServer(false);
  });
  document.getElementById("btnApplyCropRoi")?.addEventListener("click", () => {
    applyCropRoiToServer(true);
  });
  document.getElementById("btnCancelCropRoi")?.addEventListener("click", () => {
    setCalibMode(state.calib.spots.length > 0 ? "smart_edit" : "curved_row");
  });
  document.getElementById("btnToggleAnchorMode")?.addEventListener("click", openAnchorPointsEditor);
  document.getElementById("btnAutoResetAnchors")?.addEventListener("click", resetAutoAnchorPoints);
  document.getElementById("btnSaveAnchorPoints")?.addEventListener("click", saveAnchorPointsToServer);
  document.getElementById("btnCancelAnchorMode")?.addEventListener("click", () => {
    setCalibMode(state.calib.spots.length > 0 ? "smart_edit" : "curved_row");
  });
  document.getElementById("btnAutoSuggestCropRoi")?.addEventListener("click", async () => {
    const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
    if (!camId) return;
    try {
      const sug = await api(`/api/cameras/${encodeURIComponent(camId)}/suggest-crop-roi`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (sug?.suggested_crop_roi) {
        state.calib.cropDraft = {
          x: sug.suggested_crop_roi.x,
          y: sug.suggested_crop_roi.y,
          w: sug.suggested_crop_roi.w,
          h: sug.suggested_crop_roi.h,
        };
        updateCropDraftReadoutUI();
        renderCalibCanvas();
      }
    } catch (err) {
      alert(`智能框选车位带失败：${err.message}`);
    }
  });

  // Scheduled / Nightly Calibration Drift Verification controls
  document.getElementById("calibSchedMode")?.addEventListener("change", (e) => {
    const m = e.target.value;
    document.getElementById("calibSchedNightTime")?.classList.toggle("hidden", m !== "nightly");
    document.getElementById("calibSchedIntervalHours")?.classList.toggle("hidden", m !== "interval");
  });

  document.getElementById("btnSaveCalibSchedule")?.addEventListener("click", async () => {
    const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
    if (!camId) return;
    const modeVal = document.getElementById("calibSchedMode")?.value || "off";
    const nightTime = document.getElementById("calibSchedNightTime")?.value || "03:00";
    const intervalHours = Number(document.getElementById("calibSchedIntervalHours")?.value || 12);
    const autoComp = Boolean(document.getElementById("chkAutoCompensateShift")?.checked);
    const saveMsg = document.getElementById("calibSaveMsg");
    try {
      const res = await api(`/api/cameras/${encodeURIComponent(camId)}/calib-schedule`, {
        method: "POST",
        body: JSON.stringify({
          enabled: modeVal !== "off",
          mode: modeVal === "off" ? "nightly" : modeVal,
          night_time: nightTime,
          interval_hours: intervalHours,
          auto_compensate_shift: autoComp,
        }),
      });
      state.calib.calibSchedule = res.calib_schedule;
      updateCalibGuardUI();
      if (saveMsg) {
        saveMsg.style.color = "#1D9E31";
        saveMsg.textContent = res.calib_schedule?.enabled
          ? res.calib_schedule.mode === "nightly"
            ? `🛡️ 已保存定时巡检：每晚 ${res.calib_schedule.night_time} 自动完成一次机位偏移核对`
            : `🛡️ 已保存定时巡检：每 ${res.calib_schedule.interval_hours} 小时自动完成一次机位偏移核对`
          : "已关闭该机位自动标定核对计划";
      }
      await refreshOverview();
    } catch (err) {
      alert(`保存自动核对计划失败：${err.message}`);
    }
  });

  const runCalibVerifyNow = async (applyCompensate = false) => {
    const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
    if (!camId) return;
    const btnVer = document.getElementById("btnVerifyCalibNow");
    const saveMsg = document.getElementById("calibSaveMsg");
    if (btnVer) {
      btnVer.disabled = true;
      btnVer.textContent = applyCompensate ? "纠偏中..." : "核对中...";
    }
    try {
      const customLocalFrame =
        !state.calib.groundBgIsServerPreview && state.calib.groundBgDataUrl
          ? state.calib.groundBgDataUrl
          : undefined;
      const res = await api(`/api/cameras/${encodeURIComponent(camId)}/verify-calibration`, {
        method: "POST",
        body: JSON.stringify({
          image_base64: customLocalFrame,
          apply_shift_compensation: applyCompensate,
        }),
      });
      state.calib.calibHealth = res;
      updateCalibGuardUI();
      if (res.compensated) {
        await loadCameraSpotsForCalib(camId);
      }
      await refreshOverview();
      if (saveMsg) {
        saveMsg.style.color = res.status === "drifted" ? "#D70015" : "#1D9E31";
        saveMsg.textContent = `🔍 标定核对结果：${res.message || res.status}`;
      }
    } catch (err) {
      alert(`执行标定核对失败：${err.message}`);
    } finally {
      if (btnVer) {
        btnVer.disabled = false;
        btnVer.textContent = "🔍 立即核对";
      }
    }
  };

  document.getElementById("btnVerifyCalibNow")?.addEventListener("click", () => runCalibVerifyNow(false));
  document.getElementById("btnApplyDriftShiftNow")?.addEventListener("click", () => runCalibVerifyNow(true));
  document.getElementById("btnAutoRenumber").addEventListener("click", autoRenumberSpotsClient);
  document.getElementById("btnSplitSpot").addEventListener("click", splitSelectedSpotClient);
  document.getElementById("btnCloneSpotNext").addEventListener("click", cloneSelectedSpotNextClient);
  document.getElementById("btnDeleteSelectedSpot").addEventListener("click", () => {
    if (state.calib.selectedSpotIdx >= 0) removeCalibSpot(state.calib.selectedSpotIdx);
  });

  document.getElementById("selSpotLabel").addEventListener("input", (e) => {
    const sp = state.calib.spots[state.calib.selectedSpotIdx];
    if (!sp) return;
    sp.label = e.target.value;
    markCalibDirty(true);
    renderSpotListSidebar();
    renderCalibCanvas();
  });
  document.getElementById("selSpotStatus").addEventListener("change", (e) => {
    const sp = state.calib.spots[state.calib.selectedSpotIdx];
    if (!sp) return;
    pushCalibUndoSnapshot();
    sp.status = e.target.value;
    sp.initial_status = e.target.value;
    renderSpotListSidebar();
    renderCalibCanvas();
  });
  document.getElementById("selSpotCompact").addEventListener("change", (e) => {
    const sp = state.calib.spots[state.calib.selectedSpotIdx];
    if (!sp) return;
    pushCalibUndoSnapshot();
    sp.compact_only = e.target.checked;
    sp.spot_type = e.target.checked ? "irregular" : sp.spot_type;
    renderSpotListSidebar();
    renderCalibCanvas();
  });

  // Undo / Redo / Opacity / Zoom controls
  document.getElementById("btnCalibUndo").addEventListener("click", performCalibUndo);
  document.getElementById("btnCalibRedo").addEventListener("click", performCalibRedo);

  const alphaSlider = document.getElementById("calibFillAlpha");
  alphaSlider.addEventListener("input", () => {
    const v = Number(alphaSlider.value);
    state.calib.fillAlpha = v / 100.0;
    document.getElementById("calibFillAlphaVal").textContent =
      v === 0 ? "纯线框(最清晰)" : `${v}%`;
    renderCalibCanvas();
  });

  document.getElementById("btnZoomIn").addEventListener("click", () => setCalibZoom(state.calib.zoom + 0.35));
  document.getElementById("btnZoomOut").addEventListener("click", () => setCalibZoom(state.calib.zoom - 0.35));
  document.getElementById("btnZoomReset").addEventListener("click", () => setCalibZoom(1.0));

  scrollStage.addEventListener(
    "wheel",
    (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey) {
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.2 : -0.2;
        setCalibZoom(state.calib.zoom + delta);
      }
    },
    { passive: false }
  );

  // Keyboard shortcuts when Calibration tab is active
  window.addEventListener("keydown", (e) => {
    const calibTabActive = document.getElementById("tab-calibrate")?.classList.contains("active");
    if (!calibTabActive) return;
    const tag = (e.target?.tagName || "").toLowerCase();
    const inInput = tag === "input" || tag === "textarea" || tag === "select";

    if (e.code === "Space" && !inInput) {
      state.calib.spacePressed = true;
      canvas.style.cursor = "grab";
      e.preventDefault();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !inInput) {
      e.preventDefault();
      if (e.shiftKey) performCalibRedo();
      else performCalibUndo();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y" && !inInput) {
      e.preventDefault();
      performCalibRedo();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      document.getElementById("btnSaveCalibration")?.click();
      return;
    }
    if (
      (e.key === "Delete" || e.key === "Backspace") &&
      !inInput &&
      state.calib.mode === "smart_edit" &&
      state.calib.selectedSpotIdx >= 0
    ) {
      e.preventDefault();
      removeCalibSpot(state.calib.selectedSpotIdx);
      return;
    }
    if (e.key === "Enter" && !inInput && state.calib.mode === "curved_row") {
      e.preventDefault();
      if (state.calib.curvePhase === "front" && state.calib.curveFrontPts.length >= 2) {
        state.calib.curvePhase = "back";
        updateCurvedHintUI();
        renderCalibCanvas();
      } else if (state.calib.previewTrapezoids.length > 0) {
        document.getElementById("btnCommitCurveRow")?.click();
      }
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
      state.calib.spacePressed = false;
      state.calib.panDrag = null;
      canvas.style.cursor = state.calib.mode === "smart_edit" ? "default" : "crosshair";
    }
  });

  document.getElementById("calibCameraSelect").addEventListener("change", async (e) => {
    state.lab.calibUserSelectedCam = true;
    state.calib.cameraId = e.target.value;
    document.getElementById("camCrudFormPanel")?.classList.add("hidden");
    renderCameraMetaSummaryCard();
    await loadCameraSpotsForCalib(state.calib.cameraId);
  });

  document.getElementById("calibZoneSelect").addEventListener("change", async (e) => {
    const newZoneId = e.target.value;
    state.calib.zoneId = newZoneId;
    const formPanel = document.getElementById("camCrudFormPanel");
    const isCreatingCam =
      formPanel && !formPanel.classList.contains("hidden") && state.calib.camFormMode === "create";
    const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
    if (!camId || isCreatingCam) return;
    const hintEl = document.getElementById("calibZoneAutoSaveHint");
    try {
      await api(`/api/cameras/${encodeURIComponent(camId)}`, {
        method: "PUT",
        body: JSON.stringify({ zone_id: newZoneId }),
      });
      await refreshOverview();
      renderCameraMetaSummaryCard();
      if (hintEl) {
        const zObj = (state.overview?.zones || []).find((z) => z.zone_id === newZoneId);
        hintEl.style.color = "#1D9E31";
        hintEl.textContent = `✓ 已将当前机位所属小区片区更新为「${
          zObj?.name || newZoneId
        }」，导览步行距离已同步重算`;
      }
    } catch (err) {
      if (hintEl) {
        hintEl.style.color = "#D70015";
        hintEl.textContent = `保存所属片区失败：${err.message}`;
      }
    }
  });

  // Structured Camera CRUD (Create / Edit Name, Code & ID / Delete)
  document.getElementById("btnOpenNewCamera")?.addEventListener("click", () => {
    openCameraCrudForm("create");
  });
  document.getElementById("btnOpenEditCamera")?.addEventListener("click", () => {
    openCameraCrudForm("edit");
  });
  document.getElementById("btnCancelCamForm")?.addEventListener("click", () => {
    document.getElementById("camCrudFormPanel")?.classList.add("hidden");
  });
  document.getElementById("btnCancelCamForm2")?.addEventListener("click", () => {
    document.getElementById("camCrudFormPanel")?.classList.add("hidden");
  });
  document.getElementById("camFormGroupCode")?.addEventListener("input", () => {
    updateCameraFormLivePreview(false);
  });
  document.getElementById("camFormMountAddr")?.addEventListener("input", () => {
    updateCameraFormLivePreview(false);
  });
  document.getElementById("camFormName")?.addEventListener("input", (e) => {
    if (e.target.value.trim()) e.target.dataset.manual = "1";
    else delete e.target.dataset.manual;
  });
  document.getElementById("camFormId")?.addEventListener("input", (e) => {
    if (e.target.value.trim()) e.target.dataset.manual = "1";
    else delete e.target.dataset.manual;
  });

  document.getElementById("btnSubmitCamForm")?.addEventListener("click", async () => {
    const group_code = document.getElementById("camFormGroupCode").value.trim();
    const mount_address = document.getElementById("camFormMountAddr").value.trim();
    let name = document.getElementById("camFormName").value.trim();
    let camera_id = document.getElementById("camFormId").value.trim();
    const zone_id = document.getElementById("calibZoneSelect").value;
    const parsed = group_code ? parseCameraGroupCodesClient(group_code, mount_address) : null;

    if (!name && parsed?.valid) name = parsed.suggested_name;
    if (!camera_id && parsed?.valid) camera_id = parsed.suggested_camera_id;
    if (!camera_id || !name) {
      return alert("请填写车位组编码（如 5LV7 或 10BV7+10BH2）或直接填写机位名称与代号");
    }

    const saveMsg = document.getElementById("calibSaveMsg");
    try {
      if (state.calib.camFormMode === "edit" && state.calib.editingOriginalCamId) {
        const res = await api(
          `/api/cameras/${encodeURIComponent(state.calib.editingOriginalCamId)}`,
          {
            method: "PUT",
            body: JSON.stringify({
              new_camera_id: camera_id,
              name,
              zone_id,
              group_code: parsed?.valid ? parsed.normalized_group_code : group_code,
              mount_address,
            }),
          }
        );
        state.calib.cameraId = res.camera.camera_id;
        document.getElementById("camCrudFormPanel")?.classList.add("hidden");
        await refreshOverview();
        document.getElementById("calibCameraSelect").value = state.calib.cameraId;
        renderCameraMetaSummaryCard();
        await loadCameraSpotsForCalib(state.calib.cameraId);
        if (saveMsg) {
          saveMsg.style.color = "#1D9E31";
          saveMsg.textContent = `✅ 机位信息已更新：${res.camera.name}（${res.camera.camera_id}）`;
        }
      } else {
        const res = await api("/api/cameras", {
          method: "POST",
          body: JSON.stringify({
            camera_id,
            name,
            zone_id,
            group_code: parsed?.valid ? parsed.normalized_group_code : group_code,
            mount_address,
            image_width: state.calib.imgW,
            image_height: state.calib.imgH,
          }),
        });
        const newCam = res.camera || { camera_id };
        state.calib.cameraId = newCam.camera_id;
        document.getElementById("camCrudFormPanel")?.classList.add("hidden");
        await refreshOverview();
        document.getElementById("calibCameraSelect").value = state.calib.cameraId;
        renderCameraMetaSummaryCard();
        await loadCameraSpotsForCalib(state.calib.cameraId);
        if (saveMsg) {
          saveMsg.style.color = "#1D9E31";
          saveMsg.textContent = `✅ 已新增机位：${newCam.name || name}（${state.calib.cameraId}）`;
        }
      }
    } catch (err) {
      alert(`保存机位失败：${err.message}`);
    }
  });

  document.getElementById("btnDeleteCamera")?.addEventListener("click", () => {
    const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value;
    if (!camId) return;
    openCameraDeleteModal(camId);
  });

  document.getElementById("calibImageInput").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const dataUrl = ev.target.result;
      const img = new Image();
      img.onload = async () => {
        state.calib.fullFrameImg = img;
        state.calib.fullFrameDataUrl = dataUrl;
        state.calib.fullFrameW = img.naturalWidth;
        state.calib.fullFrameH = img.naturalHeight;

        const camId = document.getElementById("calibCameraSelect").value;
        const saveMsg = document.getElementById("calibSaveMsg");

        // When calibrating Vehicle 3D layer or updating Ground template on an already-calibrated camera,
        // let backend SIFT+Anchor homography warp & crop the photo into exact calibrated canvas space!
        if (state.calib.layerMode === "vehicle" && camId) {
          try {
            const res = await api(`/api/cameras/${encodeURIComponent(camId)}/vehicle-template`, {
              method: "POST",
              body: JSON.stringify({ image_base64: dataUrl, auto_align: true }),
            });
            const alignedUrl = res.vehicle_template_jpeg_b64 || dataUrl;
            await new Promise((resolve) => {
              const aImg = new Image();
              aImg.onload = () => {
                state.calib.vehicleBgImage = aImg;
                state.calib.vehicleBgDataUrl = alignedUrl;
                state.calib.vehicleBgIsServerPreview = Boolean(res.vehicle_template_jpeg_b64);
                resolve();
              };
              aImg.onerror = () => resolve();
              aImg.src = alignedUrl;
            });
            syncCalibActiveBgImage();
            updateCropRoiStripUI();
            updateCalibButtons();
            renderCalibCanvas();
            if (saveMsg) {
              saveMsg.style.color = "#1D9E31";
              saveMsg.textContent = `✅ 已载入并自动透视对齐有车参考底图（${
                res.alignment?.method || "与标定坐标系一致"
              }），可直接微调 3D 车体框`;
            }
            return;
          } catch (_) {}
        } else if (
          camId &&
          Boolean(state.calib.cameraMeta?.has_template) &&
          Number(state.calib.cameraMeta?.calibrated_spot_count || 0) > 0 &&
          state.calib.spots.some((s) => Array.isArray(s.polygon) && s.polygon.length >= 3)
        ) {
          try {
            const res = await api(`/api/cameras/${encodeURIComponent(camId)}/ground-template`, {
              method: "POST",
              body: JSON.stringify({ image_base64: dataUrl, auto_align: true }),
            });
            const alignedUrl = res.template_jpeg_b64 || dataUrl;
            await new Promise((resolve) => {
              const aImg = new Image();
              aImg.onload = () => {
                state.calib.groundBgImage = aImg;
                state.calib.groundBgDataUrl = alignedUrl;
                state.calib.groundBgIsServerPreview = Boolean(res.template_jpeg_b64);
                resolve();
              };
              aImg.onerror = () => resolve();
              aImg.src = alignedUrl;
            });
            syncCalibActiveBgImage();
            updateCropRoiStripUI();
            updateCalibButtons();
            renderCalibCanvas();
            if (saveMsg) {
              saveMsg.style.color = "#1D9E31";
              saveMsg.textContent = `✅ 已载入并自动对齐无车地面底图（原标定车位框丝毫不变，可直接精细微调或点「重新自动标定」）`;
            }
            return;
          } catch (_) {}
        }

        const cropRes = cropImageSourceIfNeeded(
          img,
          state.calib.cropRoi,
          state.calib.imgW,
          state.calib.imgH
        );
        const effDataUrl = cropRes.dataUrl || dataUrl;
        let effImg = img;
        if (cropRes.wasCropped && cropRes.dataUrl) {
          await new Promise((resolve) => {
            const cImg = new Image();
            cImg.onload = () => {
              effImg = cImg;
              resolve();
            };
            cImg.onerror = () => resolve();
            cImg.src = cropRes.dataUrl;
          });
        }

        if (state.calib.spots.length > 0) {
          rescaleCalibSpotsToSize(effImg.naturalWidth || cropRes.width, effImg.naturalHeight || cropRes.height);
        } else if (saveMsg) {
          saveMsg.style.color = "#0A84FF";
          saveMsg.textContent =
            "ℹ️ 已载入原图底图：点击上方「重新自动标定」可一键智能裁剪车位带并生成 3D 空间标定；或手绘车位后点击「保存标定」自动裁剪";
        }

        if (state.calib.layerMode === "vehicle") {
          state.calib.vehicleBgImage = effImg;
          state.calib.vehicleBgDataUrl = effDataUrl;
          state.calib.vehicleBgIsServerPreview = false;
        } else {
          state.calib.groundBgImage = effImg;
          state.calib.groundBgDataUrl = effDataUrl;
          state.calib.groundBgIsServerPreview = false;
        }
        syncCalibActiveBgImage();
        updateCropRoiStripUI();
        updateCalibButtons();
        renderCalibCanvas();
      };
      img.src = dataUrl;
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  });

  const autoCalibInput = document.getElementById("autoCalibFileInput");
  document.getElementById("btnUploadAutoCalibrate").addEventListener("click", () => {
    autoCalibInput.click();
  });
  autoCalibInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const camId = document.getElementById("calibCameraSelect").value;
      const zoneId = document.getElementById("calibZoneSelect").value;
      await runAutoCalibrationWithDataUrl(camId, zoneId, ev.target.result, false);
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  });

  document.getElementById("btnRerunAutoCalibrate").addEventListener("click", async () => {
    const rawOrBgUrl = state.calib.fullFrameDataUrl || state.calib.bgDataUrl;
    if (!rawOrBgUrl) return;
    const camId = document.getElementById("calibCameraSelect").value;
    const zoneId = document.getElementById("calibZoneSelect").value;
    await runAutoCalibrationWithDataUrl(camId, zoneId, rawOrBgUrl, false);
  });

  document.getElementById("btnRefineWhiteLines").addEventListener("click", async () => {
    if (!state.calib.bgDataUrl || !state.calib.spots.length) return;
    const camId = document.getElementById("calibCameraSelect").value;
    const btnRef = document.getElementById("btnRefineWhiteLines");
    const saveMsg = document.getElementById("calibSaveMsg");
    btnRef.disabled = true;
    try {
      pushCalibUndoSnapshot();
      const res = await api(`/api/cameras/${encodeURIComponent(camId)}/refine-lines`, {
        method: "POST",
        body: JSON.stringify({
          image_base64: state.calib.bgDataUrl,
          spots: state.calib.spots,
          auto_save: true,
        }),
      });
      state.calib.spots = (res.spots || []).map((sp) => {
        ensureSpotVehiclePolygon(sp);
        return sp;
      });
      markCalibDirty(false);
      syncSelectedSpotInspector();
      renderSpotListSidebar();
      renderCalibCanvas();
      saveMsg.style.color = "#1D9E31";
      saveMsg.textContent = `已完成标线/相邻边自适应贴合（耗时 ${res.elapsed_ms} ms）`;
      await refreshOverview();
    } catch (err) {
      saveMsg.style.color = "#D70015";
      saveMsg.textContent = `标线贴合失败：${err.message}`;
    } finally {
      updateCalibButtons();
    }
  });

  document.getElementById("btnGenDemoScene").addEventListener("click", () => {
    if (state.calib.layerMode === "vehicle") {
      state.calib.vehicleBgImage = null;
      state.calib.vehicleBgDataUrl = null;
    } else {
      state.calib.groundBgImage = null;
      state.calib.groundBgDataUrl = null;
    }
    syncCalibActiveBgImage();
    state.calib.imgW = 1280;
    state.calib.imgH = 720;
    canvas.width = 1280;
    canvas.height = 720;
    setCalibZoom(1.0);
    updateCalibButtons();
    drawSyntheticDemoLotOnCalibCanvas();
    renderCalibCanvas();
  });

  // Curved Row controls
  document.getElementById("curveSpotCount").addEventListener("input", () => {
    computeCurvedRowPreviewJS();
    updateCurvedHintUI();
    renderCalibCanvas();
  });
  document.getElementById("btnCurveNextPhase").addEventListener("click", () => {
    if (state.calib.curvePhase === "front") {
      if (state.calib.curveFrontPts.length < 2) return;
      state.calib.curvePhase = "back";
    } else {
      state.calib.curvePhase = "front";
      state.calib.curveFrontPts = [];
      state.calib.curveBackPts = [];
      state.calib.previewTrapezoids = [];
    }
    updateCurvedHintUI();
    renderCalibCanvas();
  });
  document.getElementById("btnCommitCurveRow").addEventListener("click", () => {
    if (!state.calib.previewTrapezoids.length) return;
    pushCalibUndoSnapshot();
    if (state.calib.layerMode === "vehicle" && state.calib.spots.length > 0) {
      // Strictly write into vehicle_polygon of existing spots without touching ground polygon!
      state.calib.previewTrapezoids.forEach((poly, i) => {
        if (i < state.calib.spots.length) {
          state.calib.spots[i].vehicle_polygon = poly;
          state.calib.spots[i].vehicle_poly_custom = 1;
        } else {
          state.calib.spots.push({
            label: String(state.calib.spots.length + 1).padStart(2, "0"),
            spot_type: "standard",
            compact_only: false,
            total_meters: 5.2,
            capacity: 1,
            status: "OCCUPIED",
            polygon: poly,
            vehicle_polygon: poly,
            vehicle_poly_custom: 1,
          });
        }
      });
    } else {
      const baseIdx = state.calib.spots.length + 1;
      state.calib.previewTrapezoids.forEach((poly, i) => {
        state.calib.spots.push({
          label: String(baseIdx + i).padStart(2, "0"),
          spot_type: "standard",
          compact_only: false,
          total_meters: 5.2,
          capacity: 1,
          status: "FREE",
          polygon: poly,
          vehicle_polygon: deriveVehicleEnvelopeJS(poly),
          vehicle_poly_custom: 0,
        });
      });
    }
    state.calib.selectedSpotIdx = Math.min(
      state.calib.spots.length - 1,
      Math.max(0, state.calib.previewTrapezoids.length - 1)
    );
    setCalibMode("smart_edit");
    renderSpotListSidebar();
  });
  document.getElementById("btnCancelCurve").addEventListener("click", () => {
    state.calib.curveFrontPts = [];
    state.calib.curveBackPts = [];
    state.calib.curvePhase = "front";
    state.calib.previewTrapezoids = [];
    updateCurvedHintUI();
    renderCalibCanvas();
  });

  // Straight 4-Point Perspective Row controls
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
          gap_ratio: 0.0,
        }),
      });
      state.calib.previewTrapezoids = res.trapezoids;
      updateCalibButtons();
      renderCalibCanvas();
    }
  };
  document.getElementById("rowSpotCount").addEventListener("input", recomputeRow);
  document.getElementById("rowDirection").addEventListener("change", recomputeRow);
  document.getElementById("rowGamma").addEventListener("input", recomputeRow);

  document.getElementById("btnCommitRow").addEventListener("click", () => {
    if (!state.calib.previewTrapezoids.length) return;
    pushCalibUndoSnapshot();
    if (state.calib.layerMode === "vehicle" && state.calib.spots.length > 0) {
      state.calib.previewTrapezoids.forEach((poly, i) => {
        if (i < state.calib.spots.length) {
          state.calib.spots[i].vehicle_polygon = poly;
          state.calib.spots[i].vehicle_poly_custom = 1;
        } else {
          state.calib.spots.push({
            label: String(state.calib.spots.length + 1).padStart(2, "0"),
            spot_type: "standard",
            compact_only: false,
            total_meters: 5.2,
            capacity: 1,
            status: "OCCUPIED",
            polygon: poly,
            vehicle_polygon: poly,
            vehicle_poly_custom: 1,
          });
        }
      });
    } else {
      const baseIdx = state.calib.spots.length + 1;
      state.calib.previewTrapezoids.forEach((poly, i) => {
        state.calib.spots.push({
          label: String(baseIdx + i).padStart(2, "0"),
          spot_type: "standard",
          compact_only: false,
          total_meters: 5.2,
          capacity: 1,
          status: "FREE",
          polygon: poly,
          vehicle_polygon: deriveVehicleEnvelopeJS(poly),
          vehicle_poly_custom: 0,
        });
      });
    }
    state.calib.selectedSpotIdx = Math.min(
      state.calib.spots.length - 1,
      Math.max(0, state.calib.previewTrapezoids.length - 1)
    );
    setCalibMode("smart_edit");
    renderSpotListSidebar();
  });

  document.getElementById("btnCancelDraft").addEventListener("click", () => {
    state.calib.draftPoints = [];
    state.calib.previewTrapezoids = [];
    updateCalibButtons();
    renderCalibCanvas();
  });

  document.getElementById("btnFinishIrregular").addEventListener("click", () => {
    if (state.calib.draftPoints.length < 3) return;
    pushCalibUndoSnapshot();
    if (
      state.calib.layerMode === "vehicle" &&
      state.calib.selectedSpotIdx >= 0 &&
      state.calib.spots[state.calib.selectedSpotIdx]
    ) {
      const targetSp = state.calib.spots[state.calib.selectedSpotIdx];
      targetSp.vehicle_polygon = [...state.calib.draftPoints];
      targetSp.vehicle_poly_custom = 1;
    } else {
      const compact = document.getElementById("irrCompactOnly").checked;
      const idx = state.calib.spots.length + 1;
      state.calib.spots.push({
        label: compact ? `小车-${String(idx).padStart(2, "0")}` : String(idx).padStart(2, "0"),
        spot_type: "irregular",
        compact_only: compact,
        total_meters: compact ? 4.3 : 5.2,
        capacity: 1,
        status: "FREE",
        polygon: [...state.calib.draftPoints],
      });
      state.calib.selectedSpotIdx = state.calib.spots.length - 1;
    }
    setCalibMode("smart_edit");
    renderSpotListSidebar();
  });

  document.getElementById("btnCancelIrregular").addEventListener("click", () => {
    state.calib.draftPoints = [];
    updateCalibButtons();
    renderCalibCanvas();
  });

  document.getElementById("btnFinishElastic").addEventListener("click", () => {
    if (state.calib.draftPoints.length < 4) return;
    pushCalibUndoSnapshot();
    const meters = Number(document.getElementById("stripMeters").value || 18.0);
    const cap = Math.max(1, Math.floor(meters / 5.2));
    state.calib.spots.push({
      label: `${meters}m弹性顺停带`,
      spot_type: "elastic_strip",
      compact_only: false,
      total_meters: meters,
      capacity: cap,
      status: "FREE",
      polygon: state.calib.draftPoints.slice(0, 4),
    });
    state.calib.selectedSpotIdx = state.calib.spots.length - 1;
    setCalibMode("smart_edit");
    renderSpotListSidebar();
  });

  document.getElementById("btnCancelElastic").addEventListener("click", () => {
    state.calib.draftPoints = [];
    updateCalibButtons();
    renderCalibCanvas();
  });

  const getCanvasCoords = (evt) => {
    const rect = canvas.getBoundingClientRect();
    return [
      Math.max(0, Math.min(canvas.width - 1, Math.round((evt.clientX - rect.left) * (canvas.width / rect.width)))),
      Math.max(0, Math.min(canvas.height - 1, Math.round((evt.clientY - rect.top) * (canvas.height / rect.height)))),
    ];
  };

  canvas.addEventListener("dblclick", (evt) => {
    if (state.calib.mode === "curved_row" && state.calib.curvePhase === "front" && state.calib.curveFrontPts.length >= 2) {
      evt.preventDefault();
      state.calib.curvePhase = "back";
      updateCurvedHintUI();
      renderCalibCanvas();
    } else if (state.calib.mode === "irregular_poly" && state.calib.draftPoints.length >= 3) {
      evt.preventDefault();
      document.getElementById("btnFinishIrregular")?.click();
    }
  });

  canvas.addEventListener("mousedown", async (evt) => {
    // Space + Drag OR middle-click pans the zoomed canvas viewport
    if (state.calib.spacePressed || evt.button === 1) {
      evt.preventDefault();
      state.calib.panDrag = {
        startX: evt.clientX,
        startY: evt.clientY,
        scrollLeft: scrollStage.scrollLeft,
        scrollTop: scrollStage.scrollTop,
      };
      canvas.style.cursor = "grabbing";
      return;
    }

    const [mx, my] = getCanvasCoords(evt);
    const hitRadius = Math.max(12, (canvas.width * 0.0115) / Math.sqrt(state.calib.zoom));

    // Mode 0: Fixed Camera ROI Crop Editor (`crop_roi`)
    if (state.calib.mode === "crop_roi") {
      const cropHit = hitTestCropRoi(mx, my, canvas);
      const d = state.calib.cropDraft || { x: 0, y: 0.25, w: 1, h: 0.5 };
      if (cropHit && cropHit.type === "crop_handle") {
        state.calib.draggingHandle = {
          type: "crop_handle",
          handle: cropHit.handle,
          origDraft: { ...d },
          startX: mx / Math.max(1, canvas.width),
          startY: my / Math.max(1, canvas.height),
        };
      } else if (cropHit && cropHit.type === "crop_move") {
        state.calib.draggingHandle = {
          type: "crop_move",
          origDraft: { ...d },
          startX: mx / Math.max(1, canvas.width),
          startY: my / Math.max(1, canvas.height),
        };
      } else {
        const nx = mx / Math.max(1, canvas.width);
        const ny = my / Math.max(1, canvas.height);
        state.calib.draggingHandle = {
          type: "crop_new",
          startX: nx,
          startY: ny,
        };
      }
      renderCalibCanvas();
      return;
    }

    // Mode 0b: Fixed Ground Positioning Anchors (`anchor_points`)
    if (state.calib.mode === "anchor_points") {
      if (!Array.isArray(state.calib.anchorPoints) || !state.calib.anchorPoints.length) {
        state.calib.anchorPoints = deriveDefaultAnchorsJS(state.calib.spots, state.calib.imgW, state.calib.imgH);
      }
      const anchorRadius = Math.max(16, hitRadius * 1.35);
      let hitIdx = -1;
      let bestDist = anchorRadius;
      state.calib.anchorPoints.forEach((ap, idx) => {
        const d = Math.hypot(mx - Number(ap.x || 0), my - Number(ap.y || 0));
        if (d <= bestDist) {
          bestDist = d;
          hitIdx = idx;
        }
      });
      if (hitIdx >= 0) {
        state.calib.draggingHandle = {
          type: "anchor_point",
          anchorIdx: hitIdx,
        };
      } else if (state.calib.anchorPoints.length < 6) {
        const nextNum = state.calib.anchorPoints.length + 1;
        state.calib.anchorPoints.push({
          id: `A${nextNum}`,
          label: `固定锚点${nextNum}`,
          x: +mx.toFixed(1),
          y: +my.toFixed(1),
          auto: false,
        });
        state.calib.draggingHandle = {
          type: "anchor_point",
          anchorIdx: state.calib.anchorPoints.length - 1,
        };
        updateAnchorPointsReadoutUI();
      }
      renderCalibCanvas();
      return;
    }

    // Mode 1: Smart Direct Manipulation (`smart_edit`)
    if (state.calib.mode === "smart_edit") {
      const hit = hitTestSmartEdit(mx, my, canvas);
      if (!hit) {
        state.calib.selectedSpotIdx = -1;
        syncSelectedSpotInspector();
        renderSpotListSidebar();
        renderCalibCanvas();
        return;
      }

      state.calib.selectedSpotIdx = hit.spotIdx;
      syncSelectedSpotInspector();
      renderSpotListSidebar();

      const allowMag = state.calib.magneticWeld && !evt.altKey;
      if (hit.type === "roof_height") {
        pushCalibUndoSnapshot();
        const vpoly = ensureSpotVehiclePolygon(state.calib.spots[hit.spotIdx]);
        state.calib.draggingHandle = {
          type: "smart_roof_height",
          spotIdx: hit.spotIdx,
          pA: hit.roofH.pA,
          pB: hit.roofH.pB,
          origA: [...vpoly[hit.roofH.pA]],
          origB: [...vpoly[hit.roofH.pB]],
          startX: mx,
          startY: my,
        };
      } else if (hit.type === "vertex") {
        pushCalibUndoSnapshot();
        const anchor = getEditableSpotPoly(state.calib.spots[hit.spotIdx])[hit.ptIdx];
        const linked = collectLinkedVertices(anchor, hit.spotIdx, hit.ptIdx, allowMag);
        state.calib.draggingHandle = {
          type: "smart_vertex",
          linked: linked.map((lv) => ({
            ...lv,
            orig: [...getEditableSpotPoly(state.calib.spots[lv.spotIdx])[lv.ptIdx]],
          })),
          startX: mx,
          startY: my,
        };
      } else if (hit.type === "edge") {
        pushCalibUndoSnapshot();
        const poly = getEditableSpotPoly(state.calib.spots[hit.spotIdx]);
        const setMap = new Map();
        const addLinked = (list) =>
          list.forEach((lv) => setMap.set(`${lv.spotIdx}:${lv.ptIdx}`, lv));
        addLinked(collectLinkedVertices(poly[hit.pA], hit.spotIdx, hit.pA, allowMag));
        addLinked(collectLinkedVertices(poly[hit.pB], hit.spotIdx, hit.pB, allowMag));
        state.calib.draggingHandle = {
          type: "smart_edge",
          linked: Array.from(setMap.values()).map((lv) => ({
            ...lv,
            orig: [...getEditableSpotPoly(state.calib.spots[lv.spotIdx])[lv.ptIdx]],
          })),
          startX: mx,
          startY: my,
        };
      } else if (hit.type === "rotate") {
        pushCalibUndoSnapshot();
        const origPoly = getEditableSpotPoly(state.calib.spots[hit.spotIdx]).map((p) => [...p]);
        const [cx, cy] = getPolyCentroid(origPoly);
        state.calib.draggingHandle = {
          type: "smart_rotate",
          spotIdx: hit.spotIdx,
          cx,
          cy,
          startAngle: Math.atan2(my - cy, mx - cx),
          origPoly,
        };
      } else if (hit.type === "body") {
        const spObj = state.calib.spots[hit.spotIdx];
        state.calib.draggingHandle = {
          type: "smart_body",
          spotIdx: hit.spotIdx,
          startX: mx,
          startY: my,
          moved: false,
          origPoly: getEditableSpotPoly(spObj).map((p) => [...p]),
          origVehPoly: ensureSpotVehiclePolygon(spObj).map((p) => [...p]),
        };
      }
      renderCalibCanvas();
      return;
    }

    // Mode 2: Curved / Fan-Shaped Row (`curved_row`)
    if (state.calib.mode === "curved_row") {
      for (let i = 0; i < state.calib.curveFrontPts.length; i++) {
        const [px, py] = state.calib.curveFrontPts[i];
        if (Math.hypot(mx - px, my - py) <= hitRadius) {
          state.calib.draggingHandle = { type: "curve_front", ptIdx: i };
          return;
        }
      }
      for (let i = 0; i < state.calib.curveBackPts.length; i++) {
        const [px, py] = state.calib.curveBackPts[i];
        if (Math.hypot(mx - px, my - py) <= hitRadius) {
          state.calib.draggingHandle = { type: "curve_back", ptIdx: i };
          return;
        }
      }
      if (state.calib.curvePhase === "front") {
        state.calib.curveFrontPts.push([mx, my]);
      } else {
        state.calib.curveBackPts.push([mx, my]);
        computeCurvedRowPreviewJS();
      }
      updateCurvedHintUI();
      renderCalibCanvas();
      return;
    }

    // Modes 3-5: Draft points
    for (let i = 0; i < state.calib.draftPoints.length; i++) {
      const [px, py] = state.calib.draftPoints[i];
      if (Math.hypot(mx - px, my - py) <= hitRadius) {
        state.calib.draggingHandle = { type: "draft", ptIdx: i };
        return;
      }
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
    if (state.calib.panDrag) {
      scrollStage.scrollLeft = state.calib.panDrag.scrollLeft - (evt.clientX - state.calib.panDrag.startX);
      scrollStage.scrollTop = state.calib.panDrag.scrollTop - (evt.clientY - state.calib.panDrag.startY);
      return;
    }

    const [mx, my] = getCanvasCoords(evt);
    const h = state.calib.draggingHandle;

    if (!h) {
      if (state.calib.spacePressed) {
        canvas.style.cursor = "grab";
        return;
      }
      if (state.calib.mode === "crop_roi") {
        const cHit = hitTestCropRoi(mx, my, canvas);
        canvas.style.cursor = cHit ? cHit.cursor : "crosshair";
        return;
      }
      if (state.calib.mode === "anchor_points") {
        const pts = Array.isArray(state.calib.anchorPoints) ? state.calib.anchorPoints : [];
        const near = pts.some((ap) => Math.hypot(mx - Number(ap.x || 0), my - Number(ap.y || 0)) <= 18);
        canvas.style.cursor = near ? "grab" : pts.length < 6 ? "crosshair" : "default";
        return;
      }
      if (state.calib.mode === "smart_edit") {
        const hit = hitTestSmartEdit(mx, my, canvas);
        state.calib.hoveredTarget = hit;
        if (!hit) canvas.style.cursor = "default";
        else if (hit.type === "roof_height") canvas.style.cursor = "ns-resize";
        else if (hit.type === "vertex") canvas.style.cursor = "crosshair";
        else if (hit.type === "edge") canvas.style.cursor = hit.isHorizontal ? "ns-resize" : "ew-resize";
        else if (hit.type === "rotate") canvas.style.cursor = "grab";
        else canvas.style.cursor = "move";
        renderCalibCanvas();
      } else {
        canvas.style.cursor = "crosshair";
      }
      return;
    }

    if (h.type === "crop_handle" || h.type === "crop_move" || h.type === "crop_new") {
      const nx = Math.max(0, Math.min(1, mx / Math.max(1, canvas.width)));
      const ny = Math.max(0, Math.min(1, my / Math.max(1, canvas.height)));
      const minW = 0.16;
      const minH = 0.12;
      if (h.type === "crop_move") {
        const dx = nx - h.startX;
        const dy = ny - h.startY;
        const w = h.origDraft.w;
        const hh = h.origDraft.h;
        state.calib.cropDraft = {
          x: Math.max(0, Math.min(1 - w, h.origDraft.x + dx)),
          y: Math.max(0, Math.min(1 - hh, h.origDraft.y + dy)),
          w,
          h: hh,
        };
      } else if (h.type === "crop_new") {
        const x0 = Math.min(h.startX, nx);
        const y0 = Math.min(h.startY, ny);
        const w = Math.max(minW, Math.min(1 - x0, Math.abs(nx - h.startX)));
        const hh = Math.max(minH, Math.min(1 - y0, Math.abs(ny - h.startY)));
        state.calib.cropDraft = { x: x0, y: y0, w, h: hh };
      } else {
        let x0 = h.origDraft.x;
        let y0 = h.origDraft.y;
        let x1 = h.origDraft.x + h.origDraft.w;
        let y1 = h.origDraft.y + h.origDraft.h;
        const dx = nx - h.startX;
        const dy = ny - h.startY;
        if (h.handle.includes("w")) x0 = Math.max(0, Math.min(x1 - minW, x0 + dx));
        if (h.handle.includes("e")) x1 = Math.min(1, Math.max(x0 + minW, x1 + dx));
        if (h.handle.includes("n")) y0 = Math.max(0, Math.min(y1 - minH, y0 + dy));
        if (h.handle.includes("s")) y1 = Math.min(1, Math.max(y0 + minH, y1 + dy));
        state.calib.cropDraft = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
      }
      updateCropDraftReadoutUI();
      renderCalibCanvas();
      return;
    }

    if (h.type === "anchor_point") {
      const ap = state.calib.anchorPoints?.[h.anchorIdx];
      if (ap) {
        ap.x = +Math.max(2, Math.min(canvas.width - 2, mx)).toFixed(1);
        ap.y = +Math.max(2, Math.min(canvas.height - 2, my)).toFixed(1);
        ap.auto = false;
        canvas.style.cursor = "grabbing";
        updateAnchorPointsReadoutUI();
        renderCalibCanvas();
      }
      return;
    }

    const clampPt = (x, y) => [
      Math.max(0, Math.min(canvas.width - 1, Math.round(x))),
      Math.max(0, Math.min(canvas.height - 1, Math.round(y))),
    ];

    if (h.type === "smart_roof_height") {
      const dx = mx - h.startX;
      const dy = my - h.startY;
      const spObj = state.calib.spots[h.spotIdx];
      const vpoly = ensureSpotVehiclePolygon(spObj);
      vpoly[h.pA] = clampPt(h.origA[0] + dx, h.origA[1] + dy);
      vpoly[h.pB] = clampPt(h.origB[0] + dx, h.origB[1] + dy);
      spObj.vehicle_poly_custom = 1;
      syncSelectedSpotInspector();
    } else if (h.type === "smart_vertex" || h.type === "smart_edge") {
      const dx = mx - h.startX;
      const dy = my - h.startY;
      const touched = new Set();
      h.linked.forEach((lv) => {
        const poly = getEditableSpotPoly(state.calib.spots[lv.spotIdx]);
        poly[lv.ptIdx] = clampPt(lv.orig[0] + dx, lv.orig[1] + dy);
        touched.add(lv.spotIdx);
      });
      touched.forEach((sIdx) => {
        const spObj = state.calib.spots[sIdx];
        if (state.calib.layerMode === "vehicle") {
          spObj.vehicle_poly_custom = 1;
        } else if (state.calib.layerMode === "dual" && !spObj.vehicle_poly_custom) {
          spObj.vehicle_polygon = deriveVehicleEnvelopeJS(
            spObj.polygon,
            state.calib.imgW,
            state.calib.imgH,
            state.calib.vehHeightScale,
            state.calib.vehSlantBias
          );
        }
      });
      if (state.calib.layerMode === "vehicle" || state.calib.layerMode === "dual") {
        syncSelectedSpotInspector();
      }
    } else if (h.type === "smart_rotate") {
      const dTheta = Math.atan2(my - h.cy, mx - h.cx) - h.startAngle;
      const cosT = Math.cos(dTheta);
      const sinT = Math.sin(dTheta);
      const rotated = h.origPoly.map(([ox, oy]) => {
        const rx = ox - h.cx;
        const ry = oy - h.cy;
        return clampPt(h.cx + rx * cosT - ry * sinT, h.cy + rx * sinT + ry * cosT);
      });
      const spRot = state.calib.spots[h.spotIdx];
      setEditableSpotPoly(spRot, rotated);
      if (state.calib.layerMode === "dual" && spRot && !spRot.vehicle_poly_custom) {
        spRot.vehicle_polygon = deriveVehicleEnvelopeJS(
          spRot.polygon,
          state.calib.imgW,
          state.calib.imgH,
          state.calib.vehHeightScale,
          state.calib.vehSlantBias
        );
      }
    } else if (h.type === "smart_body") {
      const dx = mx - h.startX;
      const dy = my - h.startY;
      if (!h.moved && Math.hypot(dx, dy) >= 2) {
        pushCalibUndoSnapshot();
        h.moved = true;
      }
      if (h.moved) {
        const spObj = state.calib.spots[h.spotIdx];
        const movedPoly = h.origPoly.map(([ox, oy]) => clampPt(ox + dx, oy + dy));
        if (state.calib.layerMode === "vehicle") {
          spObj.vehicle_polygon = movedPoly;
          spObj.vehicle_poly_custom = 1;
        } else if (state.calib.layerMode === "ground") {
          // Strictly decoupled: moving ground box in Ground Workspace NEVER shifts vehicle_polygon
          spObj.polygon = movedPoly;
        } else {
          // In combined "dual" preview mode, move both together
          spObj.polygon = movedPoly;
          if (h.origVehPoly) {
            spObj.vehicle_polygon = h.origVehPoly.map(([ox, oy]) => clampPt(ox + dx, oy + dy));
          }
        }
      }
    } else if (h.type === "curve_front") {
      state.calib.curveFrontPts[h.ptIdx] = [mx, my];
      computeCurvedRowPreviewJS();
    } else if (h.type === "curve_back") {
      state.calib.curveBackPts[h.ptIdx] = [mx, my];
      computeCurvedRowPreviewJS();
    } else if (h.type === "draft") {
      state.calib.draftPoints[h.ptIdx] = [mx, my];
      if (state.calib.mode === "perspective_row" && state.calib.draftPoints.length === 4) {
        await recomputeRow();
        return;
      }
    }
    renderCalibCanvas();
  });

  window.addEventListener("mouseup", () => {
    state.calib.draggingHandle = null;
    if (state.calib.panDrag) {
      state.calib.panDrag = null;
      canvas.style.cursor = state.calib.spacePressed ? "grab" : "default";
    }
  });

  document.getElementById("btnClearAllSpots").addEventListener("click", () => {
    if (state.calib.spots.length) pushCalibUndoSnapshot();
    if (state.calib.layerMode === "vehicle" && state.calib.spots.length > 0) {
      // In Vehicle Workspace, reset only vehicle_polygon without deleting ground spots!
      state.calib.spots.forEach((sp) => {
        sp.vehicle_polygon = deriveVehicleEnvelopeJS(sp.polygon);
        sp.vehicle_poly_custom = 0;
      });
    } else {
      state.calib.spots = [];
      state.calib.selectedSpotIdx = -1;
    }
    state.calib.draftPoints = [];
    state.calib.curveFrontPts = [];
    state.calib.curveBackPts = [];
    state.calib.previewTrapezoids = [];
    syncSelectedSpotInspector();
    updateCalibButtons();
    renderSpotListSidebar();
    renderCalibCanvas();
  });

  document.getElementById("btnSaveCalibration").addEventListener("click", async () => {
    const camId = document.getElementById("calibCameraSelect").value;
    const zoneId = document.getElementById("calibZoneSelect").value;
    const mode = state.calib.layerMode || "dual";
    const calibration_layer =
      mode === "ground" ? "ground" : mode === "vehicle" ? "vehicle" : "both";
    if (mode !== "ground") {
      state.calib.spots.forEach((sp) => ensureSpotVehiclePolygon(sp));
    }
    const groundImgPayload =
      calibration_layer === "vehicle" || state.calib.groundBgIsServerPreview
        ? undefined
        : state.calib.fullFrameDataUrl || state.calib.groundBgDataUrl || undefined;
    const res = await api(`/api/cameras/${encodeURIComponent(camId)}/spots`, {
      method: "POST",
      body: JSON.stringify({
        zone_id: zoneId,
        image_width: state.calib.imgW,
        image_height: state.calib.imgH,
        spots: state.calib.spots,
        calibration_layer,
        image_base64: groundImgPayload,
        evaluate_current: true,
      }),
    });
    if (res.auto_cropped || groundImgPayload) {
      await loadCameraSpotsForCalib(camId);
    } else {
      state.calib.spots = (res.spots || []).map((sp) => {
        ensureSpotVehiclePolygon(sp);
        return sp;
      });
      markCalibDirty(false);
      syncSelectedSpotInspector();
      renderSpotListSidebar();
      renderCalibCanvas();
    }
    const saveMsg = document.getElementById("calibSaveMsg");
    saveMsg.style.color = "#1D9E31";
    const layerLabel =
      calibration_layer === "ground"
        ? "无车地面标定层（未改动车辆层）"
        : calibration_layer === "vehicle"
        ? "车辆停入标定层（未改动地面层）"
        : "双层完整空间标定（地面框 + 3D 车体层）";
    const cropNote = res.auto_cropped ? " · 已智能裁剪车位带" : "";
    saveMsg.textContent = `✅ 已保存 ${res.spots.length} 个车位的【${layerLabel}】至 ${camId}${cropNote}，态势图已同步更新`;
    await refreshOverview();
    renderCameraMetaSummaryCard();
  });
}

async function runDualLayerBenchmarkUI() {
  const camId = document.getElementById("calibCameraSelect").value;
  const btn = document.getElementById("btnBenchmarkDualLayer");
  const panel = document.getElementById("dualBenchPanel");
  const kpisEl = document.getElementById("dualBenchKpis");
  const tbodyEl = document.getElementById("dualBenchTbody");
  const titleEl = document.getElementById("dualBenchTitle");
  const saveMsg = document.getElementById("calibSaveMsg");
  if (!state.calib.spots.length) {
    alert("当前机位尚无标定车位，请先完成标定后再执行实测对比。");
    return;
  }

  state.calib.spots.forEach((sp) => ensureSpotVehiclePolygon(sp));
  if (btn) {
    btn.disabled = true;
    btn.textContent = "⚡ 正在实测对比中...";
  }
  try {
    const res = await api(`/api/cameras/${encodeURIComponent(camId)}/benchmark-dual-layer`, {
      method: "POST",
      body: JSON.stringify({
        image_base64: state.calib.bgDataUrl || undefined,
        image_width: state.calib.imgW,
        image_height: state.calib.imgH,
        spots: state.calib.spots,
        sync_db: !state.calib.dirty,
        repeats: 8,
      }),
    });

    const c = res.compute || {};
    const s = res.summary || {};
    titleEl.textContent = `⚡ ${camId} 双层空间标定实测对比（分辨率 ${res.image_resolution?.[0]}×${res.image_resolution?.[1]} · ${res.spot_count} 个车位 · 连续 ${res.repeats} 轮均值）`;

    const vlmSaved = (c.single_vlm_escalations || 0) - (c.dual_vlm_escalations || 0);
    const vlmSub =
      vlmSaved > 0
        ? `单层需唤醒云端 VLM ${c.single_vlm_escalations} 次 → 双层降至 ${c.dual_vlm_escalations} 次（反省毫秒/Token）`
        : `单层 ${c.single_vlm_escalations || 0} 次 / 双层 ${c.dual_vlm_escalations || 0} 次云端 VLM 复核`;

    kpisEl.innerHTML = `
      <div class="bench-kpi-card">
        <div class="kpi-label">识别精准度对比（当前实景）</div>
        <div class="kpi-val good">已停 ${s.dual_occupied} / 空闲 ${s.dual_free}</div>
        <div class="kpi-sub">仅地面单层：已停 ${s.single_occupied} / 空闲 ${s.single_free}${
      s.changed_spots > 0 ? `（修正 <strong>${s.changed_spots}</strong> 个斜拍视角偏差车位）` : "（空场/正拍零误报一致）"
    }</div>
      </div>
      <div class="bench-kpi-card">
        <div class="kpi-label">单帧 CPU 算力耗时（全车位总计）</div>
        <div class="kpi-val accent">${c.dual_layer_total_ms} ms <small style="font-size:12px;font-weight:500">/ 帧</small></div>
        <div class="kpi-sub">单层 ${c.single_layer_total_ms} ms → 双层仅增 <strong>+${c.extra_ms_total} ms</strong>（单车位 +${c.extra_ms_per_spot} ms）</div>
      </div>
      <div class="bench-kpi-card">
        <div class="kpi-label">静止帧门控 & 云端大模型唤醒</div>
        <div class="kpi-val">${c.static_framediff_total_ms} ms</div>
        <div class="kpi-sub">99% 静止帧仅跑 32×32 复合差分；${vlmSub}</div>
      </div>
      <div class="bench-kpi-card">
        <div class="kpi-label">常驻内存 & 瞬时裁剪开销</div>
        <div class="kpi-val good">${c.persistent_sig_kb_per_spot} KB <small style="font-size:12px;font-weight:500">/ 车位</small></div>
        <div class="kpi-sub">常驻签名<strong>零额外内存</strong>；瞬时裁剪缓冲 ${c.single_transient_patch_kb} → ${c.dual_transient_patch_kb} KB（用完即毁）</div>
      </div>
    `;

    const stChip = (st) => {
      const map = { FREE: "空闲", OCCUPIED: "已停", BLOCKED: "占道" };
      return `<span class="spot-chip chip-${st}">${map[st] || st}</span>`;
    };

    tbodyEl.innerHTML = (res.spots || [])
      .map(
        (r) => `
        <tr class="${r.changed ? "row-diff" : ""}">
          <td><strong>${escapeHtml(r.label)}</strong>${r.changed ? ' <span class="tpl-pill tpl-pill-ready" style="padding:1px 6px;font-size:10px">双层修正</span>' : ""}</td>
          <td>${stChip(r.single_status)} <small style="color:var(--text-secondary)">(${Math.round(r.single_confidence * 100)}%)</small></td>
          <td><strong>${r.single_score}</strong>${r.single_needs_vlm ? ' <small style="color:#D97706">· 需VLM</small>' : ""}</td>
          <td>${stChip(r.dual_status)} <small style="color:var(--text-secondary)">(${Math.round(r.dual_confidence * 100)}%)</small></td>
          <td><code>${r.ground_score}</code> / <strong style="color:#0284C7">${r.veh_3d_score}</strong> → <strong>${r.fused_score}</strong></td>
          <td style="color:var(--text-secondary)">${escapeHtml(r.reason || "")}</td>
        </tr>
      `
      )
      .join("");

    panel.classList.remove("hidden");

    // Update local spot preview statuses from Dual-Layer result
    const byLabel = {};
    (res.spots || []).forEach((r) => {
      byLabel[r.label] = r;
    });
    state.calib.spots.forEach((sp) => {
      const r = byLabel[sp.label];
      if (r) {
        sp.status = r.dual_status;
        sp.effective_status = r.dual_status;
        sp.confidence = r.dual_confidence;
        sp.last_reason = r.reason;
      }
    });
    renderSpotListSidebar();
    renderCalibCanvas();
    if (!state.calib.dirty) {
      await refreshOverview();
    }
    if (saveMsg) {
      saveMsg.style.color = "#1D9E31";
      saveMsg.textContent = `⚡ 双层实测完成：每车位仅增 +${c.extra_ms_per_spot}ms，已停 ${s.dual_occupied} / 空闲 ${s.dual_free}`;
    }
  } catch (err) {
    alert(`双层实测对比失败：${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "⚡ 实测单层 vs 双层精准度与算力";
    }
  }
}

async function runAutoCalibrationWithDataUrl(cameraId, zoneId, dataUrl, fromTestLab = false) {
  const overlay = document.getElementById("calibLoadingOverlay");
  const saveMsg = document.getElementById("calibSaveMsg");
  const btnUpload = document.getElementById("btnUploadAutoCalibrate");
  const btnRerun = document.getElementById("btnRerunAutoCalibrate");
  const btnLabCal = document.getElementById("btnLabAutoCalibrate");
  const canvas = document.getElementById("calibCanvas");

  // Preview raw uploaded reference photo cleanly while AI calibrates — hide old cropped-space polygons
  // so stale cropped coordinates are never rendered high up in raw portrait building windows!
  await new Promise((resolve) => {
    const preImg = new Image();
    preImg.onload = () => {
      if (state.calib.cameraId === cameraId) {
        state.calib.calibratingPreview = true;
        state.calib.fullFrameImg = preImg;
        state.calib.fullFrameDataUrl = dataUrl;
        state.calib.fullFrameW = preImg.naturalWidth;
        state.calib.fullFrameH = preImg.naturalHeight;
        state.calib.groundBgImage = preImg;
        state.calib.groundBgDataUrl = dataUrl;
        state.calib.groundBgIsServerPreview = false;
        syncCalibActiveBgImage();
        state.calib.imgW = preImg.naturalWidth;
        state.calib.imgH = preImg.naturalHeight;
        canvas.width = preImg.naturalWidth;
        canvas.height = preImg.naturalHeight;
        renderCalibCanvas();
      }
      resolve();
    };
    preImg.onerror = () => resolve();
    preImg.src = dataUrl;
  });

  overlay.classList.remove("hidden");
  btnUpload.disabled = true;
  btnRerun.disabled = true;
  if (btnLabCal) {
    btnLabCal.disabled = true;
    btnLabCal.textContent = "正在 AI 自动标定建档...";
  }
  if (fromTestLab) {
    setLabBusy(true);
    setLabStatusBanner(
      "is-loading",
      "⏳ 正在通过多模态模型与 OpenCV 标线引擎自动标定参考模板并对齐车位框..."
    );
  }
  saveMsg.style.color = "#0071E3";
  saveMsg.textContent = "正在通过多模态模型与 OpenCV 标线引擎自动标定参考模板...";

  try {
    const res = await api(`/api/cameras/${encodeURIComponent(cameraId)}/auto-calibrate`, {
      method: "POST",
      body: JSON.stringify({
        image_base64: dataUrl,
        zone_id: zoneId,
        auto_save: true,
      }),
    });

    const calCamSel = document.getElementById("calibCameraSelect");
    state.calib.cameraId = cameraId;
    state.calib.layerMode = "dual";
    state.calib.calibratingPreview = false;
    if (calCamSel) {
      calCamSel.value = cameraId;
    }
    await loadCameraSpotsForCalib(cameraId);
    try {
      await refreshOverview();
      if (calCamSel) {
        calCamSel.value = cameraId;
      }
    } catch (ovErr) {
      console.error("Post-calibration overview refresh warning:", ovErr);
    }
    const cropSummary = res.crop_roi?.enabled
      ? ` · 已智能裁剪至 ${res.image_width}×${res.image_height}`
      : "";
    const evDetectedCnt = (res.spots || []).filter(
      (s) => s.is_ev || (Array.isArray(s.tags) && s.tags.includes("ev_charging"))
    ).length;
    const evSummary = evDetectedCnt > 0 ? ` · ⚡自动识别 ${evDetectedCnt} 个新能源车位` : "";
    if (res.vlm_fallback_error) {
      saveMsg.style.color = "#B45309";
      saveMsg.textContent = `⚠️ 多模态接口不可用（${res.vlm_fallback_error}），已回退本地 OpenCV 几何识别 ${res.spots.length} 个车位${evSummary}（耗时 ${(
        res.elapsed_ms / 1000
      ).toFixed(1)}s）。请检查顶部模型 Endpoint 配置后重新标定以获得最佳异形贴合度。`;
    } else {
      saveMsg.style.color = "#1D9E31";
      saveMsg.textContent = `✅ 自动标定完成（含 3D 空间车体层${cropSummary}${evSummary}）：识别 ${res.spots.length} 个车位（耗时 ${(
        res.elapsed_ms / 1000
      ).toFixed(1)}s）`;
    }

    if (fromTestLab) {
      const syntheticSnapRes = {
        elapsed_ms: res.elapsed_ms,
        instant_confirm_mode: true,
        debounce_seconds: 0,
        effective_crop_roi: { ...(res.crop_roi || {}), enabled: false, already_cropped: true },
        template_alignment: {
          active: true,
          aligned: false,
          shift_px: 0,
          just_calibrated: true,
          anchor_matches: (res.anchor_points || []).map((a) => ({
            ...a,
            dx: 0,
            dy: 0,
            corr: 1.0,
            matched: true,
          })),
        },
        reports: res.spots.map((sp) => ({
          spot_id: sp.spot_id,
          label: sp.label,
          spot_type: sp.spot_type,
          effective_polygon: sp.polygon,
          effective_vehicle_polygon: sp.vehicle_polygon,
          diff_score: 1.0,
          stage_used: res.engine_used,
          observed_status: sp.status,
          confidence: sp.confidence || 0.95,
          reason: sp.last_reason || "参考图自动标定基准",
          fsm: {
            committed_status: sp.status,
            pending_status: null,
            note: "已存为机位永久参考模板并同步初始车位状态",
          },
        })),
      };
      renderLabOverlayAndReport(
        res.template_jpeg_b64 || dataUrl,
        {
          camera: {
            image_width: res.image_width,
            image_height: res.image_height,
            crop_roi: res.crop_roi,
          },
          spots: res.spots,
        },
        syntheticSnapRes
      );
      setLabStatusBanner(
        "is-ok",
        `✅ 自动标定完成：已为机位保存参考模板并识别 <strong>${res.spots.length}</strong> 个车位（引擎：<code>${escapeHtml(
          res.engine_used
        )}</code>，耗时 ${(res.elapsed_ms / 1000).toFixed(1)}s）。`,
        [
          {
            label: "前往「空间标定」微调车位框",
            onClick: () => openCalibrationForCamera(cameraId),
          },
        ]
      );
    }
    return res;
  } catch (err) {
    state.calib.calibratingPreview = false;
    await loadCameraSpotsForCalib(cameraId).catch(() => {});
    saveMsg.style.color = "#D70015";
    saveMsg.textContent = `自动标定失败：${err.message}`;
    if (fromTestLab) {
      setLabStatusBanner("is-error", `❌ 自动标定失败：${escapeHtml(err.message)}`);
    }
  } finally {
    state.calib.calibratingPreview = false;
    overlay.classList.add("hidden");
    btnUpload.disabled = false;
    updateCalibButtons();
    if (fromTestLab) setLabBusy(false);
    if (btnLabCal) {
      btnLabCal.disabled = false;
      btnLabCal.textContent = "新视角？设为参考模板并自动标定";
    }
  }
}

function updateTemplateBannerUI(templateResp) {
  const pill = document.getElementById("tplStatusPill");
  const summary = document.getElementById("tplSummaryText");
  if (!pill || !summary) return;

  if (templateResp && templateResp.has_template && templateResp.template_meta) {
    const m = templateResp.template_meta;
    pill.className = "tpl-pill tpl-pill-ready";
    pill.textContent = "已设参考模板";
    const hintPrefix = m.building_hint ? `${m.building_hint} · ` : "";
    const redactNote = m.plates_redacted ? ` · 脱敏${m.plates_redacted}处` : "";
    summary.textContent = `${hintPrefix}实标 ${m.spots_count || 0} 位（空闲 ${
      m.initial_free ?? "-"
    } / 占用 ${m.initial_occupied ?? "-"}${redactNote}）`;
  } else {
    pill.className = "tpl-pill tpl-pill-idle";
    pill.textContent = "未设模板";
    summary.textContent = "上传实拍图自动识别车位框并建立基准参考模板。";
  }
}

async function loadCameraSpotsForCalib(cameraId) {
  if (!cameraId) return;
  if (state.calib.cameraId && state.calib.cameraId !== cameraId) {
    state.calib.fullFrameImg = null;
    state.calib.fullFrameDataUrl = null;
    state.calib.fullFrameW = 0;
    state.calib.fullFrameH = 0;
  }
  const [data, tpl, vehTpl] = await Promise.all([
    api(`/api/cameras/${encodeURIComponent(cameraId)}/spots`),
    api(`/api/cameras/${encodeURIComponent(cameraId)}/template`),
    api(`/api/cameras/${encodeURIComponent(cameraId)}/vehicle-template`).catch(() => ({
      has_vehicle_template: false,
    })),
  ]);

  state.calib.cameraId = cameraId;
  state.calib.cameraMeta = data.camera || null;
  state.calib.zoneId = data.camera.zone_id;
  state.calib.templateMeta = tpl.template_meta || null;
  state.calib.cropRoi = data.camera?.crop_roi || tpl.crop_roi || { enabled: false, x: 0, y: 0, w: 1, h: 1 };
  state.calib.anchorPoints =
    Array.isArray(data.camera?.anchor_points) && data.camera.anchor_points.length
      ? data.camera.anchor_points.map((p) => ({ ...p }))
      : Array.isArray(tpl.anchor_points) && tpl.anchor_points.length
      ? tpl.anchor_points.map((p) => ({ ...p }))
      : [];
  updateAnchorPointsReadoutUI();
  state.calib.calibSchedule = data.camera?.calib_schedule || tpl.calib_schedule || {
    enabled: false,
    mode: "nightly",
    night_time: "03:00",
    interval_hours: 12,
    auto_compensate_shift: true,
  };
  state.calib.calibHealth = data.camera?.calib_health || tpl.calib_health || { status: "unverified" };
  document.getElementById("calibZoneSelect").value = data.camera.zone_id;
  updateTemplateBannerUI(tpl);
  renderCameraMetaSummaryCard();

  const canvas = document.getElementById("calibCanvas");
  const calW = data.camera.image_width || 1280;
  const calH = data.camera.image_height || 720;
  state.calib.imgW = calW;
  state.calib.imgH = calH;
  canvas.width = calW;
  canvas.height = calH;

  if (tpl.has_template && tpl.template_jpeg_b64) {
    await new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        state.calib.groundBgImage = img;
        state.calib.groundBgDataUrl = tpl.template_jpeg_b64;
        state.calib.groundBgIsServerPreview = true;
        resolve();
      };
      img.onerror = () => {
        state.calib.groundBgImage = null;
        state.calib.groundBgDataUrl = null;
        state.calib.groundBgIsServerPreview = false;
        resolve();
      };
      img.src = tpl.template_jpeg_b64;
    });
  } else {
    state.calib.groundBgImage = null;
    state.calib.groundBgDataUrl = null;
    state.calib.groundBgIsServerPreview = false;
  }

  if (vehTpl && vehTpl.has_vehicle_template && vehTpl.vehicle_template_jpeg_b64) {
    await new Promise((resolve) => {
      const vImg = new Image();
      vImg.onload = () => {
        state.calib.vehicleBgImage = vImg;
        state.calib.vehicleBgDataUrl = vehTpl.vehicle_template_jpeg_b64;
        state.calib.vehicleBgIsServerPreview = true;
        resolve();
      };
      vImg.onerror = () => {
        state.calib.vehicleBgImage = null;
        state.calib.vehicleBgDataUrl = null;
        state.calib.vehicleBgIsServerPreview = false;
        resolve();
      };
      vImg.src = vehTpl.vehicle_template_jpeg_b64;
    });
  } else {
    state.calib.vehicleBgImage = null;
    state.calib.vehicleBgDataUrl = null;
    state.calib.vehicleBgIsServerPreview = false;
  }

  syncCalibActiveBgImage();
  updateCropRoiStripUI();
  updateCalibGuardUI();

  state.calib.spots = (data.spots || []).map((sp) => {
    ensureSpotVehiclePolygon(sp);
    return sp;
  });
  document.getElementById("dualBenchPanel")?.classList.add("hidden");
  state.calib.selectedSpotIdx = state.calib.spots.length > 0 ? 0 : -1;
  state.calib.undoStack = [];
  state.calib.redoStack = [];
  state.calib.draftPoints = [];
  state.calib.curveFrontPts = [];
  state.calib.curveBackPts = [];
  state.calib.previewTrapezoids = [];
  markCalibDirty(false);

  // Sync active layer workspace banner & controls (default to dual so both ground & 3D spatial prisms show)
  setCalibLayerMode(state.calib.layerMode || "dual");
  // Auto-enter Smart Direct Manipulation mode whenever spots exist so users can adjust immediately!
  setCalibMode(state.calib.spots.length > 0 ? "smart_edit" : "curved_row");
  renderSpotListSidebar();
  renderCalibCanvas();
}

function updateCalibButtons() {
  document.getElementById("btnCommitRow").disabled = state.calib.previewTrapezoids.length === 0;
  document.getElementById("btnFinishIrregular").disabled = state.calib.draftPoints.length < 3;
  document.getElementById("btnFinishElastic").disabled = state.calib.draftPoints.length < 4;
  const rerunBtn = document.getElementById("btnRerunAutoCalibrate");
  if (rerunBtn) rerunBtn.disabled = !state.calib.bgDataUrl;
  const refineBtn = document.getElementById("btnRefineWhiteLines");
  if (refineBtn) refineBtn.disabled = !(state.calib.bgDataUrl && state.calib.spots.length > 0);
  updateCurvedHintUI();
}

function renderSpotListSidebar() {
  document.getElementById("calibSpotCount").textContent = state.calib.spots.length;
  const list = document.getElementById("calibSpotList");
  if (!state.calib.spots.length) {
    list.innerHTML = `<div class="locks-container">暂未框选区域，可上传参考图 AI 自动标定，或选「弯道弧形连排 / 直线连排」点选生成</div>`;
    return;
  }
  const stLabels = { FREE: "空闲", OCCUPIED: "已停", BLOCKED: "占道", UNKNOWN: "未知" };
  list.innerHTML = state.calib.spots
    .map((sp, i) => {
      const badge =
        sp.spot_type === "elastic_strip"
          ? `弹性带 ${sp.total_meters}m`
          : sp.spot_type === "irregular"
          ? sp.compact_only
            ? "异形小车"
            : "异形框"
          : "标准框";
      const pend = sp.pending_status;
      const st =
        sp.effective_status ||
        (pend === "OCCUPIED" || pend === "BLOCKED"
          ? pend
          : sp.status || sp.initial_status || "FREE");
      const isSel = i === state.calib.selectedSpotIdx;
      const custom3dTag = sp.vehicle_poly_custom ? " · 3D微调" : "";
      const isEv = Boolean(sp.is_ev || (Array.isArray(sp.tags) && sp.tags.includes("ev_charging")));
      return `
        <div class="calib-spot-item ${isSel ? "selected" : ""}" onclick="selectCalibSpot(${i})">
          <div>
            <strong>${escapeHtml(sp.label)}${isEv ? " ⚡" : ""}</strong>
            <small>${badge}${custom3dTag}</small>
            <span class="spot-chip chip-${st}" style="margin-left:6px">${stLabels[st] || st}${pend ? "(复核)" : ""}</span>
          </div>
          <div style="display:flex;align-items:center;">
            <button class="calib-ev-toggle-btn ${isEv ? "is-ev" : ""}" type="button" title="切换是否为新能源充电车位（保存标定时自动同步至平面图闪电符号）" onclick="event.stopPropagation(); toggleCalibSpotEv(${i})">⚡${isEv ? "新能源" : "设为电桩"}</button>
            <button class="link-btn danger-link" type="button" onclick="event.stopPropagation(); removeCalibSpot(${i})">移除</button>
          </div>
        </div>
      `;
    })
    .join("");
}

window.toggleCalibSpotEv = function (idx) {
  const sp = state.calib.spots[idx];
  if (!sp) return;
  pushCalibUndoSnapshot();
  const curTags = Array.isArray(sp.tags) ? [...sp.tags] : [];
  const wasEv = Boolean(sp.is_ev || curTags.includes("ev_charging"));
  const nextEv = !wasEv;
  sp.is_ev = nextEv;
  if (nextEv) {
    if (!curTags.includes("ev_charging")) curTags.unshift("ev_charging");
  } else {
    const k = curTags.indexOf("ev_charging");
    if (k >= 0) curTags.splice(k, 1);
  }
  sp.tags = curTags;
  sp.tag_meta = { ...(sp.tag_meta || {}), ev_user_override: nextEv };
  markCalibDirty(true);
  renderSpotListSidebar();
  renderCalibCanvas();
};

window.selectCalibSpot = function (idx) {
  state.calib.selectedSpotIdx = idx;
  if (state.calib.mode !== "smart_edit") {
    setCalibMode("smart_edit");
  } else {
    syncSelectedSpotInspector();
    renderSpotListSidebar();
    renderCalibCanvas();
  }
};

window.removeCalibSpot = function (idx) {
  if (idx < 0 || idx >= state.calib.spots.length) return;
  pushCalibUndoSnapshot();
  state.calib.spots.splice(idx, 1);
  if (state.calib.selectedSpotIdx >= state.calib.spots.length) {
    state.calib.selectedSpotIdx = state.calib.spots.length - 1;
  }
  syncSelectedSpotInspector();
  updateCalibButtons();
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
  ctx.fillText("窗台俯拍透视标定示意（点击右上「载入底图」或「上传参考图 AI 自动标定」）", 28, 40);
}

function draw3DPrismEnvelope(ctx, groundPoly, vehPoly, strokeColor, fillColor, lineW = 2.0, dashed = true) {
  if (!vehPoly || vehPoly.length < 3) return;
  ctx.save();
  // Draw 4 vertical corner connectors from ground footprint up to 3D vehicle roof envelope
  if (groundPoly && groundPoly.length >= 3) {
    const gPts = groundPoly.length === 4 ? orderQuadTlTrBrBlJS(groundPoly) : groundPoly;
    const vPts = vehPoly.length === 4 ? orderQuadTlTrBrBlJS(vehPoly) : vehPoly;
    const n = Math.min(gPts.length, vPts.length);
    ctx.strokeStyle = strokeColor;
    ctx.globalAlpha = 0.72;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 3]);
    for (let k = 0; k < n; k++) {
      ctx.beginPath();
      ctx.moveTo(gPts[k][0], gPts[k][1]);
      ctx.lineTo(vPts[k][0], vPts[k][1]);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1.0;
  ctx.setLineDash(dashed ? [6, 4] : []);
  drawPolygon(ctx, vehPoly, strokeColor, fillColor, lineW);
  ctx.setLineDash([]);
  ctx.restore();
}

function renderCalibCanvas() {
  const canvas = document.getElementById("calibCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Special Canvas Mode: Per-Camera Parking-Strip ROI Crop Editor (`crop_roi`)
  if (state.calib.mode === "crop_roi") {
    const ffImg = state.calib.fullFrameImg || state.calib.bgImage;
    const ffW = state.calib.fullFrameW || ffImg?.naturalWidth || 1280;
    const ffH = state.calib.fullFrameH || ffImg?.naturalHeight || 720;
    if (canvas.width !== ffW || canvas.height !== ffH) {
      canvas.width = ffW;
      canvas.height = ffH;
    }
    if (ffImg) {
      ctx.drawImage(ffImg, 0, 0, canvas.width, canvas.height);
    } else {
      drawSyntheticDemoLotOnCalibCanvas(canvas, canvas.width, canvas.height);
    }

    // Project existing calibrated spots onto full-frame reference so user sees where stalls sit
    const curRoi = state.calib.cropRoi || { enabled: false, x: 0, y: 0, w: 1, h: 1 };
    const calW = Math.max(1, state.calib.imgW || canvas.width);
    const calH = Math.max(1, state.calib.imgH || canvas.height);
    state.calib.spots.forEach((sp) => {
      if (!sp.polygon || sp.polygon.length < 3) return;
      const proj = sp.polygon.map(([px, py]) => {
        const nx = curRoi.enabled ? curRoi.x + (px / calW) * curRoi.w : px / calW;
        const ny = curRoi.enabled ? curRoi.y + (py / calH) * curRoi.h : py / calH;
        return [nx * canvas.width, ny * canvas.height];
      });
      ctx.save();
      ctx.setLineDash([6, 4]);
      drawPolygon(ctx, proj, "rgba(52, 199, 89, 0.9)", "rgba(52, 199, 89, 0.12)", 2.2);
      ctx.restore();
      const [cx, cy] = getPolyCentroid(proj);
      drawPillLabel(ctx, cx, cy, sp.label);
    });

    const d = state.calib.cropDraft || { x: 0, y: 0.25, w: 1, h: 0.5 };
    const x0 = Math.round(d.x * canvas.width);
    const y0 = Math.round(d.y * canvas.height);
    const w = Math.round(d.w * canvas.width);
    const h = Math.round(d.h * canvas.height);
    const x1 = x0 + w;
    const y1 = y0 + h;

    // Darken non-parking top/bottom/side regions
    ctx.save();
    ctx.fillStyle = "rgba(15, 23, 42, 0.62)";
    ctx.fillRect(0, 0, canvas.width, y0);
    ctx.fillRect(0, y1, canvas.width, Math.max(0, canvas.height - y1));
    ctx.fillRect(0, y0, x0, h);
    ctx.fillRect(x1, y0, Math.max(0, canvas.width - x1), h);

    // Rule-of-thirds interior guide lines
    ctx.strokeStyle = "rgba(255, 255, 255, 0.32)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(x0, y0 + h / 3);
    ctx.lineTo(x1, y0 + h / 3);
    ctx.moveTo(x0, y0 + (2 * h) / 3);
    ctx.lineTo(x1, y0 + (2 * h) / 3);
    ctx.moveTo(x0 + w / 3, y0);
    ctx.lineTo(x0 + w / 3, y1);
    ctx.moveTo(x0 + (2 * w) / 3, y0);
    ctx.lineTo(x0 + (2 * w) / 3, y1);
    ctx.stroke();
    ctx.setLineDash([]);

    // Vibrant cyan crop frame
    ctx.strokeStyle = "#00E5FF";
    ctx.lineWidth = Math.max(3, canvas.width * 0.0028);
    ctx.strokeRect(x0, y0, w, h);
    ctx.restore();

    const xm = 0.5 * (x0 + x1);
    const ym = 0.5 * (y0 + y1);
    const hr = Math.max(8.5, canvas.width * 0.0075);
    [
      [x0, y0],
      [xm, y0],
      [x1, y0],
      [x1, ym],
      [x1, y1],
      [xm, y1],
      [x0, y1],
      [x0, ym],
    ].forEach(([hx, hy]) => drawHandle(ctx, hx, hy, "#00E5FF", "", hr));

    drawPillLabel(
      ctx,
      xm,
      Math.max(24, y0 - 20),
      `✂️ 机位固定裁剪区：Y ${Math.round(d.y * 100)}% ~ ${Math.round((d.y + d.h) * 100)}% (${w}×${h}px)`
    );
    return;
  }

  if (canvas.width !== state.calib.imgW || canvas.height !== state.calib.imgH) {
    canvas.width = state.calib.imgW || 1280;
    canvas.height = state.calib.imgH || 720;
  }

  if (state.calib.bgImage) {
    ctx.drawImage(state.calib.bgImage, 0, 0, canvas.width, canvas.height);
  } else {
    drawSyntheticDemoLotOnCalibCanvas(canvas, canvas.width, canvas.height);
  }

  if (state.calib.calibratingPreview) {
    return;
  }

  const alpha = state.calib.fillAlpha;
  const hov = state.calib.hoveredTarget;
  const layerMode = state.calib.layerMode || "dual";

  state.calib.spots.forEach((sp, sIdx) => {
    const groundPoly = sp.polygon;
    if (!groundPoly || groundPoly.length < 3) return;
    const vehPoly = ensureSpotVehiclePolygon(sp);
    const activePoly = layerMode === "vehicle" ? vehPoly : groundPoly;
    const isSel = state.calib.mode === "smart_edit" && sIdx === state.calib.selectedSpotIdx;
    const pend = sp.pending_status;
    const st =
      sp.effective_status ||
      (pend === "OCCUPIED" || pend === "BLOCKED"
        ? pend
        : sp.status || sp.initial_status || "FREE");

    let stroke = "#34C759";
    let rgb = "52, 199, 89";
    if (st === "OCCUPIED") {
      stroke = "#FF453A";
      rgb = "255, 69, 58";
    } else if (st === "BLOCKED") {
      stroke = "#FF9F0A";
      rgb = "255, 159, 10";
    } else if (sp.spot_type === "irregular") {
      stroke = "#FF9500";
      rgb = "255, 149, 0";
    } else if (sp.spot_type === "elastic_strip") {
      stroke = "#007AFF";
      rgb = "0, 122, 255";
    }

    const effAlpha = isSel ? Math.min(0.45, alpha + 0.08) : alpha;

    if (layerMode === "vehicle") {
      // Optional muted ground reference footprint only when user opts in via checkbox
      if (state.calib.showGroundGhost) {
        ctx.save();
        ctx.setLineDash([4, 4]);
        drawPolygon(
          ctx,
          groundPoly,
          "rgba(255, 255, 255, 0.48)",
          "rgba(255, 255, 255, 0.03)",
          1.5
        );
        ctx.restore();
      }
      // Clean standalone Vehicle Occupancy Envelope (no cross-layer clutter unless ghost opted in)
      const vehStroke = isSel ? "#00E5FF" : st === "OCCUPIED" ? "#FB7185" : "#38BDF8";
      const vehRgb = st === "OCCUPIED" ? "251, 113, 133" : "56, 189, 248";
      if (state.calib.showGroundGhost) {
        draw3DPrismEnvelope(
          ctx,
          groundPoly,
          vehPoly,
          vehStroke,
          `rgba(${vehRgb}, ${effAlpha})`,
          isSel ? 3.2 : 2.4,
          false
        );
      } else {
        drawPolygon(
          ctx,
          vehPoly,
          vehStroke,
          `rgba(${vehRgb}, ${effAlpha})`,
          isSel ? 3.2 : 2.5
        );
      }
    } else {
      // Draw Ground Footprint Layer (solid)
      drawPolygon(
        ctx,
        groundPoly,
        isSel ? "#00E5FF" : stroke,
        `rgba(${rgb}, ${effAlpha})`,
        isSel ? 3.4 : 2.5
      );
      if (pend) {
        ctx.save();
        ctx.setLineDash([5, 4]);
        drawPolygon(ctx, groundPoly, "#AF52DE", "rgba(175, 82, 222, 0.06)", 2.2);
        ctx.restore();
      }
      // In Dual-Layer mode, overlay 3D Vehicle Prism wireframe above ground footprint
      if (layerMode === "dual" && vehPoly && vehPoly.length >= 3) {
        const prismColor = st === "OCCUPIED" ? "rgba(251, 113, 133, 0.90)" : "rgba(56, 189, 248, 0.88)";
        const prismFill =
          st === "OCCUPIED" ? "rgba(251, 113, 133, 0.07)" : "rgba(56, 189, 248, 0.06)";
        draw3DPrismEnvelope(ctx, groundPoly, vehPoly, prismColor, prismFill, isSel ? 2.1 : 1.7, true);
      }
    }

    // Highlight hovered edge segment in smart_edit mode so shared-divider dragging is crystal clear
    if (state.calib.mode === "smart_edit" && hov && hov.type === "edge" && hov.spotIdx === sIdx) {
      const pA = activePoly[hov.pA];
      const pB = activePoly[hov.pB];
      if (pA && pB) {
        ctx.save();
        ctx.strokeStyle = "#FFE600";
        ctx.lineWidth = 5;
        ctx.beginPath();
        ctx.moveTo(pA[0], pA[1]);
        ctx.lineTo(pB[0], pB[1]);
        ctx.stroke();
        ctx.restore();
      }
    }

    const [cx, cy] = getPolyCentroid(activePoly);
    const isCalEv = Boolean(sp.is_ev || (Array.isArray(sp.tags) && sp.tags.includes("ev_charging")));
    const evMark = isCalEv ? " ⚡" : "";
    const suffix = pend
      ? " · 复核中"
      : st === "OCCUPIED"
      ? " · 已停"
      : st === "BLOCKED"
      ? " · 占道"
      : "";
    drawPillLabel(ctx, cx, cy, `${sp.label}${evMark}${suffix}`);

    if (state.calib.mode === "smart_edit") {
      activePoly.forEach(([x, y], pIdx) => {
        const isHovVert = hov && hov.type === "vertex" && hov.spotIdx === sIdx && hov.ptIdx === pIdx;
        const baseHandleColor = layerMode === "vehicle" ? "#38BDF8" : "#007AFF";
        if (isSel || isHovVert) {
          drawHandle(ctx, x, y, isHovVert ? "#FFE600" : baseHandleColor, "", isHovVert ? 9 : 7.5);
        } else {
          drawHandle(ctx, x, y, "rgba(255,255,255,0.88)", "", 5.2);
        }
      });

      if (isSel) {
        const rotH = getRotationHandlePos(activePoly, canvas.width);
        if (rotH) {
          ctx.save();
          ctx.strokeStyle = "#00E5FF";
          ctx.lineWidth = 2.2;
          ctx.setLineDash([4, 3]);
          ctx.beginPath();
          ctx.moveTo(rotH.anchorX, rotH.anchorY);
          ctx.lineTo(rotH.x, rotH.y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
          drawHandle(ctx, rotH.x, rotH.y, "#00E5FF", "↻", 8.5);
        }
        if (layerMode === "vehicle" || layerMode === "dual") {
          const roofH = getRoofHeightHandlePos(ensureSpotVehiclePolygon(sp), canvas.width);
          if (roofH) {
            ctx.save();
            ctx.strokeStyle = "#FF9F0A";
            ctx.lineWidth = 2.4;
            ctx.setLineDash([3, 2]);
            ctx.beginPath();
            ctx.moveTo(roofH.anchorX, roofH.anchorY);
            ctx.lineTo(roofH.x, roofH.y);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.restore();
            drawHandle(ctx, roofH.x, roofH.y, "#FF9F0A", "↕", 8.8);
          }
        }
      }
    }
  });

  if (state.calib.previewTrapezoids.length > 0) {
    state.calib.previewTrapezoids.forEach((trap, idx) => {
      drawPolygon(ctx, trap, "#0A84FF", "rgba(10, 132, 255, 0.24)", 2.4);
      const [cx, cy] = getPolyCentroid(trap);
      drawPillLabel(ctx, cx, cy, `#${String(idx + 1).padStart(2, "0")}`);
    });
  }

  // Draw Curved Row front & back smooth splines + control points
  if (state.calib.mode === "curved_row") {
    const drawCurveGuide = (pts, color, tagPrefix) => {
      if (!pts.length) return;
      if (pts.length >= 2) {
        const smooth = evalCatmullRomChainJS(pts, 28);
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.beginPath();
        smooth.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
        ctx.stroke();
        ctx.restore();
      }
      pts.forEach(([x, y], i) => drawHandle(ctx, x, y, color, `${tagPrefix}${i + 1}`, 7.5));
    };
    drawCurveGuide(state.calib.curveFrontPts, "#00E5FF", "前");
    drawCurveGuide(state.calib.curveBackPts, "#FF9F0A", "后");
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

  // Draw Fixed Ground Positioning Anchors (A1~A4+)
  const anchorsToDraw = Array.isArray(state.calib.anchorPoints) ? state.calib.anchorPoints : [];
  if (anchorsToDraw.length > 0 && state.calib.mode !== "crop_roi") {
    const isAnchorMode = state.calib.mode === "anchor_points";
    if (isAnchorMode && anchorsToDraw.length >= 3) {
      ctx.save();
      ctx.strokeStyle = "rgba(245, 158, 11, 0.82)";
      ctx.fillStyle = "rgba(245, 158, 11, 0.07)";
      ctx.lineWidth = 2.2;
      ctx.setLineDash([7, 5]);
      ctx.beginPath();
      anchorsToDraw.forEach((ap, i) =>
        i === 0 ? ctx.moveTo(Number(ap.x), Number(ap.y)) : ctx.lineTo(Number(ap.x), Number(ap.y))
      );
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
    }
    anchorsToDraw.forEach((ap, idx) => {
      drawAnchorDiamondHandle(
        ctx,
        Number(ap.x || 0),
        Number(ap.y || 0),
        ap.id || `A${idx + 1}`,
        isAnchorMode ? ap.label || "" : "",
        isAnchorMode
      );
    });
  }
}

function drawPolygon(ctx, poly, stroke, fill, lineW = 2, dash = null) {
  ctx.save();
  if (Array.isArray(dash) && dash.length) {
    ctx.setLineDash(dash);
  }
  ctx.beginPath();
  poly.forEach(([x, y], idx) => (idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.strokeStyle = stroke;
  ctx.lineWidth = lineW;
  ctx.stroke();
  ctx.restore();
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

function drawHandle(ctx, x, y, color, label = "", radius = 7.5) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = "#1C1C1E";
  ctx.lineWidth = 1.6;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(x, y, radius + 1.2, 0, Math.PI * 2);
  ctx.strokeStyle = "#FFFFFF";
  ctx.lineWidth = 1.6;
  ctx.stroke();
  if (label === "↻" || label === "↕") {
    ctx.fillStyle = "#1C1C1E";
    ctx.font = "700 12px -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(label, x, y + 4);
    ctx.textAlign = "start";
  } else if (label) {
    ctx.fillStyle = "#FFFFFF";
    ctx.font = "600 12px -apple-system, sans-serif";
    ctx.fillText(label, x + 10, y - 8);
  }
}

/* ==================== TAB 3: SNAPSHOT TEST LAB ==================== */
function setLabStatusBanner(variant, html, actions = []) {
  const banner = document.getElementById("labStatusBanner");
  if (!banner) return;
  if (!html) {
    banner.className = "lab-status-banner hidden";
    banner.innerHTML = "";
    return;
  }
  banner.className = `lab-status-banner ${variant || "is-info"}`;
  banner.innerHTML = `<div class="lab-status-text">${html}</div>`;
  if (actions && actions.length > 0) {
    const actWrap = document.createElement("div");
    actWrap.className = "lab-status-actions";
    actions.forEach((act) => {
      if (!act || !act.label) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `lab-inline-btn${act.primary ? " is-primary" : ""}`;
      btn.textContent = act.label;
      btn.addEventListener("click", act.onClick);
      actWrap.appendChild(btn);
    });
    banner.appendChild(actWrap);
  }
}

function setLabBusy(busy) {
  state.lab.busy = Boolean(busy);
  const pickBtn = document.getElementById("btnPickLabImage");
  const autoBtn = document.getElementById("btnLabAutoCalibrate");
  if (pickBtn) {
    pickBtn.disabled = state.lab.busy;
    pickBtn.textContent = state.lab.busy ? "正在识别处理中..." : "上传日常抓拍图测试";
  }
  if (autoBtn && !autoBtn.textContent.includes("自动标定建档")) {
    autoBtn.disabled = state.lab.busy;
  }
  document.querySelectorAll(".synth-btn").forEach((b) => {
    b.disabled = state.lab.busy;
  });
}

function findBestCameraWithSpots(excludeCamId = "") {
  const cams = state.overview?.cameras || [];
  return (
    cams.find((c) => c.camera_id !== excludeCamId && Number(c.calibrated_spot_count || 0) > 0) ||
    cams.find((c) => c.camera_id !== excludeCamId && Number(c.spot_count || 0) > 0) ||
    null
  );
}

async function openCalibrationForCamera(camId, optionalBgDataUrl = "") {
  const calibSel = document.getElementById("calibCameraSelect");
  if (calibSel && camId) {
    state.lab.calibUserSelectedCam = true;
    calibSel.value = camId;
    state.calib.cameraId = camId;
  }
  switchMainTab("calibrate");
  renderCameraMetaSummaryCard();
  await loadCameraSpotsForCalib(state.calib.cameraId);
  if (optionalBgDataUrl) {
    await new Promise((resolve) => {
      const img = new Image();
      img.onload = async () => {
        state.calib.fullFrameImg = img;
        state.calib.fullFrameDataUrl = optionalBgDataUrl;
        state.calib.fullFrameW = img.naturalWidth;
        state.calib.fullFrameH = img.naturalHeight;

        const cropRes = cropImageSourceIfNeeded(img, state.calib.cropRoi);
        let effImg = img;
        if (cropRes.wasCropped && cropRes.dataUrl) {
          await new Promise((resCrop) => {
            const cImg = new Image();
            cImg.onload = () => {
              effImg = cImg;
              resCrop();
            };
            cImg.onerror = () => resCrop();
            cImg.src = cropRes.dataUrl;
          });
        }
        if (state.calib.spots.length > 0) {
          rescaleCalibSpotsToSize(effImg.naturalWidth || cropRes.width, effImg.naturalHeight || cropRes.height);
        }
        state.calib.groundBgImage = effImg;
        state.calib.groundBgDataUrl = cropRes.dataUrl || optionalBgDataUrl;
        state.calib.groundBgIsServerPreview = false;
        syncCalibActiveBgImage();
        renderCalibCanvas();
        resolve();
      };
      img.onerror = () => resolve();
      img.src = optionalBgDataUrl;
    });
  }
}

function getUnassignedFloorplanSummary() {
  let count = 0;
  const groups = new Set();
  for (const z of state.overview?.zones || []) {
    for (const sp of z.spots || []) {
      if (sp.camera_id === "UNASSIGNED") {
        count += 1;
        if (sp.group_code) groups.add(sp.group_code);
      }
    }
  }
  return {
    count,
    sampleGroups: Array.from(groups).slice(0, 3),
  };
}

function openFloorPlanCameraBinding() {
  const homeTabBtn = document.querySelector('.seg-tab[data-tab="tab-home"]');
  if (homeTabBtn) homeTabBtn.click();
  if (typeof enterSiteMapEditMode === "function" && !state.siteMap.editMode) {
    enterSiteMapEditMode();
  }
  const mapCard = document.getElementById("siteMapCard") || document.getElementById("siteMapSvg");
  if (mapCard) {
    mapCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function buildSandboxPreviewSpots(w, h, camId) {
  const count = 5;
  const marginX = Math.round(w * 0.08);
  const usableW = Math.max(320, w - marginX * 2);
  const stepX = usableW / count;
  const padX = Math.max(8, Math.round(stepX * 0.07));
  const yTop = Math.round(h * 0.30);
  const yBot = Math.round(h * 0.78);
  const spots = [];
  for (let i = 0; i < count; i += 1) {
    const x0 = Math.round(marginX + i * stepX + padX);
    const x1 = Math.round(marginX + (i + 1) * stepX - padX);
    const perspIn = Math.round((x1 - x0) * 0.06);
    spots.push({
      spot_id: `${camId}_SANDBOX_0${i + 1}`,
      label: `沙盒-0${i + 1}`,
      spot_type: "standard",
      total_meters: 5.2,
      polygon: [
        [x0 + perspIn, yTop],
        [x1 - perspIn, yTop],
        [x1, yBot],
        [x0, yBot],
      ],
    });
  }
  return spots;
}

function refreshLabCameraHint(preserveActiveResult = false) {
  const labPreviewVisible = !document.getElementById("labPreviewWrap")?.classList.contains("hidden");
  if (preserveActiveResult && labPreviewVisible) return;
  const camId = document.getElementById("labCameraSelect")?.value;
  const cam = (state.overview?.cameras || []).find((c) => c.camera_id === camId);
  if (!cam) return;
  const spotCount = Number(cam.spot_count || 0);
  const calibCount = Number(cam.calibrated_spot_count || 0);

  if (spotCount === 0) {
    const altCam = findBestCameraWithSpots(cam.camera_id);
    const unassigned = getUnassignedFloorplanSummary();
    const actions = [];
    if (altCam) {
      actions.push({
        label: `切换到「${altCam.name}」(${altCam.spot_count}车位)`,
        primary: true,
        onClick: () => {
          const sel = document.getElementById("labCameraSelect");
          state.lab.userSelectedCam = true;
          sel.value = altCam.camera_id;
          refreshLabCameraHint(false);
        },
      });
    }
    if (unassigned.count > 0) {
      actions.push({
        label: `前往平面图绑定待绑车位 (${unassigned.count}个)`,
        primary: !altCam,
        onClick: () => openFloorPlanCameraBinding(),
      });
    }
    actions.push({
      label: "上传实景图自动标定建档",
      primary: !altCam && unassigned.count === 0,
      onClick: () => document.getElementById("labAutoCalibInput")?.click(),
    });
    actions.push({
      label: "前往「空间标定」画车位框",
      onClick: () => openCalibrationForCamera(cam.camera_id),
    });
    const unassignedNote =
      unassigned.count > 0
        ? `（检测到态势平面图中已有 <strong>${unassigned.count}</strong> 个车位如 <code>${escapeHtml(
            unassigned.sampleGroups.join(" / ")
          )}</code> 处于「待绑定机位」状态）`
        : "";
    setLabStatusBanner(
      "is-info",
      `ℹ️ 当前机位 <strong>${escapeHtml(cam.name)}</strong>（<code>${escapeHtml(
        cam.camera_id
      )}</code>）尚未绑定车位（<strong>0 车位</strong>）${unassignedNote}。直接上传实景照片将自动尝试提取车位标定框并输出识别结果。`,
      actions
    );
  } else if (calibCount === 0) {
    setLabStatusBanner(
      "is-info",
      `ℹ️ 当前机位 <strong>${escapeHtml(
        cam.name
      )}</strong> 已从态势平面图绑定 <strong>${spotCount}</strong> 个车位（当前为默认分布框，可直接上传抓拍图测试；接入真实监控画面后建议对齐实景车位框）。`,
      [
        {
          label: "上传实景参考图自动对框",
          primary: true,
          onClick: () => document.getElementById("labAutoCalibInput")?.click(),
        },
        {
          label: "前往「空间标定」手动对框",
          onClick: () => openCalibrationForCamera(cam.camera_id),
        },
      ]
    );
  } else {
    setLabStatusBanner(
      "is-ok",
      `✅ 当前机位 <strong>${escapeHtml(
        cam.name
      )}</strong> 已就绪（共 <strong>${spotCount}</strong> 个实景标定车位），请拖入或选择抓拍图进行识别测试。`
    );
  }
}

function bindTestLab() {
  const dropzone = document.getElementById("labDropzone");
  const fileInput = document.getElementById("labImageInput");
  const labAutoCalInput = document.getElementById("labAutoCalibInput");
  const labCamSel = document.getElementById("labCameraSelect");

  labCamSel.addEventListener("change", () => {
    state.lab.userSelectedCam = true;
    document.getElementById("labPreviewWrap")?.classList.add("hidden");
    document.getElementById("labReportsWrap")?.classList.add("hidden");
    refreshLabCameraHint(false);
  });

  document.getElementById("btnPickLabImage").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files[0]) uploadSnapshotFile(e.target.files[0]);
    fileInput.value = "";
  });

  document.getElementById("btnLabAutoCalibrate").addEventListener("click", () => {
    labAutoCalInput.click();
  });
  labAutoCalInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const camId = document.getElementById("labCameraSelect").value;
      const camObj = (state.overview?.cameras || []).find((c) => c.camera_id === camId);
      const zoneId = camObj ? camObj.zone_id : "Z_BLDG3_EAST";
      await runAutoCalibrationWithDataUrl(camId, zoneId, ev.target.result, true);
    };
    reader.onerror = () => {
      setLabStatusBanner("is-error", "❌ 读取本地图片文件失败，请换一张 JPG/PNG 图像重试。");
    };
    reader.readAsDataURL(file);
    labAutoCalInput.value = "";
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
    state.siteMap.backdropDataUrl = "";
    state.siteMap.backdropUpdatedAt = 0;
    state.siteMap.showBackdrop = false;
    state.lab.userSelectedCam = false;
    state.lab.calibUserSelectedCam = false;
    exitSiteMapEditMode();
    await refreshOverview();
    await loadCameraSpotsForCalib(state.calib.cameraId);
  });
}

function uploadSnapshotFile(file) {
  setLabBusy(true);
  setLabStatusBanner("is-loading", `⏳ 正在读取图像「${escapeHtml(file.name)}」并上传执行视觉识别...`);
  const reader = new FileReader();
  reader.onload = async (ev) => {
    await submitSnapshotDataUrl(ev.target.result, `上传图像 (${file.name})`, false);
  };
  reader.onerror = () => {
    setLabBusy(false);
    setLabStatusBanner("is-error", `❌ 无法读取图像文件「${escapeHtml(file.name)}」，请检查文件格式。`);
  };
  reader.readAsDataURL(file);
}

async function generateAndSendSyntheticSnapshot(sceneType) {
  const camId = document.getElementById("labCameraSelect").value;
  setLabBusy(true);
  setLabStatusBanner("is-loading", `⏳ 正在生成内置基准测试帧 (${escapeHtml(sceneType)}) 并执行识别...`);
  try {
    const spotData = await api(`/api/cameras/${encodeURIComponent(camId)}/spots`);
    const w = spotData.camera.image_width || 1280;
    const h = spotData.camera.image_height || 720;
    const useSandbox = !spotData.spots || spotData.spots.length === 0;
    const activeSpots = useSandbox ? buildSandboxPreviewSpots(w, h, camId) : spotData.spots;
    const effectiveSpotData = useSandbox ? { ...spotData, spots: activeSpots } : spotData;

    const off = document.createElement("canvas");
    off.width = w;
    off.height = h;
    const ctx = off.getContext("2d");

    ctx.fillStyle = "#48484A";
    ctx.fillRect(0, 0, w, h);

    activeSpots.forEach((sp, idx) => {
      const poly = sp.polygon;
      ctx.strokeStyle = "rgba(255,255,255,0.68)";
      ctx.lineWidth = 3;
      ctx.beginPath();
      poly.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      ctx.closePath();
      ctx.stroke();

      if (sceneType === "mixed_cars_cone") {
        if (idx === 0 || idx === 2) {
          drawSyntheticCarInPoly(ctx, poly, idx === 0 ? "#0071E3" : "#E5E5EA");
        } else if (idx === 1) {
          drawSyntheticConeInPoly(ctx, poly);
        }
      } else if (sceneType === "passing_car" && idx === 0) {
        drawSyntheticCarInPoly(ctx, poly, "#FF3B30");
      }
    });

    const dataUrl = off.toDataURL("image/jpeg", 0.9);
    await submitSnapshotDataUrl(
      dataUrl,
      `内置测试帧 (${sceneType})`,
      true,
      effectiveSpotData,
      sceneType,
      useSandbox ? activeSpots : null
    );
  } catch (err) {
    setLabBusy(false);
    setLabStatusBanner("is-error", `❌ 生成或测试基准帧失败：${escapeHtml(err.message)}`);
  }
}

function drawSyntheticCarInPoly(ctx, poly, bodyColor) {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  const cx = xs.reduce((s, v) => s + v, 0) / xs.length;
  const cy = ys.reduce((s, v) => s + v, 0) / ys.length;
  const boxW = Math.max(40, Math.max(...xs) - Math.min(...xs));
  const boxH = Math.max(60, Math.max(...ys) - Math.min(...ys));
  const carW = Math.min(boxW * 0.72, 130);
  const carH = Math.min(boxH * 0.76, 220);

  ctx.save();
  ctx.translate(cx, cy);
  ctx.fillStyle = bodyColor;
  ctx.fillRect(-carW / 2, -carH / 2, carW, carH);
  ctx.strokeStyle = "#1C1C1E";
  ctx.lineWidth = 2.5;
  ctx.strokeRect(-carW / 2, -carH / 2, carW, carH);
  // Windshield & rear window
  ctx.fillStyle = "#1C1C1E";
  ctx.fillRect(-carW * 0.38, -carH * 0.31, carW * 0.76, carH * 0.2);
  ctx.fillRect(-carW * 0.36, carH * 0.14, carW * 0.72, carH * 0.18);
  // Cabin roof + panoramic sunroof + roof rails
  ctx.fillStyle = "#F5F5F7";
  ctx.fillRect(-carW * 0.34, -carH * 0.09, carW * 0.68, carH * 0.21);
  ctx.fillStyle = "#2C2C2E";
  ctx.fillRect(-carW * 0.22, -carH * 0.04, carW * 0.44, carH * 0.11);
  // Hood creases & front grille
  ctx.strokeStyle = "rgba(28,28,30,0.65)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(-carW * 0.26, -carH * 0.46);
  ctx.lineTo(-carW * 0.32, -carH * 0.32);
  ctx.moveTo(carW * 0.26, -carH * 0.46);
  ctx.lineTo(carW * 0.32, -carH * 0.32);
  ctx.moveTo(-carW * 0.34, -carH * 0.44);
  ctx.lineTo(carW * 0.34, -carH * 0.44);
  ctx.stroke();
  // Headlights & taillights
  ctx.fillStyle = "#FFE680";
  ctx.fillRect(-carW * 0.43, -carH / 2 + 2, carW * 0.2, Math.max(6, carH * 0.06));
  ctx.fillRect(carW * 0.23, -carH / 2 + 2, carW * 0.2, Math.max(6, carH * 0.06));
  ctx.fillStyle = "#FF3B30";
  ctx.fillRect(-carW * 0.42, carH / 2 - Math.max(7, carH * 0.06), carW * 0.2, Math.max(5, carH * 0.05));
  ctx.fillRect(carW * 0.22, carH / 2 - Math.max(7, carH * 0.06), carW * 0.2, Math.max(5, carH * 0.05));
  ctx.restore();
}

function drawSyntheticConeInPoly(ctx, poly) {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  const cx = xs.reduce((s, v) => s + v, 0) / xs.length;
  const cy = ys.reduce((s, v) => s + v, 0) / ys.length;
  const boxMin = Math.max(36, Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)));
  const r = Math.max(16, Math.min(28, boxMin * 0.22));
  ctx.save();
  ctx.fillStyle = "#FF6B00";
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#FFFFFF";
  ctx.lineWidth = Math.max(3, r * 0.22);
  ctx.stroke();
  ctx.restore();
}

async function submitSnapshotDataUrl(
  dataUrl,
  sourceLabel,
  isSynthetic = false,
  prefetchedSpotData = null,
  synthSceneType = "",
  sandboxPreviewSpots = null
) {
  const camId = document.getElementById("labCameraSelect").value;
  const instantConfirm = document.getElementById("labDebounceMode").value === "instant";
  const simOffset = document.getElementById("labSimTimeOffset").checked ? 4.2 : 0.0;
  const forceReanalyze = document.getElementById("labForceReanalyze").checked;

  state.lab.lastDataUrl = dataUrl;
  state.lab.lastSourceLabel = sourceLabel;
  setLabBusy(true);
  setLabStatusBanner("is-loading", `⏳ 正在对「${escapeHtml(sourceLabel)}」执行逐车位透视裁切与多模态/OpenCV识别...`);

  try {
    let spotData = prefetchedSpotData || (await api(`/api/cameras/${encodeURIComponent(camId)}/spots`));
    const camMeta = (state.overview?.cameras || []).find((c) => c.camera_id === camId) || spotData.camera || {};
    let autoBootstrappedCount = 0;

    if ((!spotData.spots || spotData.spots.length === 0) && !sandboxPreviewSpots && !isSynthetic) {
      setLabStatusBanner(
        "is-loading",
        `⏳ 当前机位「${escapeHtml(
          camMeta.name || camId
        )}」尚无标定车位，正在从您上传的画面自动提取车位标定框并继续识别...`
      );
      try {
        const calRes = await api(`/api/cameras/${encodeURIComponent(camId)}/auto-calibrate`, {
          method: "POST",
          body: JSON.stringify({
            image_base64: dataUrl,
            zone_id: camMeta.zone_id || "Z_BLDG3_EAST",
            auto_save: true,
          }),
        });
        if (calRes?.spots?.length > 0) {
          autoBootstrappedCount = calRes.spots.length;
          spotData = await api(`/api/cameras/${encodeURIComponent(camId)}/spots`);
          const calCamSel = document.getElementById("calibCameraSelect");
          if (calCamSel) calCamSel.value = camId;
          state.calib.cameraId = camId;
          state.calib.layerMode = "dual";
          await loadCameraSpotsForCalib(camId);
        }
      } catch (_autoCalErr) {
        // Fall through to manual guidance banner below if automatic calibration cannot find stalls
      }
    }

    if ((!spotData.spots || spotData.spots.length === 0) && !sandboxPreviewSpots) {
      renderLabOverlayAndReport(dataUrl, spotData, {
        elapsed_ms: 0,
        instant_confirm_mode: instantConfirm,
        debounce_seconds: 0,
        reports: [],
        zero_spots_notice: true,
      });
      const altCam = findBestCameraWithSpots(camId);
      const unassigned = getUnassignedFloorplanSummary();
      const actions = [
        {
          label: "🎯 前往「空间标定」手动框选车位（已带入此图）",
          primary: true,
          onClick: () => openCalibrationForCamera(camId, isSynthetic ? "" : dataUrl),
        },
      ];
      if (unassigned.count > 0) {
        actions.push({
          label: `前往平面图绑定待绑车位 (${unassigned.count}个)`,
          onClick: () => openFloorPlanCameraBinding(),
        });
      }
      if (altCam) {
        actions.push({
          label: `切换到「${altCam.name}」(${altCam.spot_count}车位) 测试`,
          onClick: async () => {
            const sel = document.getElementById("labCameraSelect");
            state.lab.userSelectedCam = true;
            sel.value = altCam.camera_id;
            await submitSnapshotDataUrl(dataUrl, sourceLabel, false);
          },
        });
      }
      setLabStatusBanner(
        "is-warn",
        `⚠️ 当前机位 <strong>${escapeHtml(
          camMeta.name || camId
        )}</strong> 尚未绑定车位，且该画面未检测到清晰地面车位标线，请点击右侧按钮在您上传的原图上直接手动框选车位，或先从态势平面图绑定车位组。`,
        actions
      );
      return;
    }

    const reqPayload = {
      image_base64: dataUrl,
      instant_confirm: autoBootstrappedCount > 0 ? true : instantConfirm,
      time_offset_seconds: simOffset,
      force_reanalyze: autoBootstrappedCount > 0 ? true : forceReanalyze,
      source_label: sourceLabel,
    };
    if (sandboxPreviewSpots && sandboxPreviewSpots.length > 0) {
      reqPayload.preview_spots = sandboxPreviewSpots;
    }

    const res = await api(`/api/cameras/${encodeURIComponent(camId)}/snapshot`, {
      method: "POST",
      body: JSON.stringify(reqPayload),
    });

    renderLabOverlayAndReport(dataUrl, spotData, res);
    await refreshOverview();

    const freeCnt = res.reports.filter(
      (r) => (r.fsm.effective_status || r.fsm.committed_status) === "FREE"
    ).length;
    const occCnt = res.reports.filter(
      (r) => (r.fsm.effective_status || r.fsm.committed_status) === "OCCUPIED"
    ).length;
    const blkCnt = res.reports.filter(
      (r) => (r.fsm.effective_status || r.fsm.committed_status) === "BLOCKED"
    ).length;
    const pendCnt = res.reports.filter((r) => Boolean(r.fsm.pending_status)).length;
    const calibCount = Number(camMeta.calibrated_spot_count || 0);
    const postActions = [];
    const pendInfo = pendCnt > 0 ? ` · 防抖待复核 <strong>${pendCnt}</strong>` : "";

    if (res.sandbox_preview) {
      const unassigned = getUnassignedFloorplanSummary();
      if (unassigned.count > 0) {
        postActions.push({
          label: `前往平面图绑定车位组 (${unassigned.count}个待绑)`,
          primary: true,
          onClick: () => openFloorPlanCameraBinding(),
        });
      }
      postActions.push({
        label: "上传实景参考图自动标定",
        primary: unassigned.count === 0,
        onClick: () => document.getElementById("labAutoCalibInput")?.click(),
      });
      postActions.push({
        label: "前往「空间标定」画实景框",
        onClick: () => openCalibrationForCamera(camId),
      });
      const unbindTip =
        unassigned.count > 0
          ? `（您平面图中的 <strong>${unassigned.count}</strong> 个待绑车位完好未动；如需实景联动，可在平面图把车位组绑定到该机位）`
          : "";
      setLabStatusBanner(
        "is-info",
        `🧪 内存沙盒自检识别完成（${escapeHtml(sourceLabel)}）：由于机位「${escapeHtml(
          camMeta.name || camId
        )}」当前绑定车位为 0，已用 <strong>${
          res.reports.length
        }</strong> 个内存沙盒车位完成验证 — 净余可停 <strong>${freeCnt}</strong> · 已停 <strong>${occCnt}</strong> · 占道 <strong>${blkCnt}</strong>${pendInfo}（耗时 ${
          res.elapsed_ms
        } ms）${unbindTip}`,
        postActions
      );
      return;
    }

    if (autoBootstrappedCount > 0) {
      postActions.push({
        label: "🎯 前往「空间标定」微调自动提取的车位框",
        primary: true,
        onClick: () => openCalibrationForCamera(camId, dataUrl),
      });
    } else if (!isSynthetic && calibCount === 0) {
      postActions.push({
        label: "✨ 用这张图自动对框建档",
        primary: true,
        onClick: () =>
          runAutoCalibrationWithDataUrl(
            camId,
            camMeta.zone_id || "Z_BLDG3_EAST",
            dataUrl,
            true
          ),
      });
      postActions.push({
        label: "🎯 用这张图去「空间标定」微调车位框",
        onClick: () => openCalibrationForCamera(camId, dataUrl),
      });
    }
    const bootstrapTip =
      autoBootstrappedCount > 0
        ? `（已自动从该画面提取并保存 <strong>${autoBootstrappedCount}</strong> 个车位框）`
        : !isSynthetic && calibCount === 0
        ? "（当前仍使用平面图默认占位框，若与实景车位位置有偏移可点击右侧一键对框）"
        : "";
    setLabStatusBanner(
      "is-ok",
      `✅ 抓拍识别完成（${escapeHtml(sourceLabel)}）：共检测 <strong>${
        res.reports.length
      }</strong> 个车位 — 净余可停 <strong>${freeCnt}</strong> · 已停 <strong>${occCnt}</strong> · 占道 <strong>${blkCnt}</strong>${pendInfo}（耗时 ${
        res.elapsed_ms
      } ms）${bootstrapTip}`,
      postActions
    );
  } catch (err) {
    setLabStatusBanner(
      "is-error",
      `❌ 抓拍测试失败（机位 <code>${escapeHtml(camId)}</code>）：${escapeHtml(err.message)}`
    );
  } finally {
    setLabBusy(false);
  }
}

function warpImageWithHomographyJS(img, hNorm, calW, calH) {
  if (
    !img ||
    !Array.isArray(hNorm) ||
    hNorm.length !== 3 ||
    !Array.isArray(hNorm[0]) ||
    calW < 16 ||
    calH < 16
  ) {
    return null;
  }
  const natW = img.naturalWidth || img.width || 1280;
  const natH = img.naturalHeight || img.height || 720;
  const scaleIn = Math.min(1.0, 1600 / Math.max(1, natW, natH));
  const srcW = Math.max(2, Math.round(natW * scaleIn));
  const srcH = Math.max(2, Math.round(natH * scaleIn));

  const srcCan = document.createElement("canvas");
  srcCan.width = srcW;
  srcCan.height = srcH;
  const srcCtx = srcCan.getContext("2d", { willReadFrequently: true });
  srcCtx.drawImage(img, 0, 0, srcW, srcH);
  const srcData = srcCtx.getImageData(0, 0, srcW, srcH).data;

  const dstW = Math.max(32, Math.round(calW));
  const dstH = Math.max(32, Math.round(calH));
  const dstCan = document.createElement("canvas");
  dstCan.width = dstW;
  dstCan.height = dstH;
  const dstCtx = dstCan.getContext("2d");
  const dstImg = dstCtx.createImageData(dstW, dstH);
  const dstData = dstImg.data;

  const h00 = Number(hNorm[0][0]) || 0;
  const h01 = Number(hNorm[0][1]) || 0;
  const h02 = Number(hNorm[0][2]) || 0;
  const h10 = Number(hNorm[1][0]) || 0;
  const h11 = Number(hNorm[1][1]) || 0;
  const h12 = Number(hNorm[1][2]) || 0;
  const h20 = Number(hNorm[2][0]) || 0;
  const h21 = Number(hNorm[2][1]) || 0;
  const h22 = Number(hNorm[2][2]) || 1;

  const maxSx = srcW - 1.001;
  const maxSy = srcH - 1.001;
  let dstIdx = 0;

  for (let y = 0; y < dstH; y++) {
    const row0 = h01 * y + h02;
    const row1 = h11 * y + h12;
    const row2 = h21 * y + h22;
    for (let x = 0; x < dstW; x++) {
      const w = h20 * x + row2;
      if (Math.abs(w) < 1e-8) {
        dstData[dstIdx] = 20;
        dstData[dstIdx + 1] = 24;
        dstData[dstIdx + 2] = 32;
        dstData[dstIdx + 3] = 255;
        dstIdx += 4;
        continue;
      }
      const invW = 1.0 / w;
      const u = (h00 * x + row0) * invW;
      const v = (h10 * x + row1) * invW;
      const fxRaw = u * (srcW - 1);
      const fyRaw = v * (srcH - 1);
      const outOfBounds = fxRaw < -0.5 || fxRaw > srcW - 0.5 || fyRaw < -0.5 || fyRaw > srcH - 0.5;
      const fx = fxRaw < 0 ? 0 : fxRaw > maxSx ? maxSx : fxRaw;
      const fy = fyRaw < 0 ? 0 : fyRaw > maxSy ? maxSy : fyRaw;
      const x0 = fx | 0;
      const y0 = fy | 0;
      const dx = fx - x0;
      const dy = fy - y0;
      const oneDx = 1.0 - dx;
      const oneDy = 1.0 - dy;
      const w00 = oneDx * oneDy;
      const w10 = dx * oneDy;
      const w01 = oneDx * dy;
      const w11 = dx * dy;

      const i00 = (y0 * srcW + x0) << 2;
      const i10 = i00 + 4;
      const i01 = i00 + (srcW << 2);
      const i11 = i01 + 4;

      const dim = outOfBounds ? 0.52 : 1.0;
      dstData[dstIdx] = (srcData[i00] * w00 + srcData[i10] * w10 + srcData[i01] * w01 + srcData[i11] * w11) * dim;
      dstData[dstIdx + 1] =
        (srcData[i00 + 1] * w00 + srcData[i10 + 1] * w10 + srcData[i01 + 1] * w01 + srcData[i11 + 1] * w11) * dim;
      dstData[dstIdx + 2] =
        (srcData[i00 + 2] * w00 + srcData[i10 + 2] * w10 + srcData[i01 + 2] * w01 + srcData[i11 + 2] * w11) * dim;
      dstData[dstIdx + 3] = 255;
      dstIdx += 4;
    }
  }
  dstCtx.putImageData(dstImg, 0, 0);
  return { img: dstCan, width: dstW, height: dstH, rectified: true };
}

function renderLabOverlayAndReport(dataUrl, spotData, res) {
  document.getElementById("labPreviewWrap").classList.remove("hidden");
  document.getElementById("labReportsWrap").classList.remove("hidden");
  const tplAlign = res.template_alignment || {};
  const cropTelemetry = tplAlign.crop_roi || {};
  let alignNote = "";
  const effZoom =
    typeof cropTelemetry.focal_zoom === "number"
      ? cropTelemetry.focal_zoom
      : typeof cropTelemetry.zoom_ratio === "number"
      ? cropTelemetry.zoom_ratio
      : null;
  const effInliers = cropTelemetry.sift_inliers ?? cropTelemetry.inliers ?? 0;
  const effNote = cropTelemetry.note || cropTelemetry.reason || "";
  if (cropTelemetry.rectified_by_homography) {
    const rotStr =
      typeof cropTelemetry.rotation_deg === "number"
        ? `${cropTelemetry.rotation_deg >= 0 ? "+" : ""}${cropTelemetry.rotation_deg.toFixed(1)}°`
        : "0°";
    const zoomStr = effZoom !== null ? `×${effZoom.toFixed(2)}` : "×1.00";
    alignNote += ` · 📍锚点单应矫正(${rotStr} · ${zoomStr})`;
  } else if (cropTelemetry.dynamic_adjusted) {
    alignNote +=
      cropTelemetry.dynamic_source === "vlm_constellation"
        ? " · AI动态ROI重框定"
        : " · 抗风动态ROI纠偏";
  }
  if (tplAlign.just_calibrated) {
    alignNote += " · 已建档设为参考模板";
  } else if (tplAlign.aligned) {
    const scaleTag =
      tplAlign.scale && Math.abs(Number(tplAlign.scale) - 1.0) >= 0.02
        ? ` ×${Number(tplAlign.scale).toFixed(2)}`
        : "";
    alignNote += ` · 刚性对齐 Δ${tplAlign.shift_px}px${scaleTag}`;
  } else if (tplAlign.active && !cropTelemetry.rectified_by_homography) {
    alignNote += " · 固定锚点基准已对齐";
  }

  document.getElementById("labTimingBadge").textContent = res.zero_spots_notice
    ? "0 个已标定车位 · 待绑定或手动框选"
    : `${res.elapsed_ms} ms · ${res.sandbox_preview ? "内存沙盒 · " : ""}0 字节落盘 · ${
        res.instant_confirm_mode ? "单帧直通" : `双帧防抖 (${res.debounce_seconds}s)`
      }${alignNote}`;

  const canvas = document.getElementById("labResultCanvas");
  const ctx = canvas.getContext("2d");
  const img = new Image();
  img.onload = () => {
    const calTargetW = spotData?.camera?.image_width || 880;
    const calTargetH = spotData?.camera?.image_height || 320;
    const activeCropRoi = res.effective_crop_roi || spotData?.camera?.crop_roi;
    let cropRes = null;
    if (res.zero_spots_notice) {
      cropRes = { img, width: img.naturalWidth, height: img.naturalHeight };
    } else if (
      cropTelemetry.rectified_by_homography &&
      Array.isArray(cropTelemetry.rectified_homography_norm)
    ) {
      cropRes = warpImageWithHomographyJS(
        img,
        cropTelemetry.rectified_homography_norm,
        calTargetW,
        calTargetH
      );
    }
    if (!cropRes) {
      cropRes = cropImageSourceIfNeeded(img, activeCropRoi, calTargetW, calTargetH);
    }
    const drawSrc = cropRes.img || img;
    const drawW = cropRes.width || img.naturalWidth;
    const drawH = cropRes.height || img.naturalHeight;
    canvas.width = drawW;
    canvas.height = drawH;
    ctx.drawImage(drawSrc, 0, 0, drawW, drawH);

    if (res.zero_spots_notice) {
      ctx.save();
      ctx.fillStyle = "rgba(15, 23, 42, 0.72)";
      const boxW = Math.min(drawW - 40, 620);
      const boxH = 76;
      const bx = (drawW - boxW) / 2;
      const by = Math.max(20, drawH - boxH - 24);
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(bx, by, boxW, boxH, 14);
      else ctx.rect(bx, by, boxW, boxH);
      ctx.fill();
      ctx.fillStyle = "#FFFFFF";
      ctx.font = "600 18px -apple-system, BlinkMacSystemFont, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("当前机位暂未绑定/标定车位 (0 车位)", drawW / 2, by + 32);
      ctx.fillStyle = "rgba(255,255,255,0.85)";
      ctx.font = "400 14px -apple-system, BlinkMacSystemFont, sans-serif";
      ctx.fillText("请点击上方提示条一键自动标定，或切换已绑车位的机位", drawW / 2, by + 56);
      ctx.restore();
      return;
    }

    const calW = spotData.camera.image_width || drawW;
    const calH = spotData.camera.image_height || drawH;
    const sx = drawW / calW;
    const sy = drawH / calH;

    const repMap = {};
    res.reports.forEach((r) => (repMap[r.spot_id] = r));

    spotData.spots.forEach((sp) => {
      const r = repMap[sp.spot_id];
      const basePoly = (r && r.effective_polygon) || sp.polygon;
      const scaled = basePoly.map(([x, y]) => [x * sx, y * sy]);
      const effSt = r ? r.fsm.effective_status || r.fsm.committed_status : sp.status;
      const obsSt = r ? r.observed_status || effSt : effSt;
      const pend = r ? r.fsm.pending_status : null;
      const obsType = r ? r.obstacle_type || r.fsm.obstacle_type || "none" : "none";

      let stroke = "#34C759";
      let fill = "rgba(52, 199, 89, 0.24)";
      let dash = null;
      if (pend === "FREE" || (pend && obsSt === "FREE")) {
        // Current frame ALREADY observed FREE while FSM holds conservative OCCUPIED/BLOCKED until Frame 2:
        // render translucent green/cyan fill + dashed purple debounce border so visual matches AI observation!
        stroke = "#5856D6";
        dash = [7, 4];
        fill =
          obsType === "movable"
            ? "rgba(0, 199, 190, 0.18)"
            : obsType === "pedestrian"
            ? "rgba(50, 173, 230, 0.18)"
            : "rgba(52, 199, 89, 0.16)";
      } else {
        if (effSt === "FREE" && obsType === "movable") {
          stroke = "#00C7BE";
          fill = "rgba(0, 199, 190, 0.25)";
        } else if (effSt === "FREE" && obsType === "pedestrian") {
          stroke = "#32ADE6";
          fill = "rgba(50, 173, 230, 0.25)";
        } else if (effSt === "OCCUPIED") {
          stroke = "#FF3B30";
          fill = "rgba(255, 59, 48, 0.26)";
        } else if (effSt === "BLOCKED") {
          stroke = "#FF9500";
          fill = "rgba(255, 149, 0, 0.28)";
        }
        if (pend) {
          stroke = "#5856D6";
          dash = [7, 4];
        }
      }

      drawPolygon(ctx, scaled, stroke, fill, 3, dash);
      const rawVehPoly = (r && r.effective_vehicle_polygon) || sp.vehicle_polygon;
      if (rawVehPoly && rawVehPoly.length >= 3) {
        const scaledVeh = rawVehPoly.map(([x, y]) => [x * sx, y * sy]);
        draw3DPrismEnvelope(
          ctx,
          scaled,
          scaledVeh,
          "rgba(56, 189, 248, 0.85)",
          "rgba(56, 189, 248, 0.05)",
          1.7,
          true
        );
      }
      const cx = scaled.reduce((s, p) => s + p[0], 0) / scaled.length;
      const cy = scaled.reduce((s, p) => s + p[1], 0) / scaled.length;
      let statusLabel = effSt;
      if (effSt === "FREE" && obsType === "movable") statusLabel = "可停(移开轻物)";
      else if (effSt === "FREE" && obsType === "pedestrian") statusLabel = "可停(活体让行)";
      let tag = `${sp.label} · ${statusLabel}`;
      if (pend === "FREE") {
        tag =
          obsType === "movable"
            ? `${sp.label} · 视觉可移物(待复核)`
            : `${sp.label} · 视觉FREE(待复核)`;
      } else if (pend === "OCCUPIED") {
        tag = `${sp.label} · 视觉有车(防抖扣减)`;
      } else if (pend === "BLOCKED") {
        tag = `${sp.label} · 视觉固障(防抖扣减)`;
      } else if (pend) {
        tag = `${sp.label} · 复核中(${pend})`;
      }
      drawPillLabel(ctx, cx, cy, tag);
    });

    // Render matched fixed ground positioning anchors (A1~A4) on labResultCanvas
    const matchedAnchors = Array.isArray(cropTelemetry.anchor_matches)
      ? cropTelemetry.anchor_matches
      : [];
    matchedAnchors.forEach((am, idx) => {
      const px = Array.isArray(am.cal_px)
        ? am.cal_px[0] * sx
        : Array.isArray(am.cal_xy)
        ? am.cal_xy[0] * drawW
        : null;
      const py = Array.isArray(am.cal_px)
        ? am.cal_px[1] * sy
        : Array.isArray(am.cal_xy)
        ? am.cal_xy[1] * drawH
        : null;
      if (px !== null && py !== null) {
        drawAnchorDiamondHandle(ctx, px, py, am.id || `A${idx + 1}`, "", false);
      }
    });
  };
  img.src = dataUrl;

  const obsBadges = {
    movable: '<span class="spot-chip chip-MOVABLE" style="margin-left:4px">可移轻物/散砖·可停</span>',
    pedestrian: '<span class="spot-chip chip-PEDESTRIAN" style="margin-left:4px">行人/小动物·让行</span>',
    immovable: '<span class="spot-chip chip-BLOCKED" style="margin-left:4px">固障/砖堆·禁停</span>',
  };

  const tbody = document.getElementById("labReportTbody");
  if (!res.reports || res.reports.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align:center;color:#6E6E73;padding:18px 12px;">
          当前机位暂无已标定/绑定的车位（0 车位）。请使用上方提示栏一键自动标定车位，或切换到已绑定车位的机位后再试。
        </td>
      </tr>
    `;
    return;
  }
  const pendingReleaseLabels = res.reports
    .filter((r) => r.fsm && r.fsm.pending_status === "FREE" && r.observed_status === "FREE")
    .map((r) => r.label);
  const pendingOccLabels = res.reports
    .filter(
      (r) =>
        r.fsm &&
        (r.fsm.pending_status === "OCCUPIED" || r.fsm.pending_status === "BLOCKED")
    )
    .map((r) => r.label);
  const dynBannerRow = cropTelemetry.rectified_by_homography
    ? `<tr>
        <td colspan="7" style="background:rgba(217,119,6,0.10);color:#92400E;font-size:12px;padding:8px 12px;border-bottom:1px solid rgba(217,119,6,0.24);">
          📍 <strong>固定定位锚点单应性透视还原已生效：</strong>检测到本张抓拍存在摄像头位移、倾斜（<strong>${
            typeof cropTelemetry.rotation_deg === "number"
              ? `${cropTelemetry.rotation_deg >= 0 ? "+" : ""}${cropTelemetry.rotation_deg.toFixed(1)}°`
              : "0°"
          }</strong>）或焦距缩放（<strong>×${
        effZoom !== null ? effZoom.toFixed(2) : "1.00"
      }</strong>${
        effInliers ? ` · 地面锚点与刚性环境特征 ${effInliers} 内点` : ""
      }），系统已将画面 <strong>1:1 透视反变换还原至空间标定基准系</strong>，${
        Array.isArray(cropTelemetry.anchor_matches) ? cropTelemetry.anchor_matches.length : 4
      } 个固定地面锚点与识别框完全重合。
        </td>
      </tr>`
    : cropTelemetry.dynamic_adjusted
    ? `<tr>
        <td colspan="7" style="background:rgba(14,116,144,0.08);color:#0E7490;font-size:12px;padding:7px 12px;border-bottom:1px solid rgba(14,116,144,0.18);">
          🧭 <strong>动态车位区域框定已生效：</strong>检测到本张照片与静态标定存在机位偏移或焦距变化，已通过 <strong>${escapeHtml(
            cropTelemetry.dynamic_source === "vlm_constellation"
              ? "AI 多模态定位 + 车位相对形状不变重投影"
              : "抗风除树叶多尺度刚性搜索"
          )}</strong> 自动重框定车位带${effNote ? `（${escapeHtml(effNote)}）` : ""}。
        </td>
      </tr>`
    : "";
  const pendingReleaseRow = pendingReleaseLabels.length
    ? `<tr>
        <td colspan="7" style="background:rgba(88,86,214,0.08);color:#3B389C;font-size:12px;padding:8px 12px;border-bottom:1px solid rgba(88,86,214,0.18);">
          ℹ️ <strong>车位 ${escapeHtml(
            pendingReleaseLabels.join("、")
          )}</strong> 本帧视觉已识别为 <strong>FREE</strong>（画布已用浅绿+紫色虚线框标示）；因上一帧处于占用态，「宁少勿多」${
        res.debounce_seconds
      }s 双帧防抖暂保持有效占用，待第 2 帧复核通过后释放为空位。若测试单张静态图，可切为<strong>「单帧直通验证」</strong>或勾选<strong>「模拟时间推进 +4 秒」</strong>再传一次立即释放。
        </td>
      </tr>`
    : "";
  const pendingOccRow = pendingOccLabels.length
    ? `<tr>
        <td colspan="7" style="background:rgba(245,158,11,0.09);color:#92400E;font-size:12px;padding:8px 12px;border-bottom:1px solid rgba(245,158,11,0.22);">
          ⏱️ <strong>车位 ${escapeHtml(
            pendingOccLabels.join("、")
          )}</strong> 刚从空位首帧检测到占用：系统已<strong>立即悲观扣减净余车位</strong>（绝不虚报空位），同时进入 <strong>${
        res.debounce_seconds
      }s 双帧防抖复核窗口</strong>以过滤过路车。手动单张上传看到「待复核」属正常首帧保护；连续视频流满 ${
        res.debounce_seconds
      }s 自动转正，手动测试勾选<strong>「模拟时间推进 +4 秒」</strong>再传一帧或选<strong>「单帧直通验证」</strong>即可立即落库。
        </td>
      </tr>`
    : "";

  const vlmWarnRow = res.vlm_fallback_error
    ? `<tr>
        <td colspan="7" style="background:rgba(220,38,38,0.08);color:#991B1B;font-size:12px;padding:8px 12px;border-bottom:1px solid rgba(220,38,38,0.20);">
          ⚠️ <strong>多模态 VLM 调用回退提示：</strong>尝试并行复核 ${
            res.vlm_attempted_spots || 0
          } 个车位时接口返回异常（<code>${escapeHtml(
        res.vlm_fallback_error
      )}</code>），本帧已安全降级沿用本地 OpenCV 判定。请检查顶部模型按钮中的 Endpoint / Deployment 配置。
        </td>
      </tr>`
    : res.low_light_scene
    ? `<tr>
        <td colspan="7" style="background:rgba(30,58,138,0.08);color:#1E3A8A;font-size:12px;padding:7px 12px;border-bottom:1px solid rgba(30,58,138,0.18);">
          🌙 <strong>夜间/低照度场景自动增强已触发：</strong>检测到当前画面照度偏暗，混合模式已自动压低纯本地阈值并并行交由多模态 VLM 复核（耗时 ${(
            (res.elapsed_ms || 0) / 1000
          ).toFixed(2)}s）。
        </td>
      </tr>`
    : "";

  tbody.innerHTML =
    vlmWarnRow +
    dynBannerRow +
    pendingReleaseRow +
    pendingOccRow +
    res.reports
      .map((r) => {
      const obsTag = obsBadges[r.obstacle_type] || "";
      const effSt = r.fsm.effective_status || r.fsm.committed_status;
      const posVerifiedBadge =
        r.fsm && r.fsm.transition === "COMMITTED_POSITION_VERIFIED"
          ? ` <span style="display:inline-block;padding:1px 6px;border-radius:4px;background:#DCFCE7;color:#166534;font-size:11px;font-weight:600">📍定位无位移直确</span>`
          : "";
      const motionLabel = r.motion && r.motion.intent_label
        ? `<div style="color:#1D4ED8;font-size:11.5px;margin-top:2px">🚗 ${escapeHtml(
            r.motion.intent_label
          )}</div>`
        : "";
      const hintLine = r.action_hint
        ? `<div style="color:#0F766E;font-weight:500;font-size:11.5px;margin-top:3px">💡 建议：${escapeHtml(
            r.action_hint
          )}</div>`
        : "";
      return `
      <tr>
        <td><strong>${escapeHtml(r.label)}</strong></td>
        <td>${escapeHtml(r.spot_type)}</td>
        <td>${(r.diff_score * 100).toFixed(1)}%</td>
        <td><code>${escapeHtml(r.stage_used)}</code></td>
        <td><span class="spot-chip chip-${r.observed_status}">${r.observed_status}</span>${obsTag} (${Math.round(
        r.confidence * 100
      )}%)</td>
        <td>
          <span class="spot-chip chip-${effSt}">${effSt}</span>${posVerifiedBadge}
          ${r.fsm.pending_status ? ` → <strong>待复核 ${r.fsm.pending_status}</strong>` : ""}
          <div style="color:#6E6E73;font-size:11.5px;margin-top:2px">${escapeHtml(r.fsm.note)}</div>
          ${motionLabel}
        </td>
        <td>${escapeHtml(r.reason)}${hintLine}</td>
      </tr>
    `;
    })
    .join("");
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ==================== MAINSTREAM CAMERA MANAGEMENT HUB & INGESTION/BINDING WIZARD ==================== */
const camHubState = {
  presetsLoaded: false,
  vendors: [],
  protocols: [],
  defaultIngestConfig: null,
  filter: "all",
  searchQuery: "",
  selectedVendor: "hikvision",
  previewSnapshots: {}, // camera_id -> dataUrl (session-only RAM preview)
  lastTestDataUrl: "",
  urlPreviewTimer: null,
};

const CLIENT_FALLBACK_PROTOCOLS = [
  { protocol: "rtsp", label: "RTSP 实时流抽帧 (H.264/H.265)", mode: "pull" },
  { protocol: "http_snapshot", label: "HTTP/HTTPS CGI/ISAPI 高清抓拍", mode: "pull" },
  { protocol: "onvif", label: "ONVIF 国际标准探测与抓拍", mode: "pull" },
  { protocol: "mjpeg", label: "HTTP MJPEG 视频流", mode: "pull" },
  { protocol: "hls", label: "HLS (.m3u8) 云网关/直播流", mode: "pull" },
  { protocol: "rtmp", label: "RTMP 流媒体拉流", mode: "pull" },
  { protocol: "ftp_push", label: "FTP 定时抓拍推送 (内存零落盘)", mode: "push" },
  { protocol: "http_push", label: "HTTP Webhook 抓拍直传", mode: "push" },
  { protocol: "usb", label: "本机 USB / UVC 采集卡", mode: "pull" },
];

async function ensureCameraPresetsLoaded() {
  if (camHubState.presetsLoaded && camHubState.vendors.length) return;
  try {
    const res = await api("/api/cameras/presets");
    camHubState.vendors = Array.isArray(res.vendors) ? res.vendors : [];
    camHubState.protocols = Array.isArray(res.protocols)
      ? res.protocols
      : CLIENT_FALLBACK_PROTOCOLS;
    camHubState.defaultIngestConfig = res.default_ingest_config || null;
    camHubState.presetsLoaded = true;
    renderCamHubVendorShowcase();
  } catch (err) {
    console.warn("Failed to load camera presets:", err);
    if (!camHubState.protocols.length) {
      camHubState.protocols = CLIENT_FALLBACK_PROTOCOLS;
    }
  }
}

function getVendorPresetById(vendorId) {
  return (
    camHubState.vendors.find((v) => v.vendor === vendorId) ||
    camHubState.vendors[0] || {
      vendor: vendorId || "onvif_generic",
      label: "ONVIF 通用网络摄像机",
      subtitle: "兼容标准 ONVIF / RTSP / HTTP 协议",
      badge: "通用标准",
      recommended_protocol: "onvif",
      supported_protocols: CLIENT_FALLBACK_PROTOCOLS.map((p) => p.protocol),
      defaults: { port: 80, rtsp_port: 554, onvif_port: 80, auth_mode: "digest", username: "admin", channel: 1, stream: "main" },
      tips: "支持标准 ONVIF 自动探测或直接填写 RTSP / HTTP 抓拍地址。",
    }
  );
}

function getProtocolSpecById(protoId) {
  const list = camHubState.protocols.length ? camHubState.protocols : CLIENT_FALLBACK_PROTOCOLS;
  return list.find((p) => p.protocol === protoId) || list[0];
}

function isPullProtocolClient(protoId) {
  const spec = getProtocolSpecById(protoId);
  if (!spec) return !["ftp_push", "http_push"].includes(protoId);
  if (typeof spec.is_pull === "boolean") return spec.is_pull;
  return spec.mode === "pull" || spec.mode === "active_pull";
}

function renderCamHubVendorShowcase() {
  const strip = document.getElementById("camHubVendorStrip");
  if (!strip || !camHubState.vendors.length) return;
  strip.innerHTML = camHubState.vendors
    .map(
      (v) => `
      <button type="button" class="cam-vendor-chip js-vendor-quick-create" data-vendor="${escapeHtml(
        v.vendor
      )}" title="${escapeHtml(v.subtitle || "")} · 点击一键接入">
        <span>${escapeHtml(v.label)}</span>
        <span class="cam-vendor-chip-badge">${escapeHtml(v.badge || v.recommended_protocol.toUpperCase())}</span>
      </button>`
    )
    .join("");
}

function renderCameraManagementHub(data) {
  const payload = data || state.overview;
  if (!payload) return;
  if (!camHubState.presetsLoaded) {
    ensureCameraPresetsLoaded();
  } else {
    renderCamHubVendorShowcase();
  }

  const cameras = payload.cameras || [];
  const zones = payload.zones || [];
  const spotGroups = payload.spot_groups || [];
  const unboundCamSet = new Set((payload.unbound_cameras || []).map((u) => u.camera_id));

  // Nav pill badge
  const navBadge = document.getElementById("navCamCountBadge");
  if (navBadge) {
    navBadge.textContent = String(cameras.length);
    navBadge.classList.toggle("hidden", cameras.length === 0);
  }

  let pullActiveCnt = 0;
  let pullOnlineCnt = 0;
  let boundCamCnt = 0;
  cameras.forEach((cam) => {
    const cfg = cam.ingest_config || {};
    const st = cam.ingest_status || {};
    const boundGroups = spotGroups.filter((g) => g.camera_id === cam.camera_id);
    const isBound = boundGroups.length > 0 && !unboundCamSet.has(cam.camera_id);
    if (isBound) boundCamCnt += 1;
    if (cfg.is_pull && cfg.enabled) {
      pullActiveCnt += 1;
      if (st.state === "online") pullOnlineCnt += 1;
    }
  });
  const unboundFloorGroups = spotGroups.filter((g) => !g.is_bound);
  const unboundCamCnt = Math.max(0, cameras.length - boundCamCnt);

  const elTotal = document.getElementById("camKpiTotal");
  const elTotalSub = document.getElementById("camKpiTotalSub");
  const elPull = document.getElementById("camKpiPullOnline");
  const elPullSub = document.getElementById("camKpiPullSub");
  const elBound = document.getElementById("camKpiBound");
  const elBoundSub = document.getElementById("camKpiBoundSub");
  const elUnbound = document.getElementById("camKpiUnbound");
  const elUnboundSub = document.getElementById("camKpiUnboundSub");

  if (elTotal) elTotal.textContent = String(cameras.length);
  if (elTotalSub) {
    const pushCnt = Math.max(0, cameras.length - pullActiveCnt);
    elTotalSub.textContent = `主动拉流 ${pullActiveCnt} 路 · 被动推送/待命 ${pushCnt} 路`;
  }
  if (elPull) elPull.textContent = `${pullOnlineCnt}/${pullActiveCnt}`;
  if (elPullSub) {
    const schedRunning = payload.camera_ingestor?.running !== false;
    elPullSub.textContent = schedRunning
      ? "后台零落盘拉流巡检引擎运行中"
      : "按需手动拉帧模式";
  }
  if (elBound) elBound.textContent = String(boundCamCnt);
  if (elBoundSub) {
    const boundGrpTotal = spotGroups.filter((g) => g.is_bound).length;
    elBoundSub.textContent = `已关联 ${boundGrpTotal} 组 2F 平面图车位带`;
  }
  if (elUnbound) elUnbound.textContent = String(unboundCamCnt + unboundFloorGroups.length);
  if (elUnboundSub) {
    elUnboundSub.textContent = `待绑机位 ${unboundCamCnt} 台 · 待接入平面车位组 ${unboundFloorGroups.length} 组`;
  }

  // Unbound Floor-Plan Spot Groups Quick Strip
  const unboundStrip = document.getElementById("camHubUnboundGroupsStrip");
  if (unboundStrip) {
    if (unboundFloorGroups.length > 0) {
      unboundStrip.classList.remove("hidden");
      unboundStrip.innerHTML = `
        <div style="font-size:12.5px;color:#92400E;">
          <strong>🗺️ 平面图上有 ${unboundFloorGroups.length} 组车位尚未接入摄像头：</strong>
          点选任一车位组即可一键新建或绑定主流摄像头
        </div>
        <div class="cam-unbound-groups-pills">
          ${unboundFloorGroups
            .map(
              (g) => `
              <button type="button" class="btn-apple btn-apple-tinted btn-apple-xs js-bind-unbound-floor-group"
                      data-group-code="${escapeHtml(g.group_code)}"
                      data-zone-id="${escapeHtml(g.zone_id || "")}">
                ＋ 为 ${escapeHtml(g.group_code)} (${escapeHtml(
                g.group_name || `${g.total_capacity || g.spot_count || 0}位`
              )}) 接入绑定相机
              </button>`
            )
            .join("")}
        </div>
      `;
    } else {
      unboundStrip.classList.add("hidden");
      unboundStrip.innerHTML = "";
    }
  }

  // Filter & render camera cards
  const grid = document.getElementById("camHubCardsGrid");
  if (!grid) return;

  const q = camHubState.searchQuery.trim().toLowerCase();
  const filtered = cameras.filter((cam) => {
    const cfg = cam.ingest_config || {};
    const boundGroups = spotGroups.filter((g) => g.camera_id === cam.camera_id);
    const isBound = boundGroups.length > 0 && !unboundCamSet.has(cam.camera_id);
    const isPull = Boolean(cfg.is_pull);

    if (camHubState.filter === "bound" && !isBound) return false;
    if (camHubState.filter === "unbound" && isBound) return false;
    if (camHubState.filter === "pull" && !isPull) return false;
    if (camHubState.filter === "push" && isPull) return false;

    if (!q) return true;
    const zName = zones.find((z) => z.zone_id === cam.zone_id)?.name || "";
    const hay = [
      cam.camera_id,
      cam.name,
      cam.group_code,
      cam.mount_address,
      zName,
      cfg.vendor,
      cfg.vendor_label,
      cfg.protocol,
      cfg.protocol_label,
      cfg.host,
      cfg.resolved_preview_masked,
      ...boundGroups.map((g) => `${g.group_code} ${g.group_name}`),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });

  if (!filtered.length) {
    grid.innerHTML = `
      <div class="bento-card" style="grid-column:1/-1;text-align:center;padding:34px 20px;">
        <p class="muted-p" style="margin:0 0 12px;">当前筛选条件下暂无摄像头机位。</p>
        <button type="button" class="btn-apple btn-apple-primary btn-apple-sm js-cam-empty-create">＋ 接入第一台主流摄像头</button>
      </div>`;
    return;
  }

  grid.innerHTML = filtered
    .map((cam) => {
      const cfg = cam.ingest_config || {};
      const st = cam.ingest_status || {};
      const boundGroups = spotGroups.filter((g) => g.camera_id === cam.camera_id);
      const isBound = boundGroups.length > 0 && !unboundCamSet.has(cam.camera_id);
      const zoneObj = zones.find((z) => z.zone_id === cam.zone_id);
      const allCamSpots = zones
        .flatMap((z) => z.spots || [])
        .filter((s) => s.camera_id === cam.camera_id);
      const totalSpots =
        allCamSpots.length ||
        boundGroups.reduce((a, g) => a + Number(g.total_capacity || g.spot_count || 0), 0) ||
        Number(cam.spot_count || 0);
      const freeSpots = allCamSpots.filter(
        (s) => getEffectiveSpotStatusClient(s) === "FREE"
      ).length;

      const vendorLabel = cfg.vendor_label || "4G 太阳能/FTP 相机";
      const protoShort = String(cfg.protocol || "ftp_push").toUpperCase();
      const isPull = Boolean(cfg.is_pull);

      let streamPillClass = "is-idle";
      let streamPillText = "○ 手动拉帧模式";
      if (!isPull) {
        streamPillClass = "is-push";
        streamPillText =
          cam.last_seen_at || st.last_ok_at
            ? "● 被动推送就绪 · 已收帧"
            : "● FTP/HTTP 待命接收";
      } else if (st.state === "online") {
        streamPillClass = "is-online";
        streamPillText = `● 在线${st.last_latency_ms ? ` · ${Math.round(st.last_latency_ms)}ms` : ""}${
          cfg.enabled ? ` (${cfg.poll_interval_sec || 10}s巡检)` : ""
        }`;
      } else if (st.state === "error") {
        streamPillClass = "is-error";
        streamPillText = "⚠ 拉流连接异常";
      } else if (cfg.enabled) {
        streamPillClass = "is-push";
        streamPillText = `◐ 自动巡检 (${cfg.poll_interval_sec || 10}s)`;
      }

      const endpointLine = isPull
        ? cfg.resolved_preview_masked || (cfg.host ? `${cfg.protocol}://${cfg.host}` : "待填写相机 IP 或流地址")
        : cfg.protocol === "http_push"
        ? `POST ${location.origin}/api/cameras/${cam.camera_id}/snapshot`
        : cfg.ftp_push_hint || `ftp://camera:***@${location.hostname || "127.0.0.1"}:2121/${cam.camera_id}/`;

      const lastTimeText = st.last_ok_at
        ? `最近抓拍 ${st.last_ok_at} · 累计 ${st.frames_pulled || 0} 帧`
        : cam.last_seen_at
        ? `最近活跃 ${cam.last_seen_at}`
        : "尚无实时入站帧";

      const errBanner =
        st.state === "error" && st.last_error
          ? `<div style="font-size:11.5px;color:#B91C1C;background:rgba(220,38,38,0.07);padding:6px 9px;border-radius:8px;">⚠️ ${escapeHtml(
              st.last_error
            )}</div>`
          : "";

      const boundHtml = boundGroups.length
        ? `<div class="cam-bind-box">
            <div style="font-size:12px;color:#065F46;">
              <strong>🗺️ 已绑车位带：</strong>
              ${boundGroups
                .map(
                  (g) =>
                    `<span class="grp-code-pill">${escapeHtml(g.group_code)}</span> ${escapeHtml(
                      g.group_name || ""
                    )}`
                )
                .join(" + ")}
              <span style="margin-left:6px;font-weight:700;color:#15803D;">净余 ${freeSpots}/${totalSpots} 位</span>
            </div>
            <div style="display:flex;gap:5px;align-items:center;">
              <button type="button" class="apple-mini-pill-btn js-cam-hub-config" data-cam-id="${escapeHtml(
                cam.camera_id
              )}">换绑</button>
              <button type="button" class="apple-mini-pill-btn js-cam-hub-unbind" data-cam-id="${escapeHtml(
                cam.camera_id
              )}">⛓️‍💥 解绑</button>
            </div>
          </div>`
        : `<div class="cam-bind-box is-unbound">
            <div style="font-size:12px;color:#92400E;">
              <strong>⏳ 尚未绑定 2F 平面图车位组</strong>（已标 ${totalSpots} 位 · 归属 ${escapeHtml(
            zoneObj?.name || cam.zone_id || "未设片区"
          )}）
            </div>
            <div style="display:flex;gap:5px;align-items:center;">
              <button type="button" class="apple-mini-pill-btn js-cam-hub-place-map" data-cam-id="${escapeHtml(
                cam.camera_id
              )}">🗺️ 去平面图摆位</button>
              <button type="button" class="apple-mini-pill-btn js-cam-hub-config" data-cam-id="${escapeHtml(
                cam.camera_id
              )}">立即绑定车位组</button>
            </div>
          </div>`;

      const snapUrl = camHubState.previewSnapshots[cam.camera_id] || "";
      const snapHtml = snapUrl
        ? `<div class="cam-card-snap-preview">
            <img src="${snapUrl}" alt="${escapeHtml(cam.name)} 最新实时抓拍" />
          </div>`
        : "";

      const standbyList = Array.isArray(cfg.standby_cameras) ? cfg.standby_cameras : [];
      const activeSlot = Number(st.active_slot ?? cfg.active_slot ?? 0);
      const failoverStripHtml = standbyList.length
        ? `<div class="cam-card-failover-strip ${activeSlot > 0 ? "is-failover-active" : ""}">
            <div>
              <strong>${
                activeSlot > 0
                  ? `🟠 冷备接管中 · 当前采集 #${activeSlot} (${escapeHtml(
                      standbyList[activeSlot - 1]?.label || `备用#${activeSlot}`
                    )})`
                  : `🛡️ 主备冷切就绪 (1主+${standbyList.length}备 · 备机零负载)`
              }</strong>
              ${
                st.failover_switches
                  ? `<span style="margin-left:6px;opacity:0.8;">累计切换 ${st.failover_switches} 次</span>`
                  : ""
              }
            </div>
            <div class="cam-slot-pills" title="点击可手动切换活跃采集槽位（每路槽位独立记忆标定框）">
              <button type="button" class="cam-slot-switch-pill js-cam-switch-slot ${
                activeSlot === 0 ? "is-active-slot" : ""
              }" data-cam-id="${escapeHtml(cam.camera_id)}" data-slot="0">#0 主机</button>
              ${standbyList
                .map(
                  (sb, idx) =>
                    `<button type="button" class="cam-slot-switch-pill js-cam-switch-slot ${
                      activeSlot === idx + 1 ? "is-active-slot" : ""
                    }" data-cam-id="${escapeHtml(cam.camera_id)}" data-slot="${idx + 1}">#${
                      idx + 1
                    } ${escapeHtml(sb.label || `备用${idx + 1}`)}</button>`
                )
                .join("")}
            </div>
          </div>`
        : "";

      return `
        <article class="cam-mgmt-card ${isBound ? "" : "is-unbound-card"}" data-cam-hub-id="${escapeHtml(
        cam.camera_id
      )}">
          <div class="cam-mgmt-top">
            <div class="cam-mgmt-title-wrap">
              <div class="cam-mgmt-brand-row">
                <span class="cam-brand-pill">${escapeHtml(vendorLabel)}</span>
                <span class="cam-proto-pill">${escapeHtml(protoShort)}</span>
                ${
                  zoneObj
                    ? `<span class="badge-pill">${escapeHtml(zoneObj.name)}</span>`
                    : ""
                }
              </div>
              <h3 class="cam-mgmt-title">${escapeHtml(cam.name || cam.camera_id)}</h3>
              <span class="cam-mgmt-id">${escapeHtml(cam.camera_id)}${
        cam.mount_address ? ` · 📍 ${escapeHtml(cam.mount_address)}` : ""
      }</span>
            </div>
            <span class="cam-stream-pill ${streamPillClass}">${escapeHtml(streamPillText)}</span>
          </div>

          <div class="cam-endpoint-strip">
            <div class="cam-endpoint-url">${escapeHtml(endpointLine)}</div>
            <div class="cam-endpoint-sub">
              <span>${escapeHtml(lastTimeText)}</span>
              <span>${
                cam.has_template || cam.has_vehicle_template
                  ? "★ 已建脱敏标定模板"
                  : "待传标定模板"
              } · ${Number(cam.calibrated_spot_count || 0)}/${totalSpots} 位已对框</span>
            </div>
          </div>

          ${errBanner}
          ${failoverStripHtml}
          ${boundHtml}
          ${snapHtml}

          <div class="cam-mgmt-actions">
            <div class="cam-mgmt-actions-left">
              <button type="button" class="btn-apple btn-apple-primary btn-apple-xs js-cam-hub-config"
                      data-cam-id="${escapeHtml(cam.camera_id)}">🔌 接入配置与绑定</button>
              ${
                isPull
                  ? `<button type="button" class="btn-apple btn-apple-tinted btn-apple-xs js-cam-hub-pull-now"
                             data-cam-id="${escapeHtml(cam.camera_id)}">📸 立即抓拍识别</button>`
                  : ""
              }
              <button type="button" class="btn-apple btn-apple-secondary btn-apple-xs js-cam-hub-calib"
                      data-cam-id="${escapeHtml(cam.camera_id)}">🎯 空间标定</button>
              <button type="button" class="btn-apple btn-apple-secondary btn-apple-xs js-cam-hub-lab"
                      data-cam-id="${escapeHtml(cam.camera_id)}">🧪 抓拍测试</button>
            </div>
            <div class="cam-mgmt-actions-right">
              <button type="button" class="apple-mini-pill-btn is-danger js-cam-hub-delete"
                      data-cam-id="${escapeHtml(cam.camera_id)}"
                      data-group-code="${escapeHtml(boundGroups[0]?.group_code || "")}">🗑</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function setCamHubFilter(filterVal) {
  camHubState.filter = filterVal || "all";
  document.querySelectorAll(".cam-filter-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.camFilter === camHubState.filter);
  });
  document.querySelectorAll(".cam-hub-kpi").forEach((k) => {
    k.classList.toggle("is-active", k.dataset.camFilter === camHubState.filter);
  });
  renderCameraManagementHub(state.overview);
}

async function triggerSingleCameraPullNow(cameraId, btnEl = null) {
  if (!cameraId) return;
  const origTxt = btnEl ? btnEl.textContent : "";
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = "抓拍中…";
  }
  try {
    const res = await api(`/api/cameras/${encodeURIComponent(cameraId)}/pull-now`, {
      method: "POST",
      body: JSON.stringify({ process_vision: true, save_reference_frame: true }),
    });
    if (res.preview_jpeg_b64) {
      camHubState.previewSnapshots[cameraId] = `data:image/jpeg;base64,${res.preview_jpeg_b64}`;
    }
    await refreshOverview();
    const spotCnt = Array.isArray(res.vision?.spot_results) ? res.vision.spot_results.length : 0;
    showAppleToast(
      `📸 机位 ${cameraId} 实时抓拍完成 (${res.width}×${res.height} · 已识别 ${spotCnt} 个车位，原图零落盘销毁)`
    );
  } catch (err) {
    await refreshOverview();
    showAppleToast(`抓拍失败 (${cameraId})：${err.message}`, "error", 4800);
  } finally {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = origTxt;
    }
  }
}

async function runLanCameraDiscovery() {
  const btn = document.getElementById("btnRunCamDiscover");
  const out = document.getElementById("camDiscoverResults");
  const hostsRaw = document.getElementById("camDiscoverTargetHosts")?.value?.trim() || "";
  const target_hosts = hostsRaw
    .split(/[,，;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (btn) {
    btn.disabled = true;
    btn.textContent = "正在扫描局域网…";
  }
  if (out) {
    out.innerHTML = `<div class="subtle-text">正在发送 ONVIF WS-Discovery 多播报文${
      target_hosts.length ? `并探测 ${target_hosts.length} 个指定主机` : ""
    }…</div>`;
  }
  try {
    const res = await api("/api/cameras/discover", {
      method: "POST",
      body: JSON.stringify({ timeout_sec: 1.6, target_hosts }),
    });
    const devices = res.devices || [];
    if (!devices.length) {
      if (out) {
        out.innerHTML = `<div class="subtle-text">未在当前子网发现开启 WS-Discovery 广播的设备。若相机跨网段或关闭了多播，可在上方输入相机 IP 后再点扫描，或直接点击右上角「＋ 接入主流摄像头」填写 IP。</div>`;
      }
      return;
    }
    if (out) {
      out.innerHTML = devices
        .map(
          (d, idx) => `
          <div class="cam-discovered-item">
            <div style="min-width:0;">
              <strong style="font-size:13px;color:#0F172A;">📹 ${escapeHtml(
                d.vendor_label || d.name || d.ip
              )}</strong>
              <div style="font-size:11.5px;color:#475569;font-family:ui-monospace,monospace;">
                IP: ${escapeHtml(d.ip)}:${escapeHtml(d.onvif_port || 80)}
              </div>
              <div style="font-size:11px;color:#64748B;">${escapeHtml(
                d.model || d.hardware || d.service_url || "ONVIF 网络摄像机"
              )}</div>
            </div>
            <button type="button" class="btn-apple btn-apple-primary btn-apple-xs js-adopt-discovered-cam"
                    data-disc-idx="${idx}">一键接入</button>
          </div>`
        )
        .join("");
      out.querySelectorAll(".js-adopt-discovered-cam").forEach((b) => {
        b.addEventListener("click", () => {
          const dev = devices[Number(b.dataset.discIdx) || 0];
          if (dev) {
            openCameraConfigModal("create", "", { discoveredDevice: dev });
          }
        });
      });
    }
  } catch (err) {
    if (out) {
      out.innerHTML = `<div style="color:#B91C1C;font-size:12px;">局域网扫描异常：${escapeHtml(
        err.message
      )}</div>`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "开始扫描";
    }
  }
}

function collectWizardIngestConfig() {
  const protocol = document.getElementById("camCfgProtocolSelect")?.value || "rtsp";
  const isUsb = protocol === "usb";
  const pollVal = isUsb
    ? Number(document.getElementById("camCfgUsbPollSecInput")?.value || 10)
    : Number(document.getElementById("camCfgPollSecInput")?.value || 10);
  return {
    vendor: camHubState.selectedVendor || "hikvision",
    protocol,
    enabled: Boolean(document.getElementById("camCfgIngestEnabled")?.checked),
    host: document.getElementById("camCfgHostInput")?.value?.trim() || "",
    port: Number(document.getElementById("camCfgPortInput")?.value || 80),
    rtsp_port:
      protocol === "rtsp"
        ? Number(document.getElementById("camCfgPortInput")?.value || 554)
        : 554,
    onvif_port: Number(document.getElementById("camCfgOnvifPortInput")?.value || 80),
    username: document.getElementById("camCfgUsernameInput")?.value?.trim() || "",
    password: document.getElementById("camCfgPasswordInput")?.value || "",
    auth_mode: document.getElementById("camCfgAuthModeSelect")?.value || "digest",
    channel: Number(document.getElementById("camCfgChannelInput")?.value || 1),
    stream: document.getElementById("camCfgStreamSelect")?.value || "main",
    rtsp_transport: document.getElementById("camCfgRtspTransportSelect")?.value || "tcp",
    poll_interval_sec: Math.max(2, pollVal || 10),
    custom_url: document.getElementById("camCfgCustomUrlInput")?.value?.trim() || "",
    usb_index: Number(document.getElementById("camCfgUsbIndexInput")?.value || 0),
    push_token: document.getElementById("camCfgPushTokenInput")?.value?.trim() || "",
    failover_enabled: Boolean(document.getElementById("camCfgFailoverEnabled")?.checked),
    failover_threshold: Math.max(
      1,
      Math.min(20, Number(document.getElementById("camCfgFailoverThreshold")?.value || 3))
    ),
    auto_recover_primary: Boolean(document.getElementById("camCfgAutoRestorePrimary")?.checked),
    primary_probe_interval_sec: Math.max(
      15,
      Math.min(3600, Number(document.getElementById("camCfgPrimaryProbeSec")?.value || 180))
    ),
    active_slot: Number(camHubState.editingActiveSlot || 0),
    standby_cameras: collectWizardStandbySlots(),
  };
}

function collectWizardStandbySlots() {
  const container = document.getElementById("camCfgStandbySlotsList");
  if (!container) return [];
  const cards = [...container.querySelectorAll(".cam-standby-slot-card")];
  return cards.slice(0, 3).map((card, idx) => {
    const protocol = card.querySelector(".js-sb-protocol")?.value || "rtsp";
    const portVal = Number(card.querySelector(".js-sb-port")?.value || (protocol === "rtsp" ? 554 : 80));
    return {
      slot_id: idx + 1,
      label: card.querySelector(".js-sb-label")?.value?.trim() || `备用#${idx + 1}`,
      vendor: card.querySelector(".js-sb-vendor")?.value || "hikvision",
      protocol,
      host: card.querySelector(".js-sb-host")?.value?.trim() || "",
      port: protocol === "rtsp" ? 80 : portVal,
      rtsp_port: protocol === "rtsp" ? portVal : 554,
      username: card.querySelector(".js-sb-username")?.value?.trim() || "admin",
      password: card.querySelector(".js-sb-password")?.value || "",
      auth_mode: "digest",
      channel: 1,
      stream: "main",
      rtsp_transport: "tcp",
      custom_url: card.querySelector(".js-sb-custom-url")?.value?.trim() || "",
    };
  });
}

function renderWizardStandbySlots(slots) {
  const container = document.getElementById("camCfgStandbySlotsList");
  const addBtn = document.getElementById("btnCamAddStandbySlot");
  if (!container) return;
  const list = Array.isArray(slots) ? slots.slice(0, 3) : [];
  camHubState.wizardStandbySlots = list;
  if (addBtn) {
    addBtn.disabled = list.length >= 3;
    addBtn.textContent = list.length >= 3 ? "已达上限 (最多 3 备)" : "➕ 添加备用摄像头";
  }
  if (!list.length) {
    container.innerHTML = `<div class="cam-standby-empty">当前为单摄像头直连模式。若同一车位带安装了第 2/第 3 个冗余摄像头（如物业监控 + 业主阳台窗机），可点击右上角「➕ 添加备用摄像头」配置冷备兜底。</div>`;
    return;
  }
  const vendorOpts = (camHubState.vendors || [])
    .map((v) => ({ value: v.vendor, label: v.label }));
  const protoOpts = [
    { value: "rtsp", label: "RTSP 视频流抓帧 (554)" },
    { value: "http_snapshot", label: "HTTP/ISAPI 快照 (80)" },
    { value: "onvif", label: "ONVIF 标准快照 (80)" },
    { value: "mjpeg", label: "MJPEG 实时流" },
  ];
  container.innerHTML = list
    .map((sb, idx) => {
      const proto = sb.protocol || "rtsp";
      const port = proto === "rtsp" ? sb.rtsp_port || 554 : sb.port || 80;
      const hasPwd = Boolean(sb.has_password);
      return `
      <div class="cam-standby-slot-card" data-sb-index="${idx}">
        <div class="cam-standby-slot-head">
          <div>
            <span class="cam-standby-slot-badge">🛡️ 冷备槽位 #${idx + 1}</span>
            <span style="font-size:11.5px;color:#64748B;margin-left:6px;">平时休眠零带宽 · 仅前序机位断流时唤醒接管</span>
          </div>
          <div style="display:flex;gap:6px;">
            <button type="button" class="btn-apple btn-apple-ghost btn-apple-xs js-cam-test-standby" data-sb-index="${idx}">⚡ 测通此备机</button>
            <button type="button" class="btn-apple btn-apple-ghost btn-apple-xs js-cam-remove-standby" data-sb-index="${idx}" style="color:#B91C1C;">移除</button>
          </div>
        </div>
        <div class="field-grid-3">
          <div class="field">
            <label>备机备注名称</label>
            <input type="text" class="js-sb-label" value="${escapeHtml(sb.label || `备用摄像头#${idx + 1}`)}" placeholder="如 西侧灯杆备机" />
          </div>
          <div class="field">
            <label>品牌预设</label>
            <select class="js-sb-vendor">
              ${vendorOpts
                .map(
                  (vo) =>
                    `<option value="${escapeHtml(vo.value)}" ${
                      vo.value === (sb.vendor || "hikvision") ? "selected" : ""
                    }>${escapeHtml(vo.label)}</option>`
                )
                .join("")}
            </select>
          </div>
          <div class="field">
            <label>接入协议</label>
            <select class="js-sb-protocol">
              ${protoOpts
                .map(
                  (po) =>
                    `<option value="${escapeHtml(po.value)}" ${
                      po.value === proto ? "selected" : ""
                    }>${escapeHtml(po.label)}</option>`
                )
                .join("")}
            </select>
          </div>
        </div>
        <div class="field-grid-4">
          <div class="field">
            <label>备机 IP / 主机</label>
            <input type="text" class="js-sb-host" value="${escapeHtml(sb.host || "")}" placeholder="192.168.1.65" />
          </div>
          <div class="field">
            <label>端口</label>
            <input type="number" class="js-sb-port" value="${Number(port)}" />
          </div>
          <div class="field">
            <label>账号</label>
            <input type="text" class="js-sb-username" value="${escapeHtml(sb.username ?? "admin")}" />
          </div>
          <div class="field">
            <label>密码 ${hasPwd ? `<span class="saved-key-pill">已存</span>` : ""}</label>
            <input type="password" class="js-sb-password" value="" placeholder="${
              hasPwd ? "留空保持原密码" : "输入备机密码"
            }" />
          </div>
        </div>
        <div class="field">
          <label>完整自定义流/抓拍 URL（可选，填写后优先使用）</label>
          <input type="text" class="js-sb-custom-url" value="${escapeHtml(sb.custom_url || "")}" placeholder="如 rtsp://admin:12345@192.168.1.65:554/Streaming/Channels/101" />
        </div>
      </div>`;
    })
    .join("");
}

function renderWizardVendorGrid() {
  const grid = document.getElementById("camCfgVendorGrid");
  if (!grid) return;
  const list = camHubState.vendors;
  grid.innerHTML = list
    .map(
      (v) => `
      <button type="button" class="cam-wiz-vendor-card ${
        v.vendor === camHubState.selectedVendor ? "is-selected" : ""
      }" data-wiz-vendor="${escapeHtml(v.vendor)}">
        <div class="cam-wiz-vendor-top">
          <strong>${escapeHtml(v.label)}</strong>
          <span class="cam-vendor-chip-badge">${escapeHtml(v.badge || "")}</span>
        </div>
        <span class="cam-wiz-vendor-sub">${escapeHtml(v.subtitle || "")}</span>
      </button>`
    )
    .join("");
}

function applyVendorSelectionToWizard(vendorId, applyDefaultPorts = true) {
  camHubState.selectedVendor = vendorId;
  renderWizardVendorGrid();
  const v = getVendorPresetById(vendorId);
  const protoSel = document.getElementById("camCfgProtocolSelect");
  const allProtos = camHubState.protocols.length
    ? camHubState.protocols
    : CLIENT_FALLBACK_PROTOCOLS;
  const supported = new Set(v.supported_protocols || allProtos.map((p) => p.protocol));

  if (protoSel) {
    const prevProto = protoSel.value;
    protoSel.innerHTML = allProtos
      .filter((p) => supported.has(p.protocol))
      .map(
        (p) =>
          `<option value="${escapeHtml(p.protocol)}">${escapeHtml(p.label)}${
            p.protocol === v.recommended_protocol ? " ★ 推荐" : ""
          }</option>`
      )
      .join("");
    if (applyDefaultPorts || !supported.has(prevProto)) {
      protoSel.value = v.recommended_protocol || protoSel.options[0]?.value || "rtsp";
    } else {
      protoSel.value = prevProto;
    }
  }

  const tipEl = document.getElementById("camCfgVendorTipBanner");
  if (tipEl) {
    tipEl.innerHTML = `💡 <strong>${escapeHtml(v.label)}接入提示：</strong>${escapeHtml(
      v.tips || v.subtitle || ""
    )}`;
  }

  if (applyDefaultPorts && v.defaults) {
    const curProto = protoSel?.value || v.recommended_protocol || "rtsp";
    const portInput = document.getElementById("camCfgPortInput");
    if (portInput) {
      portInput.value =
        curProto === "rtsp"
          ? v.defaults.rtsp_port || 554
          : curProto === "rtmp"
          ? 1935
          : v.defaults.port || 80;
    }
    const onvifPortInput = document.getElementById("camCfgOnvifPortInput");
    if (onvifPortInput) onvifPortInput.value = v.defaults.onvif_port || 80;
    const authSel = document.getElementById("camCfgAuthModeSelect");
    if (authSel && v.defaults.auth_mode) authSel.value = v.defaults.auth_mode;
    const userInp = document.getElementById("camCfgUsernameInput");
    if (userInp && !userInp.value && v.defaults.username !== undefined) {
      userInp.value = v.defaults.username;
    }
    const mountSel = document.getElementById("camCfgMountTypeSelect");
    if (mountSel && vendorId === "solar_4g") {
      mountSel.value = "solar_4g";
    }
  }

  syncWizardProtocolVisibility(applyDefaultPorts);
  scheduleWizardUrlPreviewUpdate();
}

function syncWizardProtocolVisibility(adjustDefaultPort = false) {
  const proto = document.getElementById("camCfgProtocolSelect")?.value || "rtsp";
  const v = getVendorPresetById(camHubState.selectedVendor);
  const isUsb = proto === "usb";
  const isPush = proto === "ftp_push" || proto === "http_push";
  const isNetPull = !isUsb && !isPush;

  document.getElementById("camCfgPullFieldsWrap")?.classList.toggle("hidden", !isNetPull);
  document.getElementById("camCfgUsbFieldsWrap")?.classList.toggle("hidden", !isUsb);
  document.getElementById("camCfgPushFieldsWrap")?.classList.toggle("hidden", !isPush);
  document.getElementById("camCfgEnabledToggleWrap")?.classList.toggle("hidden", isPush);

  const portLabel = document.getElementById("camCfgPortLabel");
  if (portLabel) {
    portLabel.textContent =
      proto === "rtsp"
        ? "RTSP 端口 (默认554)"
        : proto === "onvif"
        ? "HTTP/抓拍端口"
        : proto === "rtmp"
        ? "RTMP 端口 (1935)"
        : "HTTP/HTTPS 端口";
  }

  if (adjustDefaultPort && v?.defaults) {
    const portInput = document.getElementById("camCfgPortInput");
    if (portInput) {
      portInput.value =
        proto === "rtsp"
          ? v.defaults.rtsp_port || 554
          : proto === "rtmp"
          ? 1935
          : v.defaults.port || 80;
    }
  }

  updatePushEndpointHintsInWizard();
}

function updatePushEndpointHintsInWizard() {
  const camId =
    document.getElementById("camCfgIdInput")?.value?.trim() ||
    document.getElementById("camCfgOriginalId")?.value?.trim() ||
    "CAM_NEW";
  const host = location.hostname || "127.0.0.1";
  const ftpPort = state.overview?.config?.ftp_port || 2121;
  const token = document.getElementById("camCfgPushTokenInput")?.value?.trim() || "";
  const ftpEl = document.getElementById("camCfgFtpHintCode");
  const httpEl = document.getElementById("camCfgHttpPushCode");
  if (ftpEl) {
    ftpEl.textContent = `ftp://camera:parking2026@${host}:${ftpPort}/${camId}/`;
  }
  if (httpEl) {
    const q = token ? `?token=${encodeURIComponent(token)}` : "";
    httpEl.textContent = `POST ${location.origin}/api/cameras/${camId}/snapshot${q}`;
  }
}

function scheduleWizardUrlPreviewUpdate() {
  clearTimeout(camHubState.urlPreviewTimer);
  camHubState.urlPreviewTimer = setTimeout(async () => {
    const camId =
      document.getElementById("camCfgIdInput")?.value?.trim() || "CAM_PREVIEW";
    const ingest_config = collectWizardIngestConfig();
    const badge = document.getElementById("camCfgSynthesizedBadge");
    if (badge) {
      badge.textContent = ingest_config.custom_url
        ? "使用自定义完整流地址"
        : "按品牌模板自动生成";
    }
    updatePushEndpointHintsInWizard();
    try {
      const res = await api("/api/cameras/preview-url", {
        method: "POST",
        body: JSON.stringify({ camera_id: camId, ingest_config }),
      });
      const outEl = document.getElementById("camCfgResolvedUrlText");
      if (outEl && res.ingest_config) {
        outEl.textContent =
          res.ingest_config.resolved_preview_masked ||
          "填写摄像头 IP 地址后将自动合成完整流地址…";
      }
    } catch (_) {}
  }, 150);
}

function updateWizardRuleNamingPreview(forceFill = false) {
  const grpRaw = document.getElementById("camCfgGroupCodeInput")?.value?.trim() || "";
  const addrRaw = document.getElementById("camCfgMountAddrInput")?.value?.trim() || "";
  const nameInp = document.getElementById("camCfgNameInput");
  const idInp = document.getElementById("camCfgIdInput");
  const hintBox = document.getElementById("camCfgRuleHint");
  const mode = document.getElementById("camCfgMode")?.value || "create";

  if (!grpRaw) {
    if (hintBox) {
      hintBox.innerHTML =
        "💡 选择上方「2F 平面图车位组」或输入规则码（如 <code>5BV7</code> = 5号楼后侧竖排7个车位），系统会自动生成标准名称与代号。";
    }
    updatePushEndpointHintsInWizard();
    return;
  }

  const parsed = parseCameraGroupCodesClient(grpRaw, addrRaw);
  const suggestedName = parsed.suggested_name || parsed.display_name || "";
  const normCode = parsed.normalized_group_code || parsed.group_code || grpRaw.toUpperCase();
  if (parsed.valid) {
    if (hintBox) {
      hintBox.innerHTML = `✅ <strong>编码解析（共 ${parsed.total_spots} 个车位）</strong>：${escapeHtml(
        suggestedName
      )} · 标准组码 <code>${escapeHtml(normCode)}</code>`;
    }
    if (nameInp && (forceFill || !nameInp.dataset.manual)) {
      nameInp.value = suggestedName;
    }
    if (idInp && mode === "create" && (forceFill || !idInp.dataset.manual)) {
      idInp.value = parsed.suggested_camera_id || `CAM_${normCode.replace(/\+/g, "_")}`;
    }
  } else if (hintBox) {
    hintBox.innerHTML = `ℹ️ 自定义组码 <code>${escapeHtml(
      grpRaw
    )}</code>（如需按楼栋方位自动命名，可填如 <code>5LV7</code>、<code>10BH2</code>）。`;
  }
  updatePushEndpointHintsInWizard();
}

async function openCameraConfigModal(mode = "create", cameraId = "", opts = {}) {
  await ensureCameraPresetsLoaded();
  const modal = document.getElementById("cameraConfigModal");
  if (!modal) return;

  const data = state.overview || (await refreshOverview(), state.overview);
  const cameras = data?.cameras || [];
  const zones = data?.zones || [];
  const units = data?.units || [];
  const spotGroups = data?.spot_groups || [];

  const cam = mode === "edit" ? cameras.find((c) => c.camera_id === cameraId) : null;
  if (mode === "edit" && !cam) {
    showAppleToast(`未找到摄像头机位 ${cameraId}`, "warn");
    return;
  }

  document.getElementById("camCfgMode").value = mode;
  document.getElementById("camCfgOriginalId").value = cam ? cam.camera_id : "";
  document.getElementById("camCfgModalTitle").textContent = cam
    ? `摄像头接入配置与平面图绑定 · ${cam.name || cam.camera_id}`
    : "接入主流摄像头并绑定平面图车位组";
  setModalFeedback("camCfgModalFeedback", true, "");

  // Reset live test preview stage
  camHubState.lastTestDataUrl = "";
  document.getElementById("camCfgPreviewStage")?.classList.add("hidden");
  document.getElementById("camCfgOnvifResultBox")?.classList.add("hidden");
  const testPill = document.getElementById("camCfgTestStatusPill");
  if (testPill) {
    testPill.className = "tpl-pill tpl-pill-idle";
    testPill.textContent = "尚未测通";
  }

  // Populate Zone & Near-Unit selects
  const zoneSel = document.getElementById("camCfgZoneSelect");
  if (zoneSel) {
    zoneSel.innerHTML = zones
      .map(
        (z) =>
          `<option value="${escapeHtml(z.zone_id)}">${escapeHtml(z.name)} (${escapeHtml(
            z.zone_id
          )})</option>`
      )
      .join("");
  }
  const unitSel = document.getElementById("camCfgNearUnitSelect");
  if (unitSel) {
    unitSel.innerHTML =
      `<option value="">🏢 保持/自动匹配最近单元门</option>` +
      units
        .map(
          (u) =>
            `<option value="${escapeHtml(u.unit_id)}">🚪 ${escapeHtml(u.name)} (${escapeHtml(
              u.unit_id
            )})</option>`
        )
        .join("");
  }

  // Populate 2F Floor-Plan Spot Group Binding dropdown
  const bindSel = document.getElementById("camCfgBindGroupSelect");
  const currentBoundGroups = cam
    ? spotGroups.filter((g) => g.camera_id === cam.camera_id)
    : [];
  const currentPrimaryBoundCode = currentBoundGroups[0]?.group_code || "";

  if (bindSel) {
    const unboundGroups = spotGroups.filter((g) => !g.is_bound);
    const boundToSelf = spotGroups.filter(
      (g) => g.is_bound && cam && g.camera_id === cam.camera_id
    );
    const boundToOthers = spotGroups.filter(
      (g) => g.is_bound && (!cam || g.camera_id !== cam.camera_id)
    );

    let html = `<option value="">⏳ 暂不绑定平面图车位组（仅建档机位或稍后去平面图摆位）</option>`;
    if (boundToSelf.length) {
      html += `<optgroup label="✅ 当前已绑定的平面图车位组">` +
        boundToSelf
          .map(
            (g) =>
              `<option value="${escapeHtml(g.group_code)}">✅ ${escapeHtml(
                g.group_code
              )} · ${escapeHtml(g.group_name || "")} (${g.total_capacity || g.spot_count || 0}个车位 · 当前绑定)</option>`
          )
          .join("") +
        `</optgroup>`;
    }
    if (unboundGroups.length) {
      html += `<optgroup label="🟡 平面图待绑定车位组（推荐直接选用）">` +
        unboundGroups
          .map(
            (g) =>
              `<option value="${escapeHtml(g.group_code)}">🟡 ${escapeHtml(
                g.group_code
              )} · ${escapeHtml(g.group_name || "待接入车位带")} (${
                g.total_capacity || g.spot_count || 0
              }个车位 · 待绑定)</option>`
          )
          .join("") +
        `</optgroup>`;
    }
    if (boundToOthers.length) {
      html += `<optgroup label="🔄 已绑其他机位的车位组（选择将换绑至本摄像头）">` +
        boundToOthers
          .map(
            (g) =>
              `<option value="${escapeHtml(g.group_code)}">🔄 ${escapeHtml(
                g.group_code
              )} · ${escapeHtml(g.group_name || "")} (当前绑: ${escapeHtml(
                g.camera_id
              )})</option>`
          )
          .join("") +
        `</optgroup>`;
    }
    bindSel.innerHTML = html;
  }

  const nameInp = document.getElementById("camCfgNameInput");
  const idInp = document.getElementById("camCfgIdInput");
  const grpInp = document.getElementById("camCfgGroupCodeInput");
  const addrInp = document.getElementById("camCfgMountAddrInput");
  const pwdInp = document.getElementById("camCfgPasswordInput");
  const pwdPill = document.getElementById("camCfgPwdSavedPill");
  const tokInp = document.getElementById("camCfgPushTokenInput");
  const tokPill = document.getElementById("camCfgTokenSavedPill");
  const unbindQuickBtn = document.getElementById("btnCamCfgUnbindQuick");

  if (cam) {
    const cfg = cam.ingest_config || {};
    applyVendorSelectionToWizard(cfg.vendor || "hikvision", false);
    const protoSel = document.getElementById("camCfgProtocolSelect");
    if (protoSel && cfg.protocol) {
      if (![...protoSel.options].some((o) => o.value === cfg.protocol)) {
        applyVendorSelectionToWizard("custom", false);
      }
      protoSel.value = cfg.protocol;
    }
    document.getElementById("camCfgMountTypeSelect").value = cam.mount_type || "high_pole";
    document.getElementById("camCfgIngestEnabled").checked = Boolean(cfg.enabled);
    document.getElementById("camCfgHostInput").value = cfg.host || "";
    document.getElementById("camCfgPortInput").value =
      cfg.protocol === "rtsp" ? cfg.rtsp_port || 554 : cfg.port || 80;
    document.getElementById("camCfgOnvifPortInput").value = cfg.onvif_port || 80;
    document.getElementById("camCfgAuthModeSelect").value = cfg.auth_mode || "digest";
    document.getElementById("camCfgUsernameInput").value = cfg.username ?? "admin";
    if (pwdInp) {
      pwdInp.value = "";
      pwdInp.placeholder = cfg.has_password
        ? "已加密保存（留空保持原密码不变）"
        : "输入相机密码或机身验证码";
    }
    pwdPill?.classList.toggle("hidden", !cfg.has_password);
    document.getElementById("camCfgChannelInput").value = cfg.channel || 1;
    document.getElementById("camCfgStreamSelect").value = cfg.stream || "main";
    document.getElementById("camCfgPollSecInput").value = cfg.poll_interval_sec || 10;
    document.getElementById("camCfgUsbPollSecInput").value = cfg.poll_interval_sec || 10;
    document.getElementById("camCfgRtspTransportSelect").value = cfg.rtsp_transport || "tcp";
    document.getElementById("camCfgCustomUrlInput").value = cfg.custom_url || "";
    document.getElementById("camCfgUsbIndexInput").value = cfg.usb_index || 0;
    if (tokInp) {
      tokInp.value = "";
      tokInp.placeholder = cfg.has_push_token
        ? "已设置安全推送令牌（留空保持不变）"
        : "留空则免令牌接收";
    }
    tokPill?.classList.toggle("hidden", !cfg.has_push_token);

    // Populate Active-Standby cold failover settings
    camHubState.editingActiveSlot = Number(cfg.active_slot || 0);
    const foEn = document.getElementById("camCfgFailoverEnabled");
    if (foEn) foEn.checked = cfg.failover_enabled !== false;
    const foThr = document.getElementById("camCfgFailoverThreshold");
    if (foThr) foThr.value = cfg.failover_threshold ?? 3;
    const foAuto = document.getElementById("camCfgAutoRestorePrimary");
    if (foAuto) foAuto.checked = cfg.auto_recover_primary !== false;
    const foProbe = document.getElementById("camCfgPrimaryProbeSec");
    if (foProbe) foProbe.value = cfg.primary_probe_interval_sec ?? 180;
    renderWizardStandbySlots(cfg.standby_cameras || []);

    if (zoneSel && cam.zone_id) zoneSel.value = cam.zone_id;
    if (bindSel) bindSel.value = opts.presetBindGroupCode || currentPrimaryBoundCode || "";
    if (unitSel) unitSel.value = currentBoundGroups[0]?.near_unit_id || "";
    grpInp.value = cam.group_code || currentPrimaryBoundCode || "";
    addrInp.value = cam.mount_address || "";
    nameInp.value = cam.name || "";
    idInp.value = cam.camera_id;
    idInp.readOnly = false;
    nameInp.dataset.manual = "1";
    idInp.dataset.manual = "1";
    unbindQuickBtn?.classList.toggle("hidden", currentBoundGroups.length === 0);
  } else {
    const initVendor =
      opts.discoveredDevice?.vendor || opts.presetVendor || "hikvision";
    applyVendorSelectionToWizard(initVendor, true);
    document.getElementById("camCfgIngestEnabled").checked = true;
    document.getElementById("camCfgHostInput").value = opts.discoveredDevice?.ip || "";
    if (opts.discoveredDevice?.onvif_port) {
      document.getElementById("camCfgOnvifPortInput").value = opts.discoveredDevice.onvif_port;
    }
    if (pwdInp) {
      pwdInp.value = "";
      pwdInp.placeholder = "相机登录密码或机身6位安全码";
    }
    pwdPill?.classList.add("hidden");
    if (tokInp) {
      tokInp.value = "";
      tokInp.placeholder = "留空则免令牌接收";
    }
    tokPill?.classList.add("hidden");
    camHubState.editingActiveSlot = 0;
    const foEn = document.getElementById("camCfgFailoverEnabled");
    if (foEn) foEn.checked = true;
    const foThr = document.getElementById("camCfgFailoverThreshold");
    if (foThr) foThr.value = 3;
    const foAuto = document.getElementById("camCfgAutoRestorePrimary");
    if (foAuto) foAuto.checked = true;
    const foProbe = document.getElementById("camCfgPrimaryProbeSec");
    if (foProbe) foProbe.value = 180;
    renderWizardStandbySlots([]);
    document.getElementById("camCfgCustomUrlInput").value = "";
    addrInp.value = "";
    nameInp.value = "";
    idInp.value = "";
    idInp.readOnly = false;
    delete nameInp.dataset.manual;
    delete idInp.dataset.manual;
    unbindQuickBtn?.classList.add("hidden");

    if (opts.presetZoneId && zoneSel) {
      zoneSel.value = opts.presetZoneId;
    }
    // Preselect unbound floor-plan group if provided or if one exists!
    const preGroup =
      opts.presetBindGroupCode ||
      spotGroups.find((g) => !g.is_bound)?.group_code ||
      "";
    if (bindSel && preGroup) {
      bindSel.value = preGroup;
      const gObj = spotGroups.find((g) => g.group_code === preGroup);
      grpInp.value = preGroup;
      if (gObj?.zone_id && zoneSel) zoneSel.value = gObj.zone_id;
      if (gObj?.near_unit_id && unitSel) unitSel.value = gObj.near_unit_id;
      updateWizardRuleNamingPreview(true);
    } else {
      grpInp.value = "";
      updateWizardRuleNamingPreview(false);
    }
  }

  syncWizardProtocolVisibility(false);
  scheduleWizardUrlPreviewUpdate();
  modal.classList.remove("hidden");
}

function closeCameraConfigModal() {
  document.getElementById("cameraConfigModal")?.classList.add("hidden");
}

async function handleWizardOnvifProbe() {
  const btn = document.getElementById("btnCamCfgProbeOnvif");
  const box = document.getElementById("camCfgOnvifResultBox");
  const camId = document.getElementById("camCfgOriginalId")?.value || document.getElementById("camCfgIdInput")?.value || "";
  const ingest_config = collectWizardIngestConfig();
  if (!ingest_config.host) {
    setModalFeedback("camCfgModalFeedback", false, "请先填写摄像头或 NVR 的 IP 地址再执行 ONVIF 探测");
    document.getElementById("camCfgHostInput")?.focus();
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = "探测中…";
  }
  try {
    const res = await api("/api/cameras/onvif-probe", {
      method: "POST",
      body: JSON.stringify({ camera_id: camId, ingest_config }),
    });
    if (box) {
      box.classList.remove("hidden");
      if (res.ok) {
        const info = res.device_info || {};
        const profCount = (res.profiles || []).length;
        box.innerHTML = `
          <div><strong>✅ ONVIF 探测成功：</strong>${escapeHtml(
            [info.Manufacturer, info.Model, info.FirmwareVersion].filter(Boolean).join(" · ") ||
              "已读取标准媒体服务"
          )}（共 ${profCount} 个码流通道）</div>
          <div style="display:flex;gap:7px;flex-wrap:wrap;align-items:center;">
            ${
              res.raw_snapshot_uri
                ? `<button type="button" class="btn-apple btn-apple-tinted btn-apple-xs" id="btnApplyOnvifSnapUri">套用探测到的 HTTP 抓拍地址</button>`
                : ""
            }
            ${
              res.raw_rtsp_uri
                ? `<button type="button" class="btn-apple btn-apple-secondary btn-apple-xs" id="btnApplyOnvifRtspUri">套用探测到的主码流 RTSP 地址</button>`
                : ""
            }
          </div>
        `;
        document.getElementById("btnApplyOnvifSnapUri")?.addEventListener("click", () => {
          document.getElementById("camCfgProtocolSelect").value = "http_snapshot";
          document.getElementById("camCfgCustomUrlInput").value = res.raw_snapshot_uri;
          syncWizardProtocolVisibility(false);
          scheduleWizardUrlPreviewUpdate();
          showAppleToast("已套用 ONVIF 探测到的高清抓拍地址");
        });
        document.getElementById("btnApplyOnvifRtspUri")?.addEventListener("click", () => {
          document.getElementById("camCfgProtocolSelect").value = "rtsp";
          document.getElementById("camCfgCustomUrlInput").value = res.raw_rtsp_uri;
          syncWizardProtocolVisibility(false);
          scheduleWizardUrlPreviewUpdate();
          showAppleToast("已套用 ONVIF 探测到的 RTSP 视频流地址");
        });
      } else {
        box.innerHTML = `<span style="color:#B91C1C;">⚠️ ${escapeHtml(
          res.error || "未能读取 ONVIF 媒体通道，请确认相机已在网页后台开启 ONVIF 并创建 ONVIF 账号。"
        )}</span>`;
      }
    }
  } catch (err) {
    if (box) {
      box.classList.remove("hidden");
      box.innerHTML = `<span style="color:#B91C1C;">⚠️ ONVIF 探测失败：${escapeHtml(
        err.message
      )}</span>`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "🔎 ONVIF 探测通道";
    }
  }
}

async function handleWizardTestConnection() {
  const btn = document.getElementById("btnCamCfgTestConn");
  const pill = document.getElementById("camCfgTestStatusPill");
  const stage = document.getElementById("camCfgPreviewStage");
  const imgEl = document.getElementById("camCfgPreviewImg");
  const metaEl = document.getElementById("camCfgPreviewMetaText");

  const camId =
    document.getElementById("camCfgOriginalId")?.value?.trim() ||
    document.getElementById("camCfgIdInput")?.value?.trim() ||
    "";
  const save_as_reference = Boolean(
    document.getElementById("camCfgSaveTestAsRef")?.checked && camId
  );
  const ingest_config = collectWizardIngestConfig();

  if (btn) {
    btn.disabled = true;
    btn.textContent = "正在连接并抓拍…";
  }
  if (pill) {
    pill.className = "tpl-pill tpl-pill-idle";
    pill.textContent = "连接测试中…";
  }
  setModalFeedback("camCfgModalFeedback", true, "");

  try {
    const res = await api("/api/cameras/test-connection", {
      method: "POST",
      body: JSON.stringify({ camera_id: camId, ingest_config, save_as_reference }),
    });
    if (res.ok) {
      if (pill) {
        pill.className = "tpl-pill tpl-pill-ready";
        pill.textContent =
          res.mode === "push" || res.mode === "passive_push"
            ? "被动推送接口就绪"
            : `连通成功 · ${res.width}×${res.height} (${Math.round(res.latency_ms || 0)}ms)`;
      }
      if (res.preview_jpeg_b64 && stage && imgEl) {
        const dataUrl = `data:image/jpeg;base64,${res.preview_jpeg_b64}`;
        camHubState.lastTestDataUrl = dataUrl;
        if (camId) camHubState.previewSnapshots[camId] = dataUrl;
        imgEl.src = dataUrl;
        if (metaEl) {
          metaEl.textContent = `实拍分辨率 ${res.width}×${res.height} · 耗时 ${Math.round(
            res.latency_ms || 0
          )}ms · 原图仅驻内存${res.saved_as_reference ? " · 已更新脱敏标定底图" : ""}`;
        }
        stage.classList.remove("hidden");
      } else {
        setModalFeedback("camCfgModalFeedback", true, res.message || "被动推送接入地址已就绪");
      }
    } else {
      if (pill) {
        pill.className = "tpl-pill tpl-pill-idle";
        pill.textContent = "连接未通";
      }
      setModalFeedback("camCfgModalFeedback", false, res.error || "连接测试未通过，请核对 IP、端口与账号密码");
    }
  } catch (err) {
    if (pill) {
      pill.className = "tpl-pill tpl-pill-idle";
      pill.textContent = "连接未通";
    }
    setModalFeedback("camCfgModalFeedback", false, err.message || "测试请求异常");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "⚡ 测试连接并抓拍预览";
    }
  }
}

async function saveCameraConfigFromWizard(pullImmediately = false) {
  const mode = document.getElementById("camCfgMode")?.value || "create";
  const origId = document.getElementById("camCfgOriginalId")?.value?.trim() || "";
  let camera_id = document.getElementById("camCfgIdInput")?.value?.trim() || "";
  let name = document.getElementById("camCfgNameInput")?.value?.trim() || "";
  const group_code = document.getElementById("camCfgGroupCodeInput")?.value?.trim() || "";
  const mount_address = document.getElementById("camCfgMountAddrInput")?.value?.trim() || "";
  const zone_id = document.getElementById("camCfgZoneSelect")?.value || "Z_NORTH";
  const mount_type = document.getElementById("camCfgMountTypeSelect")?.value || "high_pole";
  const bind_spot_group = document.getElementById("camCfgBindGroupSelect")?.value || "";
  const near_unit_id = document.getElementById("camCfgNearUnitSelect")?.value || "";
  const ingest_config = collectWizardIngestConfig();

  const vPreset = getVendorPresetById(ingest_config.vendor);
  if (!name && group_code) {
    const parsed = parseCameraGroupCodesClient(group_code, mount_address);
    name = parsed.suggested_name || parsed.display_name || "";
  }
  if (!name) {
    name = `${vPreset.label}${ingest_config.host ? ` (${ingest_config.host})` : "机位"}`;
  }
  if (!camera_id) {
    const slug = (bind_spot_group || group_code || ingest_config.host || `${Date.now() % 100000}`)
      .replace(/[^A-Za-z0-9_]+/g, "_")
      .toUpperCase();
    camera_id = slug.startsWith("CAM_") ? slug : `CAM_${slug}`;
  }

  if (
    isPullProtocolClient(ingest_config.protocol) &&
    ingest_config.protocol !== "usb" &&
    ingest_config.enabled &&
    !ingest_config.host &&
    !ingest_config.custom_url
  ) {
    setModalFeedback(
      "camCfgModalFeedback",
      false,
      "开启「后台自动拉流巡检」时，请填写摄像头 IP 地址或完整流 URL（若暂未连通相机，可先取消勾选自动巡检保存台账）"
    );
    document.getElementById("camCfgHostInput")?.focus();
    return;
  }

  const saveBtn = document.getElementById(
    pullImmediately ? "btnSaveAndPullCameraConfig" : "btnSaveCameraConfigModal"
  );
  const origBtnTxt = saveBtn ? saveBtn.textContent : "";
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = pullImmediately ? "正在保存并抓拍…" : "正在保存…";
  }

  try {
    const spotGroups = state.overview?.spot_groups || [];
    const targetGrpObj = bind_spot_group
      ? spotGroups.find((g) => g.group_code === bind_spot_group)
      : null;

    const payload = {
      camera_id,
      name,
      zone_id,
      mount_type,
      group_code: group_code || bind_spot_group || "",
      mount_address,
      auto_name: false,
      scaffold_spots: !bind_spot_group && Boolean(group_code),
      ingest_config,
      bind_spot_group,
      unbind_spot_groups: mode === "edit" && !bind_spot_group,
      group_name: targetGrpObj?.group_name || "",
      near_unit_id,
    };

    let savedId = camera_id;
    if (mode === "edit" && origId) {
      const res = await api(`/api/cameras/${encodeURIComponent(origId)}/update`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      savedId = res.camera?.camera_id || camera_id;
    } else {
      const res = await api("/api/cameras", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      savedId = res.camera?.camera_id || camera_id;
    }

    await refreshOverview();
    closeCameraConfigModal();
    showAppleToast(
      `✅ 已保存摄像头「${name}」(${vPreset.label})${
        bind_spot_group ? ` 并绑定平面图车位组 ${bind_spot_group}` : ""
      }`
    );

    if (pullImmediately && isPullProtocolClient(ingest_config.protocol)) {
      await triggerSingleCameraPullNow(savedId);
    }
  } catch (err) {
    setModalFeedback("camCfgModalFeedback", false, `保存失败：${err.message}`);
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = origBtnTxt;
    }
  }
}

function bindCameraManagementHub() {
  ensureCameraPresetsLoaded();

  // Top CTA buttons in #tab-cameras
  document.getElementById("btnCamHubAddCamera")?.addEventListener("click", () => {
    openCameraConfigModal("create");
  });
  document.getElementById("btnOpenCamIngestWizard")?.addEventListener("click", () => {
    const camId = state.calib.cameraId || document.getElementById("calibCameraSelect")?.value || "";
    if (camId) {
      openCameraConfigModal("edit", camId);
    } else {
      openCameraConfigModal("create");
    }
  });
  document.getElementById("btnCamHubDiscoverToggle")?.addEventListener("click", () => {
    const drawer = document.getElementById("camDiscoverDrawer");
    drawer?.classList.toggle("hidden");
  });
  document.getElementById("btnCloseCamDiscover")?.addEventListener("click", () => {
    document.getElementById("camDiscoverDrawer")?.classList.add("hidden");
  });
  document.getElementById("btnRunCamDiscover")?.addEventListener("click", runLanCameraDiscovery);

  document.getElementById("btnCamHubPullAll")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const pullCams = (state.overview?.cameras || []).filter(
      (c) => c.ingest_config?.is_pull && (c.ingest_config?.host || c.ingest_config?.custom_url || c.ingest_config?.protocol === "usb")
    );
    if (!pullCams.length) {
      showAppleToast("当前尚无已配置 IP/流地址的主动拉流摄像头，请先点「接入配置与绑定」设置相机 IP", "warn");
      return;
    }
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = `巡检中 (0/${pullCams.length})…`;
    let okCnt = 0;
    for (let i = 0; i < pullCams.length; i += 1) {
      btn.textContent = `巡检中 (${i + 1}/${pullCams.length})…`;
      try {
        const res = await api(
          `/api/cameras/${encodeURIComponent(pullCams[i].camera_id)}/pull-now`,
          { method: "POST", body: JSON.stringify({ process_vision: true }) }
        );
        if (res.preview_jpeg_b64) {
          camHubState.previewSnapshots[pullCams[i].camera_id] = `data:image/jpeg;base64,${res.preview_jpeg_b64}`;
        }
        okCnt += 1;
      } catch (_) {}
    }
    btn.disabled = false;
    btn.textContent = orig;
    await refreshOverview();
    showAppleToast(`🔄 巡检完成：成功抓拍识别 ${okCnt}/${pullCams.length} 台主动拉流摄像头`);
  });

  // Filter buttons & KPI tiles & Search
  document.querySelectorAll(".cam-filter-btn, .cam-hub-kpi").forEach((el) => {
    el.addEventListener("click", () => {
      setCamHubFilter(el.dataset.camFilter || "all");
    });
  });
  document.getElementById("camHubSearchInput")?.addEventListener("input", (e) => {
    camHubState.searchQuery = e.target.value || "";
    renderCameraManagementHub(state.overview);
  });

  // Brand quick-create chips
  document.getElementById("camHubVendorStrip")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".js-vendor-quick-create");
    if (!chip) return;
    openCameraConfigModal("create", "", { presetVendor: chip.dataset.vendor });
  });

  // Unbound floor-plan groups quick-bind strip
  document.getElementById("camHubUnboundGroupsStrip")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".js-bind-unbound-floor-group");
    if (!btn) return;
    openCameraConfigModal("create", "", {
      presetBindGroupCode: btn.dataset.groupCode || "",
      presetZoneId: btn.dataset.zoneId || "",
    });
  });

  // Camera Hub Cards Delegation
  document.getElementById("camHubCardsGrid")?.addEventListener("click", async (e) => {
    const slotPill = e.target.closest(".js-cam-switch-slot");
    if (slotPill) {
      const cid = slotPill.dataset.camId;
      const targetSlot = Number(slotPill.dataset.slot || 0);
      slotPill.disabled = true;
      try {
        const res = await api(`/api/cameras/${encodeURIComponent(cid)}/failover/switch`, {
          method: "POST",
          body: JSON.stringify({ target_slot: targetSlot, reason: "manual_admin_ui" }),
        });
        await refreshOverview();
        const restored = res.calibration_switch?.restored_saved_profile;
        showAppleToast(
          `🛡️ 已将摄像头 ${cid} 切换至槽位 #${targetSlot}（${
            restored ? "已自动载入该槽位独立标定框" : "已继承基准车位框，可点空间标定微调该视角"
          }）`
        );
      } catch (err) {
        showAppleToast(`槽位切换失败：${err.message}`, "error");
      } finally {
        slotPill.disabled = false;
      }
      return;
    }
    if (e.target.closest(".js-cam-empty-create")) {
      openCameraConfigModal("create");
      return;
    }
    const cfgBtn = e.target.closest(".js-cam-hub-config");
    if (cfgBtn) {
      openCameraConfigModal("edit", cfgBtn.dataset.camId);
      return;
    }
    const pullBtn = e.target.closest(".js-cam-hub-pull-now");
    if (pullBtn) {
      await triggerSingleCameraPullNow(pullBtn.dataset.camId, pullBtn);
      return;
    }
    const calibBtn = e.target.closest(".js-cam-hub-calib");
    if (calibBtn) {
      jumpToCalibrateCamera(calibBtn.dataset.camId, false);
      return;
    }
    const labBtn = e.target.closest(".js-cam-hub-lab");
    if (labBtn) {
      jumpToLabCamera(labBtn.dataset.camId);
      return;
    }
    const placeBtn = e.target.closest(".js-cam-hub-place-map");
    if (placeBtn) {
      switchMainTab("driver");
      startInteractiveCameraPlacement(placeBtn.dataset.camId);
      return;
    }
    const unbindBtn = e.target.closest(".js-cam-hub-unbind");
    if (unbindBtn) {
      const cid = unbindBtn.dataset.camId;
      unbindBtn.disabled = true;
      try {
        await api(`/api/cameras/${encodeURIComponent(cid)}/unbind`, { method: "POST" });
        await refreshOverview();
        showAppleToast(`⛓️‍💥 已解除摄像头 ${cid} 与平面图车位组的绑定（车位框与标定数据完好保留）`);
      } catch (err) {
        showAppleToast(`解绑失败：${err.message}`, "error");
      } finally {
        unbindBtn.disabled = false;
      }
      return;
    }
    const delBtn = e.target.closest(".js-cam-hub-delete");
    if (delBtn) {
      openCameraDeleteModal(delBtn.dataset.camId, delBtn.dataset.groupCode || "");
    }
  });

  // Wizard Modal Events
  document.getElementById("btnCamAddStandbySlot")?.addEventListener("click", () => {
    const current = collectWizardStandbySlots();
    if (current.length >= 3) return;
    current.push({
      slot_id: current.length + 1,
      label: `备用摄像头#${current.length + 1}`,
      vendor: camHubState.selectedVendor || "hikvision",
      protocol: "rtsp",
      host: "",
      port: 80,
      rtsp_port: 554,
      username: "admin",
      password: "",
      custom_url: "",
    });
    renderWizardStandbySlots(current);
  });

  document.getElementById("camCfgStandbySlotsList")?.addEventListener("click", async (e) => {
    const rmBtn = e.target.closest(".js-cam-remove-standby");
    if (rmBtn) {
      const idx = Number(rmBtn.dataset.sbIndex);
      const current = collectWizardStandbySlots();
      // Preserve has_password flags from existing state when re-rendering
      const prevMeta = camHubState.wizardStandbySlots || [];
      const merged = current.map((s, i) => ({
        ...s,
        has_password: Boolean(s.password) || Boolean(prevMeta[i]?.has_password),
      }));
      merged.splice(idx, 1);
      renderWizardStandbySlots(merged);
      return;
    }
    const testBtn = e.target.closest(".js-cam-test-standby");
    if (testBtn) {
      const idx = Number(testBtn.dataset.sbIndex);
      const slots = collectWizardStandbySlots();
      const sb = slots[idx];
      if (!sb) return;
      const origId = document.getElementById("camCfgOriginalId")?.value || "";
      testBtn.disabled = true;
      const origTxt = testBtn.textContent;
      testBtn.textContent = "测通中…";
      try {
        const res = await api("/api/cameras/test-connection", {
          method: "POST",
          body: JSON.stringify({
            camera_id: origId,
            slot_id: idx + 1,
            ingest_config: sb,
          }),
        });
        if (res.preview_jpeg_b64) {
          const dataUrl = `data:image/jpeg;base64,${res.preview_jpeg_b64}`;
          camHubState.lastTestDataUrl = dataUrl;
          const imgEl = document.getElementById("camCfgPreviewImg");
          if (imgEl) imgEl.src = dataUrl;
          const metaEl = document.getElementById("camCfgPreviewMetaText");
          if (metaEl) {
            metaEl.textContent = `✅ 冷备槽位 #${idx + 1} (${sb.label}) 抓拍成功 · ${res.width}×${res.height} · ${res.elapsed_ms}ms`;
          }
          document.getElementById("camCfgPreviewStage")?.classList.remove("hidden");
        }
        showAppleToast(`✅ 冷备槽位 #${idx + 1} (${sb.label}) 测通成功 (${res.elapsed_ms}ms)`);
      } catch (err) {
        showAppleToast(`冷备槽位 #${idx + 1} 测通失败：${err.message}`, "error");
      } finally {
        testBtn.disabled = false;
        testBtn.textContent = origTxt;
      }
    }
  });

  document.getElementById("btnCloseCameraConfigModal")?.addEventListener("click", closeCameraConfigModal);
  document.getElementById("btnCancelCameraConfigModal")?.addEventListener("click", closeCameraConfigModal);
  document.getElementById("cameraConfigModalBackdrop")?.addEventListener("click", closeCameraConfigModal);

  document.getElementById("camCfgVendorGrid")?.addEventListener("click", (e) => {
    const card = e.target.closest("[data-wiz-vendor]");
    if (!card) return;
    applyVendorSelectionToWizard(card.dataset.wizVendor, true);
  });

  document.getElementById("camCfgProtocolSelect")?.addEventListener("change", () => {
    syncWizardProtocolVisibility(true);
    scheduleWizardUrlPreviewUpdate();
  });

  [
    "camCfgHostInput",
    "camCfgPortInput",
    "camCfgOnvifPortInput",
    "camCfgAuthModeSelect",
    "camCfgUsernameInput",
    "camCfgPasswordInput",
    "camCfgChannelInput",
    "camCfgStreamSelect",
    "camCfgRtspTransportSelect",
    "camCfgCustomUrlInput",
    "camCfgUsbIndexInput",
    "camCfgPushTokenInput",
  ].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", scheduleWizardUrlPreviewUpdate);
    document.getElementById(id)?.addEventListener("change", scheduleWizardUrlPreviewUpdate);
  });

  document.getElementById("btnToggleCamPwdVis")?.addEventListener("click", (e) => {
    const inp = document.getElementById("camCfgPasswordInput");
    if (!inp) return;
    const isPwd = inp.type === "password";
    inp.type = isPwd ? "text" : "password";
    e.currentTarget.textContent = isPwd ? "隐藏" : "显示";
  });

  document.getElementById("btnGenCamPushToken")?.addEventListener("click", () => {
    const rand = Array.from(crypto.getRandomValues(new Uint8Array(12)))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    const inp = document.getElementById("camCfgPushTokenInput");
    if (inp) {
      inp.value = `cam_${rand}`;
      scheduleWizardUrlPreviewUpdate();
    }
  });

  document.getElementById("cameraConfigModal")?.addEventListener("click", async (e) => {
    const copyBtn = e.target.closest(".js-copy-code");
    if (!copyBtn) return;
    const targetEl = document.getElementById(copyBtn.dataset.copyTarget);
    if (!targetEl) return;
    try {
      await navigator.clipboard.writeText(targetEl.textContent || "");
      showAppleToast("已复制到剪贴板");
    } catch (_) {}
  });

  document.getElementById("btnCamCfgProbeOnvif")?.addEventListener("click", handleWizardOnvifProbe);
  document.getElementById("btnCamCfgTestConn")?.addEventListener("click", handleWizardTestConnection);

  // Load live grabbed snapshot directly into Calibration Studio!
  document.getElementById("btnCamCfgSendToCalib")?.addEventListener("click", () => {
    const dataUrl = camHubState.lastTestDataUrl;
    if (!dataUrl) return;
    const camId =
      document.getElementById("camCfgOriginalId")?.value?.trim() ||
      document.getElementById("camCfgIdInput")?.value?.trim() ||
      "";
    closeCameraConfigModal();
    if (camId && (state.overview?.cameras || []).some((c) => c.camera_id === camId)) {
      jumpToCalibrateCamera(camId, false);
    } else {
      switchMainTab("calibrate");
    }
    const img = new Image();
    img.onload = () => {
      state.calib.groundBgImage = img;
      state.calib.groundBgDataUrl = dataUrl;
      state.calib.groundBgIsServerPreview = false;
      syncCalibActiveBgImage();
      renderCalibCanvas();
      showAppleToast("🎯 已将摄像头实时抓拍画面载入空间标定画布");
    };
    img.src = dataUrl;
  });

  document.getElementById("camCfgBindGroupSelect")?.addEventListener("change", (e) => {
    const gc = e.target.value || "";
    if (!gc) return;
    const spotGroups = state.overview?.spot_groups || [];
    const gObj = spotGroups.find((g) => g.group_code === gc);
    const grpInp = document.getElementById("camCfgGroupCodeInput");
    const zoneSel = document.getElementById("camCfgZoneSelect");
    const unitSel = document.getElementById("camCfgNearUnitSelect");
    if (grpInp && (!grpInp.value || document.getElementById("camCfgMode")?.value === "create")) {
      grpInp.value = gc;
    }
    if (gObj?.zone_id && zoneSel) zoneSel.value = gObj.zone_id;
    if (gObj?.near_unit_id && unitSel) unitSel.value = gObj.near_unit_id;
    updateWizardRuleNamingPreview(document.getElementById("camCfgMode")?.value === "create");
  });

  document.getElementById("camCfgNameInput")?.addEventListener("input", (e) => {
    if (e.target.value.trim()) e.target.dataset.manual = "1";
    else delete e.target.dataset.manual;
  });
  document.getElementById("camCfgIdInput")?.addEventListener("input", (e) => {
    if (e.target.value.trim()) e.target.dataset.manual = "1";
    else delete e.target.dataset.manual;
    updatePushEndpointHintsInWizard();
  });
  document.getElementById("camCfgGroupCodeInput")?.addEventListener("input", () => {
    updateWizardRuleNamingPreview(false);
  });
  document.getElementById("camCfgMountAddrInput")?.addEventListener("input", () => {
    updateWizardRuleNamingPreview(false);
  });

  document.getElementById("btnOpenZonesFromCamWizard")?.addEventListener("click", () => {
    const curZ = document.getElementById("camCfgZoneSelect")?.value || null;
    openZonesModal(curZ);
  });

  document.getElementById("btnCamCfgUnbindQuick")?.addEventListener("click", async () => {
    const cid = document.getElementById("camCfgOriginalId")?.value?.trim();
    if (!cid) return;
    try {
      await api(`/api/cameras/${encodeURIComponent(cid)}/unbind`, { method: "POST" });
      await refreshOverview();
      closeCameraConfigModal();
      showAppleToast(`⛓️‍💥 已解除摄像头 ${cid} 与平面图车位组的绑定`);
    } catch (err) {
      setModalFeedback("camCfgModalFeedback", false, `解绑失败：${err.message}`);
    }
  });

  document.getElementById("btnSaveCameraConfigModal")?.addEventListener("click", () => {
    saveCameraConfigFromWizard(false);
  });
  document.getElementById("btnSaveAndPullCameraConfig")?.addEventListener("click", () => {
    saveCameraConfigFromWizard(true);
  });
}

/* ==================== SPOT MULTI-TAG FILTER & FIGMA DIRECT-CLICK POPOVER ==================== */

const DEFAULT_SPOT_TAG_CATALOG_CLIENT = [
  { code: "ev_charging", label: "新能源充电", short_badge: "⚡" },
  { code: "rentable", label: "对外招租", short_badge: "🤝" },
  { code: "private", label: "产权固定位", short_badge: "🔒" },
  { code: "large_friendly", label: "宽体大车位", short_badge: "🚙" },
  { code: "accessible", label: "无障碍车位", short_badge: "♿" },
  { code: "visitor", label: "访客临停", short_badge: "访" },
];

function getSpotTagCatalogClient() {
  const cat = state.overview?.spot_tag_catalog;
  return Array.isArray(cat) && cat.length ? cat : DEFAULT_SPOT_TAG_CATALOG_CLIENT;
}

function getAllPlacedOverviewSpots(ovData) {
  const data = ovData || state.overview;
  if (!data) return [];
  if (state.siteMap?.editMode && Array.isArray(state.siteMap.draftSpots)) {
    return state.siteMap.draftSpots;
  }
  const out = [];
  (data.zones || []).forEach((z) => {
    (z.spots || []).forEach((sp) => {
      if (sp.map_placed === false || sp.map_x == null || sp.map_y == null) return;
      out.push({ ...sp, zone_id: sp.zone_id || z.zone_id, zone_name: sp.zone_name || z.name });
    });
  });
  return out;
}

function syncSpotTagFilterCounts(ovData) {
  const data = ovData || state.overview;
  const spots = getAllPlacedOverviewSpots(data);
  const availByCode = new Map();
  if (data && Array.isArray(data.tag_availability) && !state.siteMap?.editMode) {
    data.tag_availability.forEach((it) => {
      availByCode.set(String(it.code), {
        label: it.label || it.code,
        short_badge: it.short_badge || "#",
        total: Number(it.total_spots || 0),
        free: Number(it.effective_free || 0),
        is_custom: Boolean(it.is_custom),
      });
    });
  } else {
    spots.forEach((sp) => {
      const tags = Array.isArray(sp.tags) ? [...sp.tags] : [];
      if (sp.is_ev && !tags.includes("ev_charging")) tags.unshift("ev_charging");
      const effSt = getEffectiveSpotStatusClient(sp);
      const isBound = sp.camera_id && sp.camera_id !== "UNASSIGNED";
      tags.forEach((code) => {
        const prev = availByCode.get(code) || {
          label: code,
          short_badge: "#",
          total: 0,
          free: 0,
          is_custom: !DEFAULT_SPOT_TAG_CATALOG_CLIENT.some((c) => c.code === code),
        };
        prev.total += 1;
        if (effSt === "FREE" && isBound && (code === "private" || !sp.is_private_reserved)) {
          prev.free += 1;
        }
        availByCode.set(code, prev);
      });
    });
  }

  const fmtTagStat = (code) => {
    const rec = availByCode.get(code);
    if (!rec || rec.total <= 0) return "0";
    return `余${rec.free}/${rec.total}`;
  };

  const activeFilter = state.siteMap?.tagFilter || "all";
  const pillsWrap = document.getElementById("spotTagFilterPills");
  if (pillsWrap) {
    const totalFreeAll = Number(data?.total_effective_free ?? 0);
    const fixedDefs = [
      { code: "all", cls: "", icon: "", label: "全部车位", stat: spots.length ? `余${totalFreeAll}/${spots.length}` : "0" },
      { code: "ev_charging", cls: "tag-ev", icon: "⚡ ", label: "新能源", stat: fmtTagStat("ev_charging") },
      { code: "rentable", cls: "tag-rent", icon: "🤝 ", label: "对外招租", stat: fmtTagStat("rentable") },
      { code: "private", cls: "tag-priv", icon: "🔒 ", label: "产权位", stat: fmtTagStat("private") },
      { code: "large_friendly", cls: "tag-wide", icon: "🚙 ", label: "宽体位", stat: fmtTagStat("large_friendly") },
    ];
    for (const extraCode of ["accessible", "visitor"]) {
      const recEx = availByCode.get(extraCode);
      if (recEx && recEx.total > 0) {
        const ic = extraCode === "accessible" ? "♿ " : "访 ";
        const lb = extraCode === "accessible" ? "无障碍" : "访客位";
        fixedDefs.push({ code: extraCode, cls: "", icon: ic, label: lb, stat: fmtTagStat(extraCode) });
      }
    }
    availByCode.forEach((recC, cCode) => {
      if (recC.is_custom && recC.total > 0) {
        fixedDefs.push({
          code: cCode,
          cls: "",
          icon: "# ",
          label: cCode,
          stat: `余${recC.free}/${recC.total}`,
        });
      }
    });
    pillsWrap.innerHTML = fixedDefs
      .map(
        (d) => `
        <button type="button" class="tag-filter-pill ${d.cls} ${activeFilter === d.code ? "is-active" : ""}" data-tag-filter="${escapeHtml(d.code)}">
          ${d.icon}${escapeHtml(d.label)} <em>${escapeHtml(d.stat)}</em>
        </button>
      `
      )
      .join("");
  }

  const drvTagSel = document.getElementById("drvTagFilterSelect");
  if (drvTagSel) {
    const baseOpts = [
      ["", "✨ 不限标签（全部公共车位）"],
      ["ev_charging", `⚡ 新能源充电位 (${fmtTagStat("ev_charging")})`],
      ["rentable", `🤝 对外招租/共享位 (${fmtTagStat("rentable")})`],
      ["large_friendly", `🚙 宽体大车位 (${fmtTagStat("large_friendly")})`],
      ["accessible", `♿ 无障碍车位 (${fmtTagStat("accessible")})`],
      ["visitor", `访 访客临停位 (${fmtTagStat("visitor")})`],
      ["private", `🔒 产权固定位 (${fmtTagStat("private")})`],
    ];
    availByCode.forEach((recC, cCode) => {
      if (recC.is_custom && recC.total > 0) {
        baseOpts.push([cCode, `# ${cCode} (余${recC.free}/${recC.total})`]);
      }
    });
    drvTagSel.innerHTML = baseOpts
      .map(([v, t]) => `<option value="${escapeHtml(v)}">${escapeHtml(t)}</option>`)
      .join("");
    drvTagSel.value = activeFilter === "all" ? "" : activeFilter;
  }

  const recTagBtn = document.getElementById("btnRecommendActiveTag");
  if (recTagBtn) {
    recTagBtn.classList.toggle("hidden", activeFilter === "all");
  }
}

function positionSpotTagPopover(clientX, clientY) {
  const pop = document.getElementById("spotTagPopover");
  if (!pop) return;
  const vw = window.innerWidth || 1280;
  const vh = window.innerHeight || 800;
  const popRect = pop.getBoundingClientRect();
  const pw = popRect.width || 356;
  const ph = popRect.height || 380;

  let left = (clientX || vw / 2) + 18;
  if (left + pw + 12 > vw) {
    left = Math.max(12, (clientX || vw / 2) - pw - 18);
  }
  let top = (clientY || vh / 2) - Math.min(90, ph * 0.25);
  top = Math.max(14, Math.min(vh - ph - 14, top));

  pop.style.left = `${Math.round(left)}px`;
  pop.style.top = `${Math.round(top)}px`;
}

function syncSpotTagPopoverDrawersAndPills() {
  const catalog = getSpotTagCatalogClient();
  const builtinCodes = new Set(catalog.map((c) => c.code));
  const draftTags = state.tagPopover.draftTags || [];

  const gridEl = document.getElementById("spotTagBuiltinGrid");
  if (gridEl) {
    const iconFor = {
      ev_charging: "⚡",
      rentable: "🤝",
      private: "🔒",
      large_friendly: "🚙",
      accessible: "♿",
      visitor: "访",
    };
    gridEl.innerHTML = catalog
      .map((item) => {
        const isOn = draftTags.includes(item.code);
        const ic = iconFor[item.code] || item.short_badge || "🏷️";
        return `
          <button type="button" class="stp-tag-btn ${isOn ? "is-on" : ""}" data-tag-code="${escapeHtml(item.code)}" title="${escapeHtml(item.description || item.label)}">
            <span>${ic} ${escapeHtml(item.label)}</span>
            <span class="stp-tag-check">${isOn ? "✓" : ""}</span>
          </button>
        `;
      })
      .join("");
  }

  const customChipsEl = document.getElementById("spotTagCustomChips");
  if (customChipsEl) {
    const customs = draftTags.filter((t) => !builtinCodes.has(t));
    customChipsEl.innerHTML = customs
      .map(
        (ct) => `
        <span class="stp-custom-chip">
          #${escapeHtml(ct)}
          <button type="button" data-remove-custom-tag="${escapeHtml(ct)}" aria-label="移除标签">✕</button>
        </span>
      `
      )
      .join("");
  }

  const evDrawer = document.getElementById("spotTagEvDrawer");
  if (evDrawer) {
    evDrawer.classList.toggle("hidden", !draftTags.includes("ev_charging"));
  }
  const rentDrawer = document.getElementById("spotTagRentalDrawer");
  if (rentDrawer) {
    rentDrawer.classList.toggle("hidden", !draftTags.includes("rentable"));
  }
}

function openSpotTagPopover(spotIds, clientX, clientY) {
  const allSpots = getAllPlacedOverviewSpots();
  const cleanIds = [...new Set((spotIds || []).filter(Boolean))];
  const targetSpots = allSpots.filter((s) => cleanIds.includes(s.spot_id));
  if (!targetSpots.length) return;

  const first = targetSpots[0];
  let initialTags;
  if (targetSpots.length === 1) {
    initialTags = [...(Array.isArray(first.tags) ? first.tags : [])];
    if (first.is_ev && !initialTags.includes("ev_charging")) initialTags.unshift("ev_charging");
  } else {
    // Multi-select: pre-check tags shared across all selected spots
    const firstTags = Array.isArray(first.tags) ? first.tags : [];
    initialTags = firstTags.filter((t) =>
      targetSpots.every((sp) => (sp.tags || []).includes(t) || (t === "ev_charging" && sp.is_ev))
    );
  }

  state.tagPopover.open = true;
  state.tagPopover.spotIds = targetSpots.map((s) => s.spot_id);
  state.tagPopover.draftTags = initialTags;
  state.tagPopover.anchorX = clientX || window.innerWidth * 0.55;
  state.tagPopover.anchorY = clientY || window.innerHeight * 0.42;

  const eyebrowEl = document.getElementById("spotTagPopoverEyebrow");
  const titleEl = document.getElementById("spotTagPopoverTitle");
  const stPillEl = document.getElementById("spotTagPopoverStatusPill");
  const sameGrpBtn = document.getElementById("btnSelectSameGroupInTagPopover");

  const stLabels = { FREE: "空闲可停", OCCUPIED: "已停车辆", BLOCKED: "占道禁停", UNKNOWN: "待接入" };
  if (targetSpots.length === 1) {
    const grpTitle = first.group_name || first.zone_name || first.group_code || "小区车位";
    if (eyebrowEl) eyebrowEl.textContent = grpTitle;
    if (titleEl) titleEl.textContent = `${first.label} 号车位`;
    const effSt = getEffectiveSpotStatusClient(first);
    if (stPillEl) {
      stPillEl.textContent = stLabels[effSt] || effSt;
      stPillEl.className = `stp-status-pill st-${effSt}`;
      stPillEl.hidden = false;
    }
  } else {
    if (eyebrowEl) eyebrowEl.textContent = "Shift 多选 · 批量精细化改标";
    if (titleEl) titleEl.textContent = `已选 ${targetSpots.length} 个车位`;
    if (stPillEl) {
      stPillEl.textContent = "批量同步";
      stPillEl.className = "stp-status-pill";
      stPillEl.hidden = false;
    }
  }

  if (sameGrpBtn) {
    const grpKey = first.group_code || first.zone_id || "";
    const grpCount = grpKey
      ? allSpots.filter((s) => (first.group_code ? s.group_code === first.group_code : s.zone_id === first.zone_id)).length
      : 0;
    sameGrpBtn.hidden = grpCount <= 1 || grpCount === targetSpots.length;
    if (!sameGrpBtn.hidden) {
      sameGrpBtn.textContent = `同排全选(${grpCount})`;
    }
  }

  const meta = first.tag_meta || {};
  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = v || "";
  };
  setVal("spotTagChargerInput", meta.charger_power || "");
  setVal("spotTagRentalWindowInput", meta.rental_window || "");
  setVal("spotTagRentalPriceInput", meta.rental_price || "");
  setVal("spotTagRentalContactInput", meta.rental_contact || "");
  setVal("spotTagNoteInput", meta.note || "");
  setVal("spotTagCustomInput", "");

  const autoBadge = document.getElementById("spotTagEvAutoBadge");
  if (autoBadge) {
    autoBadge.classList.toggle("hidden", !meta.ev_auto_detected);
  }

  syncSpotTagPopoverDrawersAndPills();

  const pop = document.getElementById("spotTagPopover");
  if (pop) {
    pop.classList.remove("hidden");
    positionSpotTagPopover(state.tagPopover.anchorX, state.tagPopover.anchorY);
  }
  if (state.overview) renderAbstractSvgMap(state.overview);
}

function closeSpotTagPopover() {
  if (!state.tagPopover?.open) return;
  state.tagPopover.open = false;
  state.tagPopover.spotIds = [];
  const pop = document.getElementById("spotTagPopover");
  pop?.classList.add("hidden");
  if (state.overview) renderAbstractSvgMap(state.overview);
}

function handleBrowseSpotTagClick(spotId, additive, clientX, clientY) {
  if (!spotId) return;
  let nextIds;
  if (additive && state.tagPopover?.open && Array.isArray(state.tagPopover.spotIds)) {
    const cur = new Set(state.tagPopover.spotIds);
    if (cur.has(spotId) && cur.size > 1) cur.delete(spotId);
    else cur.add(spotId);
    nextIds = [...cur];
  } else {
    nextIds = [spotId];
  }
  openSpotTagPopover(nextIds, clientX, clientY);
}

async function saveSpotTagPopoverChanges() {
  const ids = state.tagPopover?.spotIds || [];
  if (!ids.length) return;

  const nextTags = [...(state.tagPopover.draftTags || [])];
  const hasEv = nextTags.includes("ev_charging");
  const hasRent = nextTags.includes("rentable");
  const tagMeta = {
    charger_power: hasEv ? (document.getElementById("spotTagChargerInput")?.value || "").trim() : "",
    rental_window: hasRent ? (document.getElementById("spotTagRentalWindowInput")?.value || "").trim() : "",
    rental_price: hasRent ? (document.getElementById("spotTagRentalPriceInput")?.value || "").trim() : "",
    rental_contact: hasRent ? (document.getElementById("spotTagRentalContactInput")?.value || "").trim() : "",
    note: (document.getElementById("spotTagNoteInput")?.value || "").trim(),
  };

  // Update Edit Mode draftSpots in memory if Edit Mode is currently active
  if (state.siteMap?.editMode && Array.isArray(state.siteMap.draftSpots)) {
    state.siteMap.draftSpots.forEach((sp) => {
      if (ids.includes(sp.spot_id)) {
        sp.tags = [...nextTags];
        sp.tag_meta = { ...(sp.tag_meta || {}), ...tagMeta };
        sp.is_ev = hasEv;
        sp.is_rentable = hasRent;
      }
    });
  }

  // Persist immediately to SQLite for all existing DB spots
  const persistedIds = ids.filter((sid) => !String(sid).startsWith("NEW_"));
  const saveBtn = document.getElementById("btnSaveSpotTags");
  if (saveBtn) saveBtn.disabled = true;
  try {
    if (persistedIds.length > 0) {
      await api("/api/spots/tags", {
        method: "POST",
        body: JSON.stringify({
          spot_ids: persistedIds,
          tags: nextTags,
          tag_meta: tagMeta,
        }),
      });
    }
    closeSpotTagPopover();
    if (!state.siteMap?.editMode) {
      await refreshOverview();
    } else {
      syncSpotTagFilterCounts(state.overview);
      renderAbstractSvgMap(state.overview);
    }
    const summary = nextTags.length
      ? `已更新 ${ids.length} 个车位标签`
      : `已清空 ${ids.length} 个车位标签`;
    showAppleToast(`🏷️ ${summary}`);
  } catch (err) {
    showAppleToast(`保存标签失败：${err.message}`, "error");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

function bindSpotTagPopoverAndFilters() {
  document.getElementById("spotTagFilterPills")?.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".tag-filter-pill");
    if (!btn) return;
    const nextFilter = btn.dataset.tagFilter || "all";
    state.siteMap.tagFilter =
      state.siteMap.tagFilter === nextFilter && nextFilter !== "all" ? "all" : nextFilter;
    syncSpotTagFilterCounts(state.overview);
    updateSiriHint();
    if (state.overview) renderAbstractSvgMap(state.overview);
  });

  document.getElementById("btnRecommendActiveTag")?.addEventListener("click", () => {
    triggerRecommendation("标签筛选寻位");
  });

  document.getElementById("btnEditSelectedSpotTags")?.addEventListener("click", (ev) => {
    const sel = getSelectedSpots();
    const targets = sel.length ? sel : getActiveGroupSpots();
    if (!targets.length) {
      showAppleToast("请先在画布点选或框选至少 1 个车位", "warn");
      return;
    }
    const rect = ev.currentTarget.getBoundingClientRect();
    openSpotTagPopover(
      targets.map((s) => s.spot_id),
      rect.left - 370,
      rect.top
    );
  });

  document.getElementById("spotTagBuiltinGrid")?.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".stp-tag-btn");
    if (!btn) return;
    const code = btn.dataset.tagCode;
    if (!code) return;
    const cur = state.tagPopover.draftTags || [];
    const idx = cur.indexOf(code);
    if (idx >= 0) cur.splice(idx, 1);
    else cur.push(code);
    state.tagPopover.draftTags = cur;
    syncSpotTagPopoverDrawersAndPills();
  });

  const addCustomTagFromInput = () => {
    const inp = document.getElementById("spotTagCustomInput");
    if (!inp) return;
    const val = inp.value.trim().replace(/^[#＃]+/, "").slice(0, 16);
    if (!val) return;
    if (!state.tagPopover.draftTags.includes(val)) {
      state.tagPopover.draftTags.push(val);
    }
    inp.value = "";
    syncSpotTagPopoverDrawersAndPills();
  };

  document.getElementById("btnAddCustomSpotTag")?.addEventListener("click", addCustomTagFromInput);
  document.getElementById("spotTagCustomInput")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      addCustomTagFromInput();
    }
  });

  document.getElementById("spotTagCustomChips")?.addEventListener("click", (ev) => {
    const rmBtn = ev.target.closest("[data-remove-custom-tag]");
    if (!rmBtn) return;
    const targetTag = rmBtn.dataset.removeCustomTag;
    state.tagPopover.draftTags = (state.tagPopover.draftTags || []).filter((t) => t !== targetTag);
    syncSpotTagPopoverDrawersAndPills();
  });

  document.getElementById("spotTagPopover")?.addEventListener("click", (ev) => {
    const presetBtn = ev.target.closest(".stp-preset-chip");
    if (!presetBtn) return;
    const wrap = presetBtn.closest("[data-target-input]");
    const inputId = wrap?.dataset?.targetInput;
    const inp = inputId ? document.getElementById(inputId) : null;
    if (inp) {
      inp.value = presetBtn.dataset.val || "";
      inp.focus();
    }
  });

  document.getElementById("btnSelectSameGroupInTagPopover")?.addEventListener("click", () => {
    const allSpots = getAllPlacedOverviewSpots();
    const first = allSpots.find((s) => s.spot_id === state.tagPopover.spotIds?.[0]);
    if (!first) return;
    const grpIds = allSpots
      .filter((s) => (first.group_code ? s.group_code === first.group_code : s.zone_id === first.zone_id))
      .map((s) => s.spot_id);
    if (grpIds.length) {
      openSpotTagPopover(grpIds, state.tagPopover.anchorX, state.tagPopover.anchorY);
    }
  });

  document.getElementById("btnClearSpotTags")?.addEventListener("click", () => {
    state.tagPopover.draftTags = [];
    ["spotTagChargerInput", "spotTagRentalWindowInput", "spotTagRentalPriceInput", "spotTagRentalContactInput", "spotTagNoteInput"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    syncSpotTagPopoverDrawersAndPills();
  });

  document.getElementById("btnCloseSpotTagPopover")?.addEventListener("click", closeSpotTagPopover);
  document.getElementById("btnCancelSpotTagPopover")?.addEventListener("click", closeSpotTagPopover);
  document.getElementById("btnSaveSpotTags")?.addEventListener("click", saveSpotTagPopoverChanges);

  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && state.tagPopover?.open) {
      closeSpotTagPopover();
    }
  });
}

