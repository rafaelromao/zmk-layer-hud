import Foundation

/// A keymap file the app is given -- `zmk-layer-hud keymap -o board.json` on the computer, sent over
/// by AirDrop, Files or "Open in" -- checked before it replaces the one the keyboard draws. The
/// same rules as the Android app's (apps/android/sink Keymaps.kt).
public enum KeymapFile {
    /// The newest message schema this app reads (docs/touch.md: the keymap message's `schema`).
    public static let schema = 1
    public static let maxBytes = 8 * 1024 * 1024
    /// Where the app keeps it, in the App Group's container, and the extension reads it.
    public static let name = "keymap.json"

    public enum Checked: Equatable {
        /// A keymap to keep; `types` is false when it says nothing of what its keys do (a board
        /// published for the landing page): drawn, and its keys do nothing.
        case ok(title: String, types: Bool)
        case refused(String)
    }

    public static func check(_ data: Data) -> Checked {
        if data.count > maxBytes { return .refused("this file is too large to be a keymap") }
        guard let any = try? JSONSerialization.jsonObject(with: data), let m = any as? [String: Any] else {
            return .refused("this is not a keymap")
        }
        guard m["kind"] as? String == "keymap" else {
            return .refused("this is not a keymap: write one with `zmk-layer-hud keymap -o board.json`")
        }
        let schema = (m["schema"] as? NSNumber)?.intValue ?? 0
        if schema > Self.schema {
            return .refused("this keymap is from a newer zmk-layer-hud (schema \(schema)): update this app")
        }
        guard let layout = m["layout"] as? [String: Any], layout["keys"] is [Any], m["layers"] is [String: Any] else {
            return .refused("this keymap has no keys to draw")
        }
        let actions = m["actions"] as? [String: Any]
        let title = (m["title"] as? String).flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 } ?? "keymap"
        return .ok(title: title, types: actions != nil && actions?["error"] == nil)
    }
}
