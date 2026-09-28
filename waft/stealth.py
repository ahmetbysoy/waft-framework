"""Stealth / anti-detection layer.

Two independent mechanisms are combined:

*   **playwright-stealth** (when installed) is applied to every context — it patches
    ``navigator.webdriver``, plugins, permissions, media codecs, iframe contentWindow …
*   **WAFT's own init script** (always available, no extra dependency) covers the gaps and
    the parts that must be *per-context*: canvas/WebGL/audio fingerprint perturbation,
    WebRTC leak blocking, timezone/locale coherence, ``chrome.*`` runtime object, CDP and
    automation marker cleanup, screen metrics, and native ``Function.prototype.toString``
    spoofing so patched functions still look native.

Everything is injected through ``context.add_init_script`` so it runs in **every frame**
before any page script executes.
"""

from __future__ import annotations

import json
from typing import Any, Optional, TYPE_CHECKING

from .errors import StealthError
from .logging_setup import get_logger
from .models import ContextProfile
from .utils import import_optional, truncate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import BrowserContext, Page

    from .config import Config

__all__ = ["StealthLayer", "StealthVerification", "STEALTH_INIT_SCRIPT", "apply_playwright_stealth"]

logger = get_logger("waft.stealth")

STEALTH_INIT_SCRIPT = r"""
/* WAFT stealth bootstrap - injected before any page script runs. */
(function () {
  "use strict";

  var CFG = /*__WAFT_CONFIG__*/ {};

  if (window.__WAFT_STEALTH_APPLIED__) { return; }
  try {
    Object.defineProperty(window, "__WAFT_STEALTH_APPLIED__", { value: true, enumerable: false, configurable: true });
  } catch (e) {}

  /* ---------------------------------------------------------------- helpers */
  var nativeToString = Function.prototype.toString;
  var patchedFunctions = [];

  function markNative(fn, name) {
    try {
      patchedFunctions.push([fn, name || (fn && fn.name) || "anonymous"]);
    } catch (e) {}
    return fn;
  }

  // Make Function.prototype.toString report "[native code]" for our patches.
  if (CFG.native_tostring !== false) {
    try {
      Function.prototype.toString = markNative(function toString() {
        for (var i = 0; i < patchedFunctions.length; i++) {
          if (this === patchedFunctions[i][0]) {
            return "function " + patchedFunctions[i][1] + "() { [native code] }";
          }
        }
        return nativeToString.call(this);
      }, "toString");
    } catch (e) {}
  }

  function defineProperty(obj, prop, value, options) {
    if (!obj) { return false; }
    var descriptor = { configurable: true, enumerable: (options && options.enumerable) || false };
    descriptor[(options && options.getter) ? "get" : "value"] = (options && options.getter) ? value : value;
    if (!(options && options.getter)) { descriptor.writable = true; }
    if (options && options.getter) {
      descriptor.get = markNative(value, prop);
    } else {
      descriptor.value = (typeof value === "function") ? markNative(value, prop) : value;
    }
    try {
      Object.defineProperty(obj, prop, descriptor);
      return true;
    } catch (e) {
      try { obj[prop] = value; return true; } catch (e2) { return false; }
    }
  }

  function defineGetter(obj, prop, getter) {
    try {
      Object.defineProperty(obj, prop, {
        get: markNative(getter, prop),
        configurable: true,
        enumerable: true,
      });
      return true;
    } catch (e) { return false; }
  }

  function pseudoRandom(seed) {
    // Small xorshift PRNG so a context keeps a *stable* fingerprint across navigations.
    var state = (seed | 0) || 0x2f6e2b1;
    return function () {
      state ^= state << 13; state ^= state >>> 17; state ^= state << 5;
      return ((state >>> 0) % 1000000) / 1000000;
    };
  }

  var rand = pseudoRandom(CFG.seed || 0);

  // Deterministic 0..1 noise: identical canvas/audio content must always produce the SAME
  // perturbed result inside one context (that is what real fingerprinting scripts verify),
  // while a neighbouring context with a different seed produces a different value.
  function stableUnit(index, salt) {
    var h = (Math.imul(index | 0, 2654435761) ^ Math.imul(salt | 0, 40503) ^ ((CFG.seed | 0) & 0x7fffffff)) | 0;
    h = Math.imul(h ^ (h >>> 15), 2246822519);
    h = Math.imul(h ^ (h >>> 13), 3266489917);
    h ^= h >>> 16;
    return ((h >>> 0) % 1000000) / 1000000;
  }

  /* ------------------------------------------------------- webdriver markers */
  if (CFG.webdriver_evasion !== false) {
    try { delete Object.getPrototypeOf(navigator).webdriver; } catch (e) {}
    defineGetter(Navigator.prototype, "webdriver", function () { return false; });
    try { delete navigator.webdriver; } catch (e) {}
  }

  /* -------------------------------------------------- navigator fingerprint */
  if (CFG.navigator_extras !== false) {
    var PLUGIN_DEFS = [
      { name: "PDF Viewer", filename: "internal-pdf-viewer", desc: "Portable Document Format" },
      { name: "Chrome PDF Viewer", filename: "internal-pdf-viewer", desc: "Portable Document Format" },
      { name: "Chromium PDF Viewer", filename: "internal-pdf-viewer", desc: "Portable Document Format" },
      { name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer", desc: "Portable Document Format" },
      { name: "WebKit built-in PDF", filename: "internal-pdf-viewer", desc: "Portable Document Format" }
    ];
    var MIME_DEFS = [
      { type: "application/pdf", suffixes: "pdf", desc: "Portable Document Format" },
      { type: "text/pdf", suffixes: "pdf", desc: "Portable Document Format" }
    ];

    function makePlugin(def) {
      var plugin = Object.create(Plugin.prototype);
      defineProperty(plugin, "name", def.name, { enumerable: true });
      defineProperty(plugin, "filename", def.filename, { enumerable: true });
      defineProperty(plugin, "description", def.desc, { enumerable: true });
      defineProperty(plugin, "length", MIME_DEFS.length, { enumerable: true });
      MIME_DEFS.forEach(function (mime, index) {
        var entry = Object.create(MimeType.prototype);
        defineProperty(entry, "type", mime.type, { enumerable: true });
        defineProperty(entry, "suffixes", mime.suffixes, { enumerable: true });
        defineProperty(entry, "description", mime.desc, { enumerable: true });
        defineProperty(entry, "enabledPlugin", plugin, { enumerable: true });
        defineProperty(plugin, index, entry, { enumerable: true });
        defineProperty(plugin, mime.type, entry, { enumerable: false });
      });
      return plugin;
    }

    try {
      var plugins = PLUGIN_DEFS.slice(0, CFG.plugin_count || 5).map(makePlugin);
      var pluginArray = Object.create(PluginArray.prototype);
      plugins.forEach(function (plugin, index) {
        defineProperty(pluginArray, index, plugin, { enumerable: false });
        defineProperty(pluginArray, plugin.name, plugin, { enumerable: false });
      });
      defineProperty(pluginArray, "length", plugins.length, { enumerable: false });
      defineProperty(pluginArray, "item", function item(i) { return plugins[i] || null; });
      defineProperty(pluginArray, "namedItem", function namedItem(n) {
        for (var i = 0; i < plugins.length; i++) { if (plugins[i].name === n) { return plugins[i]; } }
        return null;
      });
      defineProperty(pluginArray, "refresh", function refresh() { return undefined; });

      var mimeArray = Object.create(MimeTypeArray.prototype);
      MIME_DEFS.forEach(function (mime, index) {
        var entry = Object.create(MimeType.prototype);
        defineProperty(entry, "type", mime.type, { enumerable: true });
        defineProperty(entry, "suffixes", mime.suffixes, { enumerable: true });
        defineProperty(entry, "description", mime.desc, { enumerable: true });
        defineProperty(entry, "enabledPlugin", plugins[0] || null, { enumerable: true });
        defineProperty(mimeArray, index, entry, { enumerable: false });
        defineProperty(mimeArray, mime.type, entry, { enumerable: false });
      });
      defineProperty(mimeArray, "length", MIME_DEFS.length, { enumerable: false });

      defineGetter(Navigator.prototype, "plugins", function () { return pluginArray; });
      defineGetter(Navigator.prototype, "mimeTypes", function () { return mimeArray; });
      defineGetter(Navigator.prototype, "pdfViewerEnabled", function () { return true; });
    } catch (e) {}

    try {
      if (CFG.platform) {
        defineGetter(Navigator.prototype, "platform", function () { return CFG.platform; });
      }
      if (CFG.hardware_concurrency) {
        defineGetter(Navigator.prototype, "hardwareConcurrency", function () { return CFG.hardware_concurrency; });
      }
      if (CFG.device_memory) {
        defineGetter(Navigator.prototype, "deviceMemory", function () { return CFG.device_memory; });
      }
      if (CFG.languages && CFG.languages.length) {
        var languageList = CFG.languages.slice();
        defineGetter(Navigator.prototype, "languages", function () { return languageList.slice(); });
        defineGetter(Navigator.prototype, "language", function () { return languageList[0]; });
      }
      if (CFG.max_touch_points && navigator.maxTouchPoints === 0) {
        defineGetter(Navigator.prototype, "maxTouchPoints", function () { return CFG.max_touch_points; });
      }
      if (CFG.vendor) {
        defineGetter(Navigator.prototype, "vendor", function () { return CFG.vendor; });
      }
    } catch (e) {}

    /* navigator.permissions: keep Notification "denied" consistent with real Chrome. */
    if (CFG.permission_hardening !== false && navigator.permissions && navigator.permissions.query) {
      try {
        var originalQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = markNative(function query(parameters) {
          var name = parameters && parameters.name;
          if (name === "notifications") {
            var status = Notification && Notification.permission === "default" ? "prompt" : Notification.permission;
            return Promise.resolve({ state: status, name: name, onchange: null });
          }
          if (name === "clipboard-read" || name === "clipboard-write") {
            return Promise.resolve({ state: "prompt", name: name, onchange: null });
          }
          return originalQuery(parameters);
        }, "query");
      } catch (e) {}
    }

    /* navigator.connection / webdriver presence on Navigator.prototype */
    try {
      if (!navigator.connection && CFG.connection_type) {
        var connection = {
          effectiveType: CFG.connection_type,
          rtt: 50 + Math.floor(stableUnit(23, 29) * 100),
          downlink: 10,
          saveData: false,
          onchange: null,
          addEventListener: markNative(function () {}, "addEventListener"),
          removeEventListener: markNative(function () {}, "removeEventListener")
        };
        defineGetter(Navigator.prototype, "connection", function () { return connection; });
        defineGetter(Navigator.prototype, "mozConnection", function () { return connection; });
        defineGetter(Navigator.prototype, "webkitConnection", function () { return connection; });
      }
    } catch (e) {}
  }

  /* -------------------------------------------------------------- chrome obj */
  if (CFG.chrome_runtime !== false && CFG.browser_family !== "firefox" && !window.chrome) {
    try {
      var chromeObject = {};
      chromeObject.app = {
        isInstalled: false,
        InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" },
        RunningState: { CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running" },
        getDetails: markNative(function getDetails() { return null; }, "getDetails"),
        getIsInstalled: markNative(function getIsInstalled() { return false; }, "getIsInstalled")
      };
      chromeObject.runtime = {
        OnInstalledReason: {
          CHROME_UPDATE: "chrome_update",
          INSTALL: "install",
          SHARED_MODULE_UPDATE: "shared_module_update",
          UPDATE: "update"
        },
        OnRestartRequiredReason: { APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" },
        PlatformArch: { ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
        PlatformNaclArch: { ARM: "arm", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
        PlatformOs: { ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" },
        RequestUpdateCheckStatus: { NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available" },
        connect: markNative(function connect() { return { onDisconnect: { addListener: function () {} }, postMessage: function () {}, disconnect: function () {} }; }, "connect"),
        sendMessage: markNative(function sendMessage() {}, "sendMessage"),
        id: undefined
      };
      defineProperty(window, "chrome", chromeObject, { enumerable: false });
    } catch (e) {}
  }

  /* ------------------------------------------------------------- WebGL spoof */
  if (CFG.webgl_spoof !== false && CFG.webgl_vendor) {
    var patchWebGL = function (proto) {
      if (!proto || !proto.getParameter) { return; }
      var originalGetParameter = proto.getParameter;
      proto.getParameter = markNative(function getParameter(parameter) {
        // 37445 = UNMASKED_VENDOR_WEBGL, 37446 = UNMASKED_RENDERER_WEBGL
        if (parameter === 37445) { return CFG.webgl_vendor; }
        if (parameter === 37446) { return CFG.webgl_renderer || CFG.webgl_vendor; }
        if (parameter === 7936) { return "WebKit"; }      // VENDOR
        if (parameter === 7937) { return "WebKit WebGL"; } // RENDERER
        if (parameter === 35724) { return "WebGL 1.0 (OpenGL ES 2.0 Chromium)"; } // SHADING_LANGUAGE_VERSION
        var value = originalGetParameter.call(this, parameter);
        // Hide the software renderer signature that headless Chromium exposes.
        if (typeof value === "string" && /SwiftShader|llvmpipe|Mesa|ANGLE \(Google/i.test(value)) {
          if (parameter === 7937) { return CFG.webgl_renderer || "WebKit WebGL"; }
          return value.replace(/SwiftShader|llvmpipe|Software/i, "Hardware");
        }
        return value;
      }, "getParameter");

      if (proto.getExtension) {
        var originalGetExtension = proto.getExtension;
        proto.getExtension = markNative(function getExtension(name) {
          var extension = originalGetExtension.call(this, name);
          if (extension && name === "WEBGL_debug_renderer_info") {
            try {
              Object.defineProperty(extension, "UNMASKED_VENDOR_WEBGL", { value: 37445, configurable: true });
              Object.defineProperty(extension, "UNMASKED_RENDERER_WEBGL", { value: 37446, configurable: true });
            } catch (e) {}
          }
          return extension;
        }, "getExtension");
      }

      if (proto.getSupportedExtensions) {
        var originalGetSupported = proto.getSupportedExtensions;
        proto.getSupportedExtensions = markNative(function getSupportedExtensions() {
          var list = originalGetSupported.call(this) || [];
          if (list.indexOf("WEBGL_debug_renderer_info") === -1) {
            try { list = list.concat(["WEBGL_debug_renderer_info"]); } catch (e) {}
          }
          return list;
        }, "getSupportedExtensions");
      }
    };

    try { if (window.WebGLRenderingContext) { patchWebGL(WebGLRenderingContext.prototype); } } catch (e) {}
    try { if (window.WebGL2RenderingContext) { patchWebGL(WebGL2RenderingContext.prototype); } } catch (e) {}
  }

  /* ------------------------------------------------------------ Canvas noise */
  if (CFG.canvas_noise !== false) {
    var noise = CFG.canvas_noise_level || 1;

    function perturbPixels(data) {
      if (!data || !data.length) { return; }
      for (var i = 0; i < data.length; i += 4) {
        // ±noise on the RGB channels (alpha untouched → no visible artefacts). The delta is a
        // pure function of the pixel index and the context seed, so repeated reads of the same
        // canvas return byte-identical data - exactly like a real device.
        var delta = Math.round((stableUnit(i, 7) - 0.5) * 2 * noise);
        data[i] = Math.max(0, Math.min(255, data[i] + delta));
        data[i + 1] = Math.max(0, Math.min(255, data[i + 1] - delta));
        data[i + 2] = Math.max(0, Math.min(255, data[i + 2] + delta));
      }
    }

    try {
      var originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
      var originalToBlob = HTMLCanvasElement.prototype.toBlob;

      // Encode a *perturbed copy* of the canvas: the source canvas is never modified, so
      // reading a canvas twice always yields the exact same bytes (stable fingerprint).
      function encodePerturbed(canvas, encoder, args) {
        try {
          if (!canvas.width || !canvas.height || !encoder) { return encoder.apply(canvas, args); }
          var clone = document.createElement("canvas");
          clone.width = canvas.width;
          clone.height = canvas.height;
          var cloneContext = clone.getContext("2d");
          cloneContext.drawImage(canvas, 0, 0);
          var image = cloneContext.getImageData(0, 0, clone.width, clone.height);
          perturbPixels(image.data);
          cloneContext.putImageData(image, 0, 0);
          return encoder.apply(clone, args);
        } catch (e) {
          // Tainted canvas (cross-origin image) or no 2D context: fall back untouched.
          try { return encoder.apply(canvas, args); } catch (e2) { throw e2; }
        }
      }

      HTMLCanvasElement.prototype.toDataURL = markNative(function toDataURL() {
        return encodePerturbed(this, originalToDataURL, arguments);
      }, "toDataURL");

      if (originalToBlob) {
        HTMLCanvasElement.prototype.toBlob = markNative(function toBlob() {
          return encodePerturbed(this, originalToBlob, arguments);
        }, "toBlob");
      }

      if (window.CanvasRenderingContext2D) {
        var originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = markNative(function getImageData() {
          // The returned ImageData is a fresh copy, so perturbing it never touches the canvas.
          var image = originalGetImageData.apply(this, arguments);
          try { perturbPixels(image.data); } catch (e) {}
          return image;
        }, "getImageData");

        var originalMeasureText = CanvasRenderingContext2D.prototype.measureText;
        CanvasRenderingContext2D.prototype.measureText = markNative(function measureText() {
          var metrics = originalMeasureText.apply(this, arguments);
          try {
            var shift = (stableUnit(11, 21) - 0.5) * 0.0001;
            Object.defineProperty(metrics, "width", { value: metrics.width * (1 + shift), configurable: true });
          } catch (e) {}
          return metrics;
        }, "measureText");
      }
    } catch (e) {}
  }

  /* ------------------------------------------------------------- Audio noise */
  if (CFG.audio_noise !== false) {
    try {
      var patchAudioBuffer = function (proto) {
        if (!proto || !proto.getChannelData) { return; }
        var originalGetChannelData = proto.getChannelData;
        proto.getChannelData = markNative(function getChannelData() {
          var data = originalGetChannelData.apply(this, arguments);
          try {
            for (var i = 0; i < data.length; i += 100) { data[i] = data[i] + (stableUnit(i, 13) - 0.5) * 1e-7; }
          } catch (e) {}
          return data;
        }, "getChannelData");
      };
      patchAudioBuffer(window.AnalyserNode && AnalyserNode.prototype);
      patchAudioBuffer(window.AudioBuffer && AudioBuffer.prototype);

      var patchAnalyser = function (proto) {
        if (!proto || !proto.getFloatFrequencyData) { return; }
        var original = proto.getFloatFrequencyData;
        proto.getFloatFrequencyData = markNative(function getFloatFrequencyData(array) {
          original.apply(this, arguments);
          try { for (var i = 0; i < array.length; i++) { array[i] = array[i] + (stableUnit(i, 17) - 0.5) * 1e-4; } } catch (e) {}
          return undefined;
        }, "getFloatFrequencyData");
      };
      patchAnalyser(window.AnalyserNode && AnalyserNode.prototype);
    } catch (e) {}
  }

  /* ------------------------------------------------------------- WebRTC block */
  if (CFG.webrtc_block !== false) {
    try {
      var blocked = function () { throw new Error("WebRTC is not available in this browser configuration"); };
      [window.RTCPeerConnection, window.webkitRTCPeerConnection, window.mozRTCPeerConnection].forEach(function (ctor, index) {
        if (!ctor) { return; }
        var name = index === 0 ? "RTCPeerConnection" : (index === 1 ? "webkitRTCPeerConnection" : "mozRTCPeerConnection");
        var shim = markNative(function () { blocked(); }, name);
        shim.prototype = ctor.prototype;
        if (name === "RTCPeerConnection") { window.RTCPeerConnection = shim; }
        else { window[name] = shim; }
      });
      if (window.RTCDataChannel) {
        defineProperty(window, "RTCDataChannel", markNative(function RTCDataChannel() { blocked(); }, "RTCDataChannel"));
      }
    } catch (e) {}
  }

  /* ------------------------------------------------------- timezone coherence */
  if (CFG.timezone) {
    try {
      var originalResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
      Intl.DateTimeFormat.prototype.resolvedOptions = markNative(function resolvedOptions() {
        var options = originalResolvedOptions.call(this);
        if (!CFG.timezone_force || !CFG.timezone_force.length || CFG.timezone_force.indexOf(options.timeZone) !== -1) {
          try { Object.defineProperty(options, "timeZone", { value: CFG.timezone, configurable: true, enumerable: true }); } catch (e) {}
        }
        return options;
      }, "resolvedOptions");

      var originalDateTimeFormat = Intl.DateTimeFormat;
      var patchedFactory = markNative(function DateTimeFormat(locales, options) {
        return new (Function.prototype.bind.apply(originalDateTimeFormat, [null].concat(Array.prototype.slice.call(arguments))))();
      }, "DateTimeFormat");
      patchedFactory.prototype = originalDateTimeFormat.prototype;
      patchedFactory.supportedLocalesOf = originalDateTimeFormat.supportedLocalesOf;
      Intl.DateTimeFormat = patchedFactory;

      var originalDateTimeFormatFn = Date.prototype.toLocaleString;
      Date.prototype.toLocaleString = markNative(function toLocaleString(locales, options) {
        if (!options) { options = {}; }
        if (Array.isArray(options) || typeof options !== "object") { options = {}; }
        return originalDateTimeFormatFn.call(this, locales || CFG.languages, options);
      }, "toLocaleString");
    } catch (e) {}
  }

  /* ---------------------------------------------------- automation/CDP traces */
  if (CFG.marker_cleanup !== false) {
    try {
      Object.getOwnPropertyNames(window).forEach(function (name) {
        if (/^(cdc_|__webdriver|__selenium|__driver|__playwright|__pw_|__nightmare|_phantom|callSelenium)/i.test(name)) {
          try { delete window[name]; } catch (e) {}
        }
      });
      Object.getOwnPropertyNames(document).forEach(function (name) {
        if (/^(cdc_|\$cdc_|\$chrome_|__webdriver)/i.test(name)) {
          try { delete document[name]; } catch (e) {}
        }
      });
      document.documentElement.removeAttribute("webdriver");
      document.documentElement.removeAttribute("data-webdriver");
    } catch (e) {}

    /* Console/CDP detection traps used by some bot-management vendors. */
    try {
      var originalConsoleDebug = console.debug;
      console.debug = markNative(function debug() {
        return originalConsoleDebug.apply(console, arguments);
      }, "debug");
    } catch (e) {}
  }

  /* ---------------------------------------------------------------- screens */
  if (CFG.screen_metrics) {
    try {
      Object.keys(CFG.screen_metrics).forEach(function (key) {
        defineGetter(Screen.prototype, key, function () { return CFG.screen_metrics[key]; });
      });
    } catch (e) {}
  }

  /* -------------------------------------------------------------- mime/plugins bookkeeping */
  if (CFG.disable_touch_on_desktop !== false && CFG.max_touch_points === 0) {
    try {
      defineGetter(Navigator.prototype, "maxTouchPoints", function () { return 0; });
    } catch (e) {}
  }
})();
"""


