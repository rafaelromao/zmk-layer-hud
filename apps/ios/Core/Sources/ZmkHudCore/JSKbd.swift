import Foundation
import JavaScriptCore

/// The HUD's touch keyboard (hud/kbd.js) in JavaScriptCore: the same virtual firmware, text
/// translation and board model the page runs, so the iPad types what the desktop HUD types.
///
/// A keyboard extension may not count on a WebView, and has little memory; JavaScriptCore runs the
/// scripts with neither a DOM nor a renderer. What the scripts answer comes back as JSON, read into
/// the types of Model.swift. Not thread-safe: one keyboard, one thread (the main one).
public final class JSKbd {
    /// The HUD's scripts it runs, in this order: each needs the ones before it.
    public static let scripts = ["hidtables.js", "hidtext.js", "engine.js", "view.js", "kbd.js"]

    public enum Failure: Error, Equatable {
        /// A script did not load: a build that copied the wrong files.
        case script(String)
        /// The keymap cannot type: not one, or without what its keys do (the message's own words).
        case keymap(String)
    }

    private final class Thrown { var message: String? }

    private let context: JSContext
    private let thrown = Thrown()
    private let call: JSValue

    /// `keymap` is the keymap message as `zmk-layer-hud keymap -o` writes it; `source(name)` gives
    /// each of `scripts`' text.
    public init(keymap: String, source: (String) throws -> String) throws {
        guard let context = JSContext() else { throw Failure.script("no JavaScriptCore context") }
        self.context = context
        let thrown = self.thrown
        context.exceptionHandler = { _, value in thrown.message = value?.toString() ?? "an exception" }
        for name in Self.scripts {
            context.evaluateScript(try source(name), withSourceURL: URL(string: "zmkhud:///\(name)"))
            if let m = thrown.message { throw Failure.script("\(name): \(m)") }
        }
        context.setObject(keymap, forKeyedSubscript: "zmkhudKeymap" as NSString)
        context.evaluateScript("""
            var zmkhudKbd = ZmkKbd.create(zmkhudKeymap, {shortcuts: false, repeat: "synthetic"});
            delete globalThis.zmkhudKeymap;
            function zmkhudCall(name, args) { return JSON.stringify(zmkhudKbd[name].apply(null, args)); }
            """)
        if let m = thrown.message {
            throw Failure.keymap(m.replacingOccurrences(of: "Error: ", with: ""))
        }
        guard let call = context.objectForKeyedSubscript("zmkhudCall") else { throw Failure.script("kbd.js") }
        self.call = call
    }

    private func invoke<T: Decodable>(_ name: String, _ args: [Any]) -> T? {
        thrown.message = nil
        guard let json = call.call(withArguments: [name, args])?.toString(), thrown.message == nil,
              let data = json.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    private static let nothing = Answer(ops: [], refused: [], changed: false, next: nil)

    /// A finger on the drawer key `idx`, at `t` milliseconds on any clock that only goes up.
    public func down(_ idx: Int, at t: Double) -> Answer { invoke("down", [idx, t]) ?? Self.nothing }

    public func up(_ idx: Int, at t: Double) -> Answer { invoke("up", [idx, t]) ?? Self.nothing }

    /// When the last answer's `next` comes round.
    public func tick(at t: Double) -> Answer { invoke("tick", [t]) ?? Self.nothing }

    /// The keyboard went away: everything up.
    public func releaseAll(at t: Double) -> Answer { invoke("releaseAll", [t]) ?? Self.nothing }

    public func board() -> Board? { invoke("board", []) }

    /// Where the keys go, docked in `width` points and at most `maxHeight` tall, a split board's
    /// halves at the sides.
    public func geometry(width: Double, maxHeight: Double, split: Bool = true) -> Geometry? {
        invoke("geometry", [width, maxHeight, split])
    }

    public var title: String { invoke("title", []) ?? "" }
}
