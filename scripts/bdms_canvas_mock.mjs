/** Deterministic canvas/WebGL mocks aligned with browser_fingerprint.json. */
export function installCanvasMocks(window, fp = null) {
  const { document } = window;
  const canvasData = fp?.canvasData || "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAASwAAACWCAYAAABkW7XSAAALpUlEQVR4AeyaO4g0RReG5/9TL2iogaIggiaChoIKJoKKiIEmCo";
  const webgl = fp?.webgl || {
    vendor: "Google Inc. (NVIDIA)",
    renderer: "ANGLE (NVIDIA, NVIDIA GeForce RTX 4080 SUPER (0x00002702) Direct3D11 vs_5_0 ps_5_0, D3D11)",
    version: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
    shading: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
  };

  function mockCtx2d() {
    return {
      fillRect() {},
      fillText() {},
      measureText: (t) => ({ width: String(t).length * 7.2 }),
      getImageData: () => ({ data: new Uint8ClampedArray(4) }),
      canvas: { toDataURL: () => canvasData },
      font: "14px Arial",
      textBaseline: "top",
    };
  }

  function mockWebGL() {
    return {
      getExtension: (name) => {
        if (name === "WEBGL_debug_renderer_info") {
          return { UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 };
        }
        return {};
      },
      getParameter: (p) => {
        const map = {
          0x9245: webgl.vendor,
          0x9246: webgl.renderer,
          0x1f00: "WebKit",
          0x1f01: "WebKit WebGL",
          0x1f02: webgl.version,
          0x8b8c: webgl.shading,
        };
        return map[p] ?? "0";
      },
      createBuffer: () => ({}),
      bindBuffer() {},
      bufferData() {},
      createProgram: () => ({}),
      createShader: () => ({}),
      shaderSource() {},
      compileShader() {},
      attachShader() {},
      linkProgram() {},
      useProgram() {},
      getAttribLocation: () => 0,
      enableVertexAttribArray() {},
      vertexAttribPointer() {},
      drawArrays() {},
      canvas: { toDataURL: () => canvasData },
    };
  }

  const origCreate = document.createElement.bind(document);
  document.createElement = function (tag, ...rest) {
    const el = origCreate(tag, ...rest);
    const t = String(tag || "").toLowerCase();
    if (t === "canvas") {
      el.getContext = (type) => {
        if (type === "2d") return mockCtx2d();
        if (type === "webgl" || type === "experimental-webgl") return mockWebGL();
        return null;
      };
      el.toDataURL = () => canvasData;
      el.width = 300;
      el.height = 150;
    }
    return el;
  };
}

export function installXhrCapture(window, testUrl) {
  const NativeXHR = window.XMLHttpRequest;
  const captures = [];

  class CapXHR extends NativeXHR {
    open(method, url, ...rest) {
      this.__cap = { method, urlIn: String(url) };
      return super.open(method, url, ...rest);
    }
    send(body) {
      try {
        super.send(body);
      } catch {
        // ignore CORS/network — bdms may have signed URL in open()
      }
      const out = this.responseURL || this.__cap?.urlIn || "";
      captures.push({
        ...this.__cap,
        out,
        hasBogus: out.includes("a_bogus="),
        a_bogus: window.a_bogus,
      });
    }
  }
  window.__xhrCaptures = captures;
  return captures;
}
