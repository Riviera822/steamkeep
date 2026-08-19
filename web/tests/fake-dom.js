/**
 * Minimal in-memory DOM shim shared by the WP 4a.8 DOM-wiring regression
 * tests (`dialog-wiring.test.js`, `modal-stack.test.js`), extended by WP 4e.6
 * (`rail-panel-wiring.test.js`) with `replaceChildren` — the shim grows just
 * far enough for each new consumer's actual DOM-API surface, per its own
 * established pattern, rather than trying to anticipate every method up
 * front.
 *
 * Same spirit as `store-poll-loop.test.js`'s fake `document` (a plain object
 * exposing only the members the module under test actually touches, set
 * BEFORE importing it) — extended just far enough to run
 * `js/lib/modal-stack.js`, `js/components/sheet-dialog.js`,
 * `js/components/status-icon.js` and `js/router.js` headlessly: createElement/
 * createElementNS returning a real (if tiny) element graph with
 * classList/attributes/children/focus/dispatchEvent, because those modules
 * do more DOM-API surface than store.js's three-member touch. This is NOT a
 * general-purpose DOM implementation — no layout, no CSS, no querySelector
 * beyond what these specific modules need — it exists to prove the WIRING
 * (an element ends up with the right class/attribute, a callback fires),
 * not to be a jsdom replacement.
 */

class FakeClassList {
  constructor(el) {
    this._el = el;
  }
  add(...names) {
    for (const n of names) this._el._classes.add(n);
  }
  remove(...names) {
    for (const n of names) this._el._classes.delete(n);
  }
  toggle(name, force) {
    const has = this._el._classes.has(name);
    const want = force === undefined ? !has : !!force;
    if (want) this._el._classes.add(name);
    else this._el._classes.delete(name);
    return want;
  }
  contains(name) {
    return this._el._classes.has(name);
  }
}

class FakeElement {
  constructor(tag) {
    this.tagName = String(tag || "").toUpperCase();
    this._classes = new Set();
    this.classList = new FakeClassList(this);
    this.children = [];
    this.parentNode = null;
    this._attrs = new Map();
    this._listeners = new Map();
    this.dataset = {};
    this.style = {};
    this.tabIndex = 0;
  }
  get className() {
    return [...this._classes].join(" ");
  }
  set className(v) {
    this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  setAttribute(name, value) {
    this._attrs.set(name, String(value));
  }
  getAttribute(name) {
    return this._attrs.has(name) ? this._attrs.get(name) : null;
  }
  hasAttribute(name) {
    return this._attrs.has(name);
  }
  removeAttribute(name) {
    this._attrs.delete(name);
  }
  append(...nodes) {
    for (const n of nodes) this.appendChild(n);
  }
  appendChild(node) {
    this.children.push(node);
    node.parentNode = this;
    return node;
  }
  // WP 4e.6 (rail-panel-wiring.test.js): rail-content rendering clears and
  // rebuilds its container on every tick the same way notifications.js's
  // log list already does in production — added here rather than assuming
  // a DOM shim only needs what existed before this WP.
  replaceChildren(...nodes) {
    for (const c of this.children) c.parentNode = null;
    this.children = [];
    this.append(...nodes);
  }
  contains(node) {
    let cur = node;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentNode;
    }
    return false;
  }
  // WP 4h.3 (header-art.test.js): components/header-art.js calls
  // `wrap.remove()` on an image load failure, real DOM's `Element.remove()`
  // — added here per this file's own "grows just far enough" policy.
  remove() {
    if (!this.parentNode) return;
    const idx = this.parentNode.children.indexOf(this);
    if (idx !== -1) this.parentNode.children.splice(idx, 1);
    this.parentNode = null;
  }
  addEventListener(type, handler) {
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(handler);
  }
  removeEventListener(type, handler) {
    this._listeners.get(type)?.delete(handler);
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    for (const handler of this._listeners.get(event.type) || []) handler(event);
    return !event.defaultPrevented;
  }
  focus() {
    if (this._ownerDoc) this._ownerDoc.activeElement = this;
  }
  querySelector() {
    return null; // not needed by the modules this harness targets
  }
}

/** Creates a fresh, isolated `{document, window}` pair. Each test that needs
 * one calls this itself (rather than sharing a module-level singleton) so
 * tests cannot leak DOM state into each other — `resetModalStack()` still
 * needs calling between cases for `lib/modal-stack.js`'s own module-level
 * stack, since THAT state is shared regardless of which fake document a
 * test built. */
export function createFakeDom() {
  const bodyListeners = new Map();
  const docListeners = new Map();

  const body = new FakeElement("body");
  const appRoot = new FakeElement("div");
  appRoot.id = "app";
  body.appendChild(appRoot);

  const document = {
    body,
    activeElement: body,
    getElementById(id) {
      return id === "app" ? appRoot : null;
    },
    createElement(tag) {
      const el = new FakeElement(tag);
      el._ownerDoc = document;
      return el;
    },
    createElementNS(_ns, tag) {
      return document.createElement(tag);
    },
    addEventListener(type, handler) {
      if (!docListeners.has(type)) docListeners.set(type, new Set());
      docListeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      docListeners.get(type)?.delete(handler);
    },
    dispatchEvent(event) {
      event.target = event.target || document;
      for (const handler of docListeners.get(event.type) || []) handler(event);
      return !event.defaultPrevented;
    },
  };
  body._ownerDoc = document;
  appRoot._ownerDoc = document;

  let pathname = "/library";
  const windowListeners = new Map();
  const window = {
    location: {
      get pathname() {
        return pathname;
      },
    },
    history: {
      pushState(_state, _title, path) {
        pathname = path;
      },
    },
    addEventListener(type, handler) {
      if (!windowListeners.has(type)) windowListeners.set(type, new Set());
      windowListeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      windowListeners.get(type)?.delete(handler);
    },
  };

  return { document, window, appRoot, FakeElement };
}

/** A minimal `KeyboardEvent`-shaped object good enough for the `keydown`
 * handlers under test (`event.key`, `event.preventDefault()`,
 * `event.defaultPrevented`, `event.target`). */
export function fakeKeyEvent(key) {
  let prevented = false;
  return {
    type: "keydown",
    key,
    preventDefault() {
      prevented = true;
    },
    get defaultPrevented() {
      return prevented;
    },
  };
}

/** A minimal `MouseEvent`-shaped `click` object. */
export function fakeClickEvent(target) {
  return { type: "click", target };
}
