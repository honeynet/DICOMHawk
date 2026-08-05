/* Browser fingerprint collector. Third-party notices: ../ATTRIBUTION.md. */
(function () {
  'use strict';

  var script = document.currentScript;
  if (!script) return;
  var enabled = (script.getAttribute('data-signals') || '').split(',');
  var ingest = script.getAttribute('data-ingest');
  if (!ingest) return;

  var M = Math;
  var fallbackFn = function () { return 0; };

  // Engine is probed from features, never the User-Agent, which a bot can spoof.

  function countTruthy(values) {
    var total = 0;
    for (var i = 0; i < values.length; i++) if (values[i]) total += 1;
    return total;
  }

  function isChromium() {
    var w = window;
    var n = navigator;
    return countTruthy([
      'webkitPersistentStorage' in n,
      'webkitTemporaryStorage' in n,
      (n.vendor || '').indexOf('Google') === 0,
      'webkitResolveLocalFileSystemURL' in w,
      'BatteryManager' in w,
      'webkitMediaStream' in w,
      'webkitSpeechGrammar' in w
    ]) >= 5;
  }

  function isWebKit() {
    var w = window;
    var n = navigator;
    return countTruthy([
      'ApplePayError' in w,
      'CSSPrimitiveValue' in w,
      'Counter' in w,
      (n.vendor || '').indexOf('Apple') === 0,
      'RGBColor' in w,
      'WebKitMediaKeys' in w
    ]) >= 4;
  }

  function isGecko() {
    var w = window;
    return countTruthy([
      'buildID' in navigator,
      'MozAppearance' in ((document.documentElement && document.documentElement.style) || {}),
      'onmozfullscreenchange' in w,
      'mozInnerScreenX' in w,
      'CSSMozDocumentRule' in w,
      'CanvasCaptureMediaStream' in w
    ]) >= 4;
  }

  function getBrowserEngineKind() {
    if (isChromium()) return 'Chromium';
    if (isWebKit()) return 'Webkit';
    if (isGecko()) return 'Gecko';
    return 'Unknown';
  }

  function getBrowserKind() {
    var userAgent = (navigator.userAgent || '').toLowerCase();
    if (userAgent.indexOf('edg/') !== -1) return 'Edge';
    if (userAgent.indexOf('trident') !== -1 || userAgent.indexOf('msie') !== -1) return 'IE';
    if (userAgent.indexOf('wechat') !== -1) return 'WeChat';
    if (userAgent.indexOf('firefox') !== -1) return 'Firefox';
    if (userAgent.indexOf('opera') !== -1 || userAgent.indexOf('opr') !== -1) return 'Opera';
    if (userAgent.indexOf('chrome') !== -1) return 'Chrome';
    if (userAgent.indexOf('safari') !== -1) return 'Safari';
    return 'Unknown';
  }

  function isDesktopWebKit() {
    var w = window;
    var HTMLElementRef = w.HTMLElement;
    var DocumentRef = w.Document;
    return countTruthy([
      'safari' in w,
      !('ongestureend' in w),
      !('TouchEvent' in w),
      !('orientation' in w),
      HTMLElementRef && !('autocapitalize' in HTMLElementRef.prototype),
      DocumentRef && 'pointerLockElement' in DocumentRef.prototype
    ]) >= 4;
  }

  function isIPad() {
    if (navigator.platform === 'iPad') return true;
    var ratio = screen.width / screen.height;
    return countTruthy([
      'MediaSource' in window,
      !!Element.prototype.webkitRequestFullscreen,
      ratio > 0.65 && ratio < 1.53
    ]) >= 2;
  }

  function isChromium86OrNewer() {
    var w = window;
    return countTruthy([
      !('MediaSettingsRange' in w),
      'RTCEncodedAudioFrame' in w,
      '' + w.Intl === '[object Intl]',
      '' + w.Reflect === '[object Reflect]'
    ]) >= 3;
  }

  function isAndroid() {
    var engine = getBrowserEngineKind();
    var w = window;
    var n = navigator;
    if (engine === 'Chromium') {
      return countTruthy([
        !('SharedWorker' in w),
        n.connection && 'ontypechange' in n.connection,
        !('sinkId' in new Audio())
      ]) >= 2;
    }
    if (engine === 'Gecko') {
      return countTruthy([
        'onorientationchange' in w,
        'orientation' in w,
        /android/i.test(n.appVersion)
      ]) >= 2;
    }
    return false;
  }

  function getPlatform() {
    var platform = navigator.platform;
    // iOS reports MacIntel in desktop mode; M1 Macs report it genuinely.
    if (platform === 'MacIntel' && isWebKit() && !isDesktopWebKit()) {
      return isIPad() ? 'iPad' : 'iPhone';
    }
    return platform;
  }

  function getLanguages() {
    var n = navigator;
    var result = [];
    var language = n.language || n.userLanguage || n.browserLanguage || n.systemLanguage;
    if (language !== undefined) result.push([language]);
    if (Array.isArray(n.languages)) {
      // Chromium 86+ incognito exposes only navigator.language here, so the list is skipped.
      if (!(isChromium() && isChromium86OrNewer())) result.push(n.languages);
    } else if (typeof n.languages === 'string' && n.languages) {
      result.push(n.languages.split(','));
    }
    return result;
  }

  function getTimezoneOffset() {
    var year = new Date().getFullYear();
    // DST shifts the offset, and the season differs by hemisphere, so take the non-DST one.
    return M.max(
      new Date(year, 0, 1).getTimezoneOffset(),
      new Date(year, 6, 1).getTimezoneOffset()
    );
  }

  function getTimezone() {
    var DateTimeFormat = window.Intl && window.Intl.DateTimeFormat;
    if (DateTimeFormat) {
      var timezone = new DateTimeFormat().resolvedOptions().timeZone;
      if (timezone) return timezone;
    }
    var offset = -getTimezoneOffset();
    return 'UTC' + (offset >= 0 ? '+' : '') + offset;
  }

  function makeCanvasContext() {
    var canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    return [canvas, canvas.getContext('2d')];
  }

  function doesSupportWinding(context) {
    context.rect(0, 0, 10, 10);
    context.rect(2, 2, 6, 6);
    return !context.isPointInPath(5, 5, 'evenodd');
  }

  function renderTextImage(canvas, context) {
    canvas.width = 240;
    canvas.height = 60;
    context.textBaseline = 'alphabetic';
    context.fillStyle = '#f60';
    context.fillRect(100, 1, 62, 20);
    context.fillStyle = '#069';
    context.font = '11pt "Times New Roman"';
    var printedText = 'Cwm fjordbank gly ' + String.fromCharCode(55357, 56835);
    context.fillText(printedText, 2, 15);
    context.fillStyle = 'rgba(102, 204, 0, 0.2)';
    context.font = '18pt Arial';
    context.fillText(printedText, 4, 45);
  }

  function renderGeometryImage(canvas, context) {
    canvas.width = 122;
    canvas.height = 110;
    context.globalCompositeOperation = 'multiply';
    var circles = [['#f2f', 40, 40], ['#2ff', 80, 40], ['#ff2', 60, 80]];
    for (var i = 0; i < circles.length; i++) {
      context.fillStyle = circles[i][0];
      context.beginPath();
      context.arc(circles[i][1], circles[i][2], 40, 0, M.PI * 2, true);
      context.closePath();
      context.fill();
    }
    context.fillStyle = '#f9c';
    context.arc(60, 60, 60, 0, M.PI * 2, true);
    context.arc(60, 60, 20, 0, M.PI * 2, true);
    context.fill('evenodd');
  }

  function getCanvas() {
    var pair = makeCanvasContext();
    var canvas = pair[0];
    var context = pair[1];
    if (!context || !canvas.toDataURL) {
      return { winding: false, geometry: 'unsupported', text: 'unsupported' };
    }
    var winding = doesSupportWinding(context);
    renderTextImage(canvas, context);
    var textImage1 = canvas.toDataURL();
    var textImage2 = canvas.toDataURL();
    // Two identical renders that differ means the browser is noising the canvas: that is intel.
    if (textImage1 !== textImage2) {
      return { winding: winding, geometry: 'unstable', text: 'unstable' };
    }
    renderGeometryImage(canvas, context);
    return { winding: winding, geometry: canvas.toDataURL(), text: textImage1 };
  }

  var STATUS_NO_GL_CONTEXT = -1;
  var STATUS_GET_PARAMETER_NOT_A_FUNCTION = -2;

  function getWebGLContext() {
    var canvas = document.createElement('canvas');
    var context;
    canvas.addEventListener('webglCreateContextError', function () { context = undefined; });
    var types = ['webgl', 'experimental-webgl'];
    for (var i = 0; i < types.length; i++) {
      try {
        context = canvas.getContext(types[i]);
      } catch (error) { /* continue */ }
      if (context) break;
    }
    return context;
  }

  function getWebGl() {
    var gl = getWebGLContext();
    if (!gl) return STATUS_NO_GL_CONTEXT;
    if (typeof gl.getParameter !== 'function') return STATUS_GET_PARAMETER_NOT_A_FUNCTION;
    // Gecko prints a console warning for this extension, and upstream avoids it there.
    var debugExtension = isGecko() ? null : gl.getExtension('WEBGL_debug_renderer_info');
    var read = function (parameter) {
      var value = gl.getParameter(parameter);
      return value === null || value === undefined ? '' : String(value);
    };
    return {
      version: read(gl.VERSION),
      vendor: read(gl.VENDOR),
      vendorUnmasked: debugExtension ? read(debugExtension.UNMASKED_VENDOR_WEBGL) : '',
      renderer: read(gl.RENDERER),
      rendererUnmasked: debugExtension ? read(debugExtension.UNMASKED_RENDERER_WEBGL) : '',
      shadingLanguageVersion: read(gl.SHADING_LANGUAGE_VERSION)
    };
  }

  function getMath() {
    var acos = M.acos || fallbackFn;
    var acosh = M.acosh || fallbackFn;
    var asin = M.asin || fallbackFn;
    var asinh = M.asinh || fallbackFn;
    var atanh = M.atanh || fallbackFn;
    var atan = M.atan || fallbackFn;
    var sin = M.sin || fallbackFn;
    var sinh = M.sinh || fallbackFn;
    var cos = M.cos || fallbackFn;
    var cosh = M.cosh || fallbackFn;
    var tan = M.tan || fallbackFn;
    var tanh = M.tanh || fallbackFn;
    var exp = M.exp || fallbackFn;
    var expm1 = M.expm1 || fallbackFn;
    var log1p = M.log1p || fallbackFn;
    var powPI = function (value) { return M.pow(M.PI, value); };
    var acoshPf = function (value) { return M.log(value + M.sqrt(value * value - 1)); };
    var asinhPf = function (value) { return M.log(value + M.sqrt(value * value + 1)); };
    var atanhPf = function (value) { return M.log((1 + value) / (1 - value)) / 2; };
    var sinhPf = function (value) { return M.exp(value) - 1 / M.exp(value) / 2; };
    var coshPf = function (value) { return (M.exp(value) + 1 / M.exp(value)) / 2; };
    var expm1Pf = function (value) { return M.exp(value) - 1; };
    var tanhPf = function (value) { return (M.exp(2 * value) - 1) / (M.exp(2 * value) + 1); };
    var log1pPf = function (value) { return M.log(1 + value); };
    return {
      acos: acos(0.123124234234234242), acosh: acosh(1e308), acoshPf: acoshPf(1e154),
      asin: asin(0.123124234234234242), asinh: asinh(1), asinhPf: asinhPf(1),
      atanh: atanh(0.5), atanhPf: atanhPf(0.5), atan: atan(0.5),
      sin: sin(-1e300), sinh: sinh(1), sinhPf: sinhPf(1),
      cos: cos(10.000000000123), cosh: cosh(1), coshPf: coshPf(1),
      tan: tan(-1e300), tanh: tanh(1), tanhPf: tanhPf(1),
      exp: exp(1), expm1: expm1(1), expm1Pf: expm1Pf(1),
      log1p: log1p(10), log1pPf: log1pPf(10), powPI: powPI(-100)
    };
  }

  function getScreenResolution() {
    // Some privacy tools return these as strings or non-numbers, hence the NaN replacement.
    var parse = function (value) {
      var parsed = parseInt(value);
      return typeof parsed === 'number' && isNaN(parsed) ? null : parsed;
    };
    var dimensions = [parse(screen.width), parse(screen.height)];
    dimensions.sort().reverse();
    return dimensions;
  }

  // A throw is recorded as {error}, keeping "missing" distinct from "false".
  function required(condition, message) {
    if (!condition) throw new Error(message);
  }

  function getErrorTrace() {
    try {
      null[0]();
    } catch (error) {
      if (error && error.stack != null) return String(error.stack);
    }
    throw new Error('errorTrace signal unexpected behaviour');
  }

  function getProcess() {
    var process = window.process;
    required(process !== undefined, 'window.process is undefined');
    required(typeof process === 'object', 'window.process is not an object');
    return { type: process.type, versions: { electron: process.versions && process.versions.electron } };
  }

  function areMimeTypesConsistent() {
    required(navigator.mimeTypes !== undefined, 'navigator.mimeTypes is undefined');
    var mimeTypes = navigator.mimeTypes;
    var isConsistent = Object.getPrototypeOf(mimeTypes) === MimeTypeArray.prototype;
    for (var i = 0; i < mimeTypes.length; i++) {
      isConsistent = isConsistent && Object.getPrototypeOf(mimeTypes[i]) === MimeType.prototype;
    }
    return isConsistent;
  }

  function getNotificationPermissions() {
    required(window.Notification !== undefined, 'window.Notification is undefined');
    required(navigator.permissions !== undefined, 'navigator.permissions is undefined');
    required(typeof navigator.permissions.query === 'function', 'permissions.query is not a function');
    return navigator.permissions.query({ name: 'notifications' }).then(function (status) {
      return window.Notification.permission === 'denied' && status.state === 'prompt';
    });
  }

  var DISTINCTIVE_PROPS = {
    Awesomium: { window: ['awesomium'] },
    Cef: { window: ['RunPerfTest'] },
    CefSharp: { window: ['CefSharp'] },
    CoachJS: { window: ['emit'] },
    FMiner: { window: ['fmget_targets'] },
    Geb: { window: ['geb'] },
    NightmareJS: { window: ['__nightmare', 'nightmare'] },
    Phantomas: { window: ['__phantomas'] },
    PhantomJS: { window: ['callPhantom', '_phantom'] },
    Rhino: { window: ['spawn'] },
    Selenium: {
      window: ['_Selenium_IDE_Recorder', '_selenium', 'calledSelenium', /^([a-z]){3}_.*_(Array|Promise|Symbol)$/],
      document: ['__selenium_evaluate', 'selenium-evaluate', '__selenium_unwrapped']
    },
    WebDriverIO: { window: ['wdioElectron'] },
    WebDriver: {
      window: ['webdriver', '__webdriverFunc', '__lastWatirAlert', '__lastWatirConfirm',
        '__lastWatirPrompt', '_WEBDRIVER_ELEM_CACHE', 'ChromeDriverw'],
      document: ['__webdriver_script_fn', '__driver_evaluate', '__webdriver_evaluate',
        '__fxdriver_evaluate', '__driver_unwrapped', '__webdriver_unwrapped',
        '__fxdriver_unwrapped', '__webdriver_script_func', '__webdriver_script_function',
        '$cdc_asdjflasutopfhvcZLmcf', '$cdc_asdjflasutopfhvcZLmcfl_', '$chrome_asyncScriptInfo',
        '__$webdriverAsyncExecutor']
    },
    HeadlessChrome: { window: ['domAutomation', 'domAutomationController'] }
  };

  function propsInclude(names, keys) {
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (typeof key === 'string') {
        if (names.indexOf(key) !== -1) return true;
      } else {
        for (var j = 0; j < names.length; j++) if (key.test(names[j])) return true;
      }
    }
    return false;
  }

  function getDistinctiveProps() {
    var windowProps = Object.getOwnPropertyNames(window);
    var documentProps = window.document !== undefined ? Object.getOwnPropertyNames(window.document) : [];
    var result = {};
    for (var bot in DISTINCTIVE_PROPS) {
      var props = DISTINCTIVE_PROPS[bot];
      var inWindow = props.window ? propsInclude(windowProps, props.window) : false;
      var inDocument = props.document && documentProps.length ? propsInclude(documentProps, props.document) : false;
      result[bot] = inWindow || inDocument;
    }
    return result;
  }

  // --- registry: source name -> [category, reader] ---

  var SOURCES = {
    platform: ['browser', getPlatform],
    languages: ['browser', getLanguages],
    timezone: ['browser', getTimezone],
    hardwareConcurrency: ['browser', function () { return navigator.hardwareConcurrency; }],
    vendor: ['browser', function () { return navigator.vendor; }],
    osCpu: ['browser', function () { return navigator.oscpu; }],
    deviceMemory: ['browser', function () { return navigator.deviceMemory; }],
    userAgent: ['browser', function () { return navigator.userAgent; }],
    appVersion: ['browser', function () {
      required(navigator.appVersion != undefined, 'navigator.appVersion is undefined');
      return navigator.appVersion;
    }],
    productSub: ['browser', function () {
      required(navigator.productSub !== undefined, 'navigator.productSub is undefined');
      return navigator.productSub;
    }],
    browserEngineKind: ['browser', getBrowserEngineKind],
    browserKind: ['browser', getBrowserKind],
    android: ['browser', isAndroid],
    canvas: ['rendering', getCanvas],
    webgl: ['rendering', getWebGl],
    math: ['math', getMath],
    screenResolution: ['screen', getScreenResolution],
    colorDepth: ['screen', function () { return screen.colorDepth; }],
    devicePixelRatio: ['screen', function () { return window.devicePixelRatio; }],
    windowSize: ['screen', function () {
      return {
        outerWidth: window.outerWidth, outerHeight: window.outerHeight,
        innerWidth: window.innerWidth, innerHeight: window.innerHeight
      };
    }],
    documentFocus: ['screen', function () {
      return document.hasFocus === undefined ? false : document.hasFocus();
    }],
    webdriver: ['bot', function () {
      required(navigator.webdriver != undefined, 'navigator.webdriver is undefined');
      return navigator.webdriver;
    }],
    evalLength: ['bot', function () { return eval.toString().length; }],
    functionBind: ['bot', function () {
      required(Function.prototype.bind !== undefined, 'Function.prototype.bind is undefined');
      return Function.prototype.bind.toString();
    }],
    windowExternal: ['bot', function () {
      required(window.external !== undefined, 'window.external is undefined');
      required(typeof window.external.toString === 'function', 'window.external.toString is not a function');
      return window.external.toString();
    }],
    errorTrace: ['bot', getErrorTrace],
    process: ['bot', getProcess],
    documentElementKeys: ['bot', function () {
      required(document.documentElement !== undefined, 'document.documentElement is undefined');
      required(typeof document.documentElement.getAttributeNames === 'function',
        'documentElement.getAttributeNames is not a function');
      return document.documentElement.getAttributeNames();
    }],
    pluginsLength: ['bot', function () {
      required(navigator.plugins !== undefined, 'navigator.plugins is undefined');
      required(navigator.plugins.length !== undefined, 'navigator.plugins.length is undefined');
      return navigator.plugins.length;
    }],
    mimeTypesConsistent: ['bot', areMimeTypesConsistent],
    notificationPermissions: ['bot', getNotificationPermissions],
    rtt: ['bot', function () {
      required(navigator.connection !== undefined, 'navigator.connection is undefined');
      required(navigator.connection.rtt !== undefined, 'navigator.connection.rtt is undefined');
      return navigator.connection.rtt;
    }],
    distinctiveProps: ['bot', getDistinctiveProps]
  };

  function collect() {
    var signals = {};
    var pending = [];
    for (var name in SOURCES) {
      var category = SOURCES[name][0];
      if (enabled.indexOf(category) === -1) continue;
      pending.push(runSource(name, SOURCES[name][1], signals));
    }
    return Promise.all(pending).then(function () { return signals; });
  }

  function runSource(name, reader, signals) {
    try {
      var value = reader();
      if (value && typeof value.then === 'function') {
        return value.then(
          function (resolved) { signals[name] = { value: resolved }; },
          function (error) { signals[name] = { error: String(error) }; }
        );
      }
      signals[name] = { value: value };
    } catch (error) {
      signals[name] = { error: String(error) };
    }
    return Promise.resolve();
  }

  function send(signals) {
    var body = JSON.stringify({ v: 1, signals: signals });
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(ingest, new Blob([body], { type: 'application/json' }))) {
        return;
      }
    } catch (error) { /* fall through to fetch */ }
    if (window.fetch) {
      fetch(ingest, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
        credentials: 'same-origin',
        keepalive: true
      }).catch(function () {});
    }
  }

  collect().then(send).catch(function () {});
})();
