"""The keymap message, read: what the Python side needs to know of a keyboard the page draws (host/
keymap.py build_message writes it). The page reads it the same way in hud/view.js -- zl,
drawnStack, idxAt, posOf, resolve -- and this is that, in Python, for the tools that read it: the
test oracle (host/ways.py), the demo compiler (host/play.py), the session's export (host/export.py),
the session itself (host/session.py) and the touch keyboard's legend fallback (host/actions.py).

Standard library only, no I/O. How a legend is typed is not here: the oracle (ways.typed) and the
touch keyboard (actions.Legends) each state that for themselves, so one checks the other.

    msg = KeymapMessage(data)
    msg.base                   the base drawer layer
    msg.drawer_of(zid)         a ZMK layer's drawer layer, or None
    msg.ids_of(layer)          the ZMK layers a drawer layer stands for, lowest first
    msg.idx_at(pos)            the drawer key at a ZMK position, or None
    msg.pos_of(idx)            the ZMK position of a drawer key, or None
    msg.stack(ids)             the drawer layers live ZMK layers draw, top first, base last
    msg.resolve(idx, stack)    (layer, key): the first layer down where the key is not transparent
    msg.combos_on(layer)       the combos a drawer layer declares
    msg.activators(layer)      the keys that bring a drawer layer up
"""


class KeymapMessage:
    def __init__(self, data):
        self.data = data
        self.layers = data.get("layers") or {}
        self.order = list(data.get("layer_order") or self.layers)
        self.zmk_layers = data.get("zmk_layers") or {}
        self.positions = {int(p): int(i) for p, i in (data.get("positions") or {}).items()}
        self._pos = {i: p for p, i in self.positions.items()}
        z0 = self.zmk_layers.get("0") or {}
        # As build_message picks it: the message's word, else what ZMK layer 0 draws, else the first.
        self.base = data.get("base") or z0.get("drawer") or (self.order[0] if self.order else None)
        self._ids = {}
        for zid, z in self.zmk_layers.items():
            if z and z.get("drawer"):
                self._ids.setdefault(z["drawer"], []).append(int(zid))
        for ids in self._ids.values():
            ids.sort()

    def drawer_of(self, zid):
        """The drawer layer a ZMK layer is drawn as; one the table leaves out is the drawer's layer
        of that number (view.js zl); None for one drawn by none."""
        z = self.zmk_layers.get(str(zid))
        if z is not None:
            return z.get("drawer") or None
        zid = int(zid)
        return self.order[zid] if 0 <= zid < len(self.order) else None

    def ids_of(self, layer):
        return list(self._ids.get(layer, []))

    def idx_at(self, pos):
        """A position the keymap does not place is no key; a message with no map at all (a
        hand-written demo) means position = index (view.js idxAt)."""
        if not self.positions:
            return int(pos)
        return self.positions.get(int(pos))

    def pos_of(self, idx):
        if not self.positions:
            return int(idx)
        return self._pos.get(int(idx))

    def stack(self, ids):
        """Live ZMK layers, top first, as the drawer layers that draw them: undrawn ones skipped,
        two drawn alike once, the base always last (view.js drawnStack)."""
        out = []
        for zid in ids:
            d = self.drawer_of(zid)
            if d and d not in out:
                out.append(d)
        if self.base not in out:
            out.append(self.base)
        return out

    def resolve(self, idx, stack):
        """(layer, key) for a drawer key on a stack, or (None, None): the first layer down where it
        is not transparent (view.js resolve). A held key is its layer's own."""
        for name in stack:
            keys = self.layers.get(name) or []
            if 0 <= idx < len(keys) and keys[idx].get("type") != "trans":
                return name, keys[idx]
        return None, None

    def combos_on(self, layer):
        """The combos `layer` declares: with it the highest layer up, these and no others fire."""
        return [c for c in self.data.get("combos") or [] if layer in c["layers"]]

    def activators(self, layer):
        """The keys that bring `layer` up (host/keymap.py activators(): {layer, idx, kind})."""
        return sorted({a["idx"] for a in self.data.get("activators") or [] if a.get("layer") == layer})
