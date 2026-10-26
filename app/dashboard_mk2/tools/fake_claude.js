// Local test double for window.claude (db + user), in memory. Only for the local preview at /__test.
(function () {
  const store = new Map();
  const listeners = new Set();
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const meta = { fromCache: false, hasPendingWrites: false };
  const snapDoc = (path) => {
    const d = store.get(path);
    return { id: path.split("/").pop(), exists: !!d, data: () => (d ? clone(d) : undefined), metadata: meta };
  };
  const depth = (p) => p.split("/").length;
  const snapCol = (l) => {
    let docs = [...store.keys()].filter((p) => p.startsWith(l.path + "/") && depth(p) === depth(l.path) + 1).map(snapDoc);
    if (l.order) {
      const s = l.dir === "desc" ? -1 : 1;
      docs.sort((a, b) => { const x = a.data()[l.order], y = b.data()[l.order]; return (x < y ? -1 : x > y ? 1 : 0) * s; });
    }
    if (l.lim) docs = docs.slice(0, l.lim);
    return { docs, size: docs.length, empty: !docs.length, docChanges: () => [], metadata: meta };
  };
  const fire = () => listeners.forEach((l) => setTimeout(() => l.cb(l.kind === "doc" ? snapDoc(l.path) : snapCol(l)), 0));
  const fail = () => { if (window.__failWrites) { const e = { code: window.__failWrites, message: "test" }; throw e; } };
  const docRef = (path) => ({
    id: path.split("/").pop(), path,
    get: async () => snapDoc(path),
    set: async (d) => { fail(); store.set(path, clone(d)); window.__writes.push(["set", path, clone(d)]); fire(); },
    update: async (d) => { fail(); if (!store.has(path)) throw { code: "invalid_argument", message: "missing" }; store.set(path, Object.assign(store.get(path), clone(d))); window.__writes.push(["update", path, clone(d)]); fire(); },
    delete: async () => { fail(); store.delete(path); window.__writes.push(["delete", path]); fire(); },
    onSnapshot: (cb) => { const l = { kind: "doc", path, cb }; listeners.add(l); setTimeout(() => cb(snapDoc(path)), 0); return () => listeners.delete(l); },
    collection: (p) => colRef(path + "/" + p),
  });
  const query = (path, order, dir, lim) => ({
    where() { return this; },
    orderBy: (f, d) => query(path, f, d || "asc", lim),
    limit: (n) => query(path, order, dir, n),
    get: async () => snapCol({ path, order, dir, lim }),
    onSnapshot: (cb) => { const l = { kind: "col", path, order, dir, lim, cb }; listeners.add(l); setTimeout(() => cb(snapCol(l)), 0); return () => listeners.delete(l); },
  });
  const colRef = (path) => Object.assign(query(path), {
    path,
    doc: (id) => docRef(path + "/" + (id || Math.random().toString(36).slice(2))),
    add: async (d) => { const r = docRef(path + "/" + Math.random().toString(36).slice(2)); await r.set(d); return r; },
  });
  window.__writes = [];
  window.__store = store;
  const db = { doc: docRef, collection: colRef };
  const user = {
    id: async () => "u_test",
    me: async () => ({ id: "u_test", name: "Tester" }),
    can: async () => (window.__canWrite === undefined ? true : window.__canWrite),
    canEdit: async () => true,
    isOwner: async () => true,
    profiles: async (ids) => Object.fromEntries([].concat(ids).map((i) => [i, { id: i, name: i === "u_test" ? "Tester" : "" }])),
  };
  window.claude = { use: async (n) => (n === "db" ? db : n === "user" ? user : null) };
})();