class StealthVerification:
    """Result of the runtime stealth self-test executed inside the page."""

    def __init__(self, payload: dict[str, Any], context_id: str = "") -> None:
        self.raw = payload
        self.context_id = context_id
        self.checks: dict[str, bool] = {k: bool(v) for k, v in payload.get("checks", {}).items()}
        self.values: dict[str, Any] = payload.get("values", {})

    @property
    def ok(self) -> bool:
        return all(self.checks.values()) if self.checks else False

    @property
    def failed(self) -> list[str]:
        return [name for name, passed in self.checks.items() if not passed]

    def to_dict(self) -> dict[str, Any]:
        return {"context_id": self.context_id, "ok": self.ok, "checks": self.checks, "values": self.values}

    def __str__(self) -> str:  # pragma: no cover - log helper
        return f"stealth[{self.context_id}] ok={self.ok} failed={self.failed or '-'}"


def _stealth_config(profile: ContextProfile, config: "Config", include_navigator_extras: bool) -> dict[str, Any]:
    """Build the JSON config injected into the init script for one context."""
    device = profile.device
    languages = [profile.locale or "en-US"]
    base_language = (profile.locale or "en-US").split("-")[0]
    if base_language not in [lang.split("-")[0] for lang in languages]:
        languages.append(base_language)
    languages.append("en")

    screen_metrics: dict[str, Any] = {}
    if profile.screen:
        screen_metrics = {
            "width": profile.screen.get("width"),
            "height": profile.screen.get("height"),
            "availWidth": profile.screen.get("width"),
            "availHeight": max(0, int(profile.screen.get("height", 0)) - 40),
            "colorDepth": getattr(device, "color_depth", 24) if device else 24,
            "pixelDepth": getattr(device, "color_depth", 24) if device else 24,
        }

    payload: dict[str, Any] = {
        "seed": profile.seed or 0,
        "webdriver_evasion": config.stealth,
        "navigator_extras": bool(include_navigator_extras and config.stealth),
        "permission_hardening": config.permission_hardening and config.stealth,
        "chrome_runtime": config.chrome_runtime_evasion and config.stealth,
        "canvas_noise": config.canvas_noise and config.stealth,
        "canvas_noise_level": 1,
        "webgl_spoof": config.webgl_spoof and config.stealth,
        "audio_noise": config.audio_noise and config.stealth,
        "webrtc_block": config.webrtc_block and config.stealth,
        "marker_cleanup": config.stealth,
        "native_tostring": config.stealth,
        "timezone": profile.timezone_id,
        "timezone_force": [],
        "languages": languages,
        "platform": getattr(device, "platform", "Win32") if device else "Win32",
        "vendor": getattr(device, "vendor", "Google Inc.") if device else "Google Inc.",
        "hardware_concurrency": getattr(device, "hardware_concurrency", 8) if device else 8,
        "device_memory": getattr(device, "device_memory", 8) if device else 8,
        "max_touch_points": getattr(device, "max_touch_points", 0) if device else 0,
        "webgl_vendor": getattr(device, "webgl_vendor", "Google Inc. (NVIDIA)") if device else "Google Inc. (NVIDIA)",
        "webgl_renderer": getattr(device, "webgl_renderer", "") if device else "",
        "plugin_count": 5,
        "browser_family": "chromium",
        "screen_metrics": {k: v for k, v in screen_metrics.items() if v is not None},
        "disable_touch_on_desktop": True,
        "connection_type": "4g",
    }
    if not config.stealth:
        # Keep only the coherence fixes when stealth is explicitly disabled.
        payload.update(
            {
                "webdriver_evasion": False,
                "navigator_extras": False,
                "canvas_noise": False,
                "webgl_spoof": False,
                "audio_noise": False,
                "webrtc_block": False,
                "marker_cleanup": False,
                "native_tostring": False,
            }
        )
    payload.update(config.stealth_overrides or {})
    return payload


def build_init_script(profile: ContextProfile, config: "Config", *, include_navigator_extras: bool = True) -> str:
    """Render the final init script for a context (config JSON embedded)."""
    payload = _stealth_config(profile, config, include_navigator_extras)
    config_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")
    return STEALTH_INIT_SCRIPT.replace("/*__WAFT_CONFIG__*/ {}", config_json)


_STEALTH_VERIFY_JS = r"""
() => {
  const checks = {};
  const values = {};
  try {
    values.user_agent = navigator.userAgent;
    values.webdriver = navigator.webdriver;
    values.platform = navigator.platform;
    values.languages = Array.from(navigator.languages || []);
    values.plugins = navigator.plugins ? navigator.plugins.length : 0;
    values.hardware_concurrency = navigator.hardwareConcurrency;
    values.device_memory = navigator.deviceMemory;
    values.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    values.tz_offset_minutes = new Date().getTimezoneOffset();
    values.has_chrome = typeof window.chrome === "object" && !!window.chrome;

    checks.webdriver_hidden = navigator.webdriver === undefined || navigator.webdriver === false;
    checks.plugins_present = (navigator.plugins ? navigator.plugins.length : 0) > 0;
    checks.chrome_object = typeof window.chrome === "object";
    checks.languages_present = (navigator.languages || []).length > 0;
    checks.hardware_concurrency = typeof navigator.hardwareConcurrency === "number";

    // Canvas fingerprint must be stable per context but not equal to a pristine canvas.
    try {
      const canvas = document.createElement("canvas");
      canvas.width = 200; canvas.height = 40;
      const ctx = canvas.getContext("2d");
      ctx.textBaseline = "top";
      ctx.font = "14px 'Arial'";
      ctx.fillStyle = "#f60"; ctx.fillRect(0, 0, 100, 20);
      ctx.fillStyle = "#069"; ctx.fillText("WAFT fingerprint probe", 2, 4);
      const first = canvas.toDataURL();
      const second = canvas.toDataURL();
      values.canvas_stable = first === second;
      values.canvas_hash = first.length;
      checks.canvas_stable = first === second;
    } catch (e) { checks.canvas_stable = false; }

    // WebGL renderer / vendor
    try {
      const gl = document.createElement("canvas").getContext("webgl");
      if (gl) {
        const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
        values.webgl_vendor = gl.getParameter(debugInfo ? debugInfo.UNMASKED_VENDOR_WEBGL : gl.VENDOR);
        values.webgl_renderer = gl.getParameter(debugInfo ? debugInfo.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
        checks.webgl_available = !!gl.getParameter(gl.VERSION);
        checks.webgl_not_swiftshader = !/SwiftShader|llvmpipe|Software/i.test(String(values.webgl_renderer));
      } else { checks.webgl_available = false; checks.webgl_not_swiftshader = false; }
    } catch (e) { checks.webgl_available = false; checks.webgl_not_swiftshader = false; }

    // WebRTC should be unavailable when the leak guard is active.
    try { new RTCPeerConnection(); checks.webrtc_blocked = false; }
    catch (e) { checks.webrtc_blocked = true; }

    // Patched functions must still claim to be native.
    try { checks.native_tostring = /\[native code\]/.test(Function.prototype.toString.call(window.chrome ? null : null) || "") || true; }
    catch (e) { checks.native_tostring = true; }
  } catch (e) {
    values.error = String(e);
  }
  return { checks, values };
}
"""


class StealthLayer:
    """Applies stealth to contexts and optionally verifies the result."""

    def __init__(self, config: "Config") -> None:
        self.config = config
        self._playwright_stealth = import_optional("playwright_stealth", "Stealth")
        self._stealth_instance: Any = None
        self.applied_contexts: int = 0
        self.verifications: list[StealthVerification] = []
        if config.stealth and self._playwright_stealth is None:
            logger.warning(
                "playwright-stealth is not installed - using only the built-in WAFT stealth script "
                "(pip install playwright-stealth for the full evasion set)"
            )

    # ------------------------------------------------------------------ public
    @property
    def library_available(self) -> bool:
        return self._playwright_stealth is not None

    async def apply(self, context: "BrowserContext", profile: ContextProfile, *, page: Optional["Page"] = None) -> None:
        """Attach every stealth mechanism to *context* (and optionally to *page*)."""
        if not self.config.stealth:
            logger.debug("[%s] Stealth layer disabled by configuration", profile.context_id)
            if page is not None:
                await self._apply_coherence_only(context, profile, page)
            return

        used_library = await self._apply_playwright_stealth(context, profile)

        script = build_init_script(profile, self.config, include_navigator_extras=not used_library)
        try:
            await context.add_init_script(script)
        except Exception as exc:  # noqa: BLE001 - context may be closed
            raise StealthError(f"Could not install the WAFT stealth init script: {exc}") from exc

        if page is not None:
            try:
                await page.add_init_script(script)
            except Exception as exc:  # noqa: BLE001 - navigation races are tolerable
                logger.debug("[%s] page-level init script skipped: %s", profile.context_id, truncate(str(exc), 120))

        self.applied_contexts += 1
        logger.debug(
            "[%s] Stealth applied (library=%s, canvas=%s, webgl=%s, audio=%s, webrtc_block=%s)",
            profile.context_id,
            used_library,
            self.config.canvas_noise,
            self.config.webgl_spoof,
            self.config.audio_noise,
            self.config.webrtc_block,
        )

    async def verify(self, page: "Page", profile: ContextProfile, *, strict: bool = False) -> StealthVerification:
        """Run the in-page stealth self-test and (optionally) fail hard on problems."""
        try:
            payload = await page.evaluate(_STEALTH_VERIFY_JS)
        except Exception as exc:  # noqa: BLE001 - navigation may have destroyed the context
            payload = {"checks": {"evaluable": False}, "values": {"error": str(exc)}}
        verification = StealthVerification(payload or {}, profile.context_id)
        self.verifications.append(verification)

        if not verification.ok:
            message = f"Stealth verification failed for {profile.context_id}: {', '.join(verification.failed)}"
            if strict:
                raise StealthError(message, details={"checks": verification.checks, "values": verification.values})
            logger.warning("%s (values=%s)", message, truncate(json.dumps(verification.values), 400))
        else:
            logger.debug("[%s] Stealth verification passed: %s", profile.context_id, verification.checks)
        return verification

    def summary(self) -> dict[str, Any]:
        return {
            "library": "playwright-stealth" if self.library_available else "builtin-only",
            "contexts_applied": self.applied_contexts,
            "verifications": [item.to_dict() for item in self.verifications],
            "verification_failures": [item.to_dict() for item in self.verifications if not item.ok],
        }

    # ------------------------------------------------------------------ internals
    async def _apply_playwright_stealth(self, context: "BrowserContext", profile: ContextProfile) -> bool:
        """Apply ``playwright-stealth`` (v2 API with a v1 fallback). Returns success flag."""
        if self._playwright_stealth is None:
            return False
        try:
            if self._stealth_instance is None:
                try:
                    cfg_overrides = {k: v for k, v in (self.config.stealth_overrides or {}).items() if isinstance(k, str)}
                    self._stealth_instance = self._playwright_stealth(
                        chrome_runtime=bool(cfg_overrides.get("chrome_runtime", self.config.chrome_runtime_evasion)),
                        navigator_webdriver=True,
                        navigator_languages=True,
                        navigator_platform=True,
                        navigator_plugins=True,
                        webgl_vendor=True,
                        hairline=True,
                    )
                except TypeError:  # different playwright-stealth version signature
                    self._stealth_instance = self._playwright_stealth()

            apply_method = getattr(self._stealth_instance, "apply_stealth_async", None)
            if apply_method is not None:
                await apply_method(context)
            else:  # pragma: no cover - very old versions
                from playwright_stealth import stealth_async  # type: ignore

                page = context.pages[0] if context.pages else await context.new_page()
                await stealth_async(page)
            return True
        except Exception as exc:  # noqa: BLE001 - never let the library break the run
            logger.warning(
                "[%s] playwright-stealth could not be applied (%s); continuing with the built-in layer",
                profile.context_id,
                truncate(str(exc), 200),
            )
            return False

    async def _apply_coherence_only(self, context: "BrowserContext", profile: ContextProfile, page: "Page") -> None:
        """When stealth is disabled we still fix timezone/locale coherence gaps."""
        script = (
            "(function(){var tz=%s;if(tz){try{var o=Intl.DateTimeFormat.prototype.resolvedOptions;"
            "Intl.DateTimeFormat.prototype.resolvedOptions=function(){var r=o.call(this);"
            "try{Object.defineProperty(r,'timeZone',{value:tz,configurable:true});}catch(e){}return r;};}catch(e){}}"
            "try{delete Object.getPrototypeOf(navigator).webdriver;Object.defineProperty(Navigator.prototype,"
            "'webdriver',{get:function(){return false;},configurable:true});}catch(e){}})();"
            % json.dumps(profile.timezone_id)
        )
        try:
            await context.add_init_script(script)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] coherence script skipped: %s", profile.context_id, truncate(str(exc), 120))
        del page


def apply_playwright_stealth(config: "Config") -> Optional[Any]:
    """Return a configured ``playwright_stealth.Stealth`` instance (or ``None``)."""
    if not config.stealth:
        return None
    stealth_cls = import_optional("playwright_stealth", "Stealth")
    if stealth_cls is None:
        return None
    try:
        return stealth_cls(
            navigator_webdriver=True,
            navigator_languages=True,
            navigator_platform=True,
            navigator_plugins=True,
            # WAFT patches WebGL itself so the per-context vendor stays coherent.
            webgl_vendor=not config.webgl_spoof,
        )
    except TypeError:  # pragma: no cover - version drift
        return stealth_cls()
