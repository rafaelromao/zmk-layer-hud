import Foundation

/// The text field as an iOS keyboard reaches it: UITextDocumentProxy's part of it that TextSink
/// uses (the keyboard extension's proxy conforms as it is).
public protocol TextProxy: AnyObject {
    var documentContextBeforeInput: String? { get }
    var documentContextAfterInput: String? { get }
    func insertText(_ text: String)
    func deleteBackward()
    func adjustTextPosition(byCharacterOffset offset: Int)
}

/// What the board types, done to the field as far as an iOS keyboard can: text inserted, Enter a
/// new line (the field turns it into Go, Search or Send itself), Tab a tab, Backspace and Delete,
/// the cursor left, right, and to either end of the line. There is no Escape, no up or down, no
/// page keys and no ⌘ shortcuts for a keyboard extension; those come back undone.
public enum TextSink {
    /// Does each op to the field; returns the ones it could not do.
    @discardableResult
    public static func type(_ ops: [Op], into field: TextProxy) -> [Op] {
        ops.filter { !type($0, into: field) }
    }

    static func type(_ op: Op, into field: TextProxy) -> Bool {
        switch op {
        case let .text(text, _, _):
            field.insertText(text)
            return true
        case let .key(name, _, _):
            return key(name, into: field)
        case .chord:
            return false
        }
    }

    // How far the cursor moves over one character: the field counts UTF-16 units, and an emoji or
    // an accented letter written as two is still one character to step over.
    static func width(of c: Character?) -> Int { c.map { String($0).utf16.count } ?? 1 }

    static func key(_ name: String, into field: TextProxy) -> Bool {
        switch name {
        case "enter":
            field.insertText("\n")
        case "tab":
            field.insertText("\t")
        case "backspace":
            field.deleteBackward()
        case "delete":
            guard let after = field.documentContextAfterInput, let c = after.first else { return true }
            field.adjustTextPosition(byCharacterOffset: width(of: c))
            field.deleteBackward()
        case "left":
            field.adjustTextPosition(byCharacterOffset: -width(of: field.documentContextBeforeInput?.last))
        case "right":
            field.adjustTextPosition(byCharacterOffset: width(of: field.documentContextAfterInput?.first))
        case "home":
            // To the start of the line, as far as the field lets the keyboard see.
            guard let before = field.documentContextBeforeInput else { return false }
            let line = before.split(separator: "\n", omittingEmptySubsequences: false).last ?? ""
            if !line.isEmpty { field.adjustTextPosition(byCharacterOffset: -line.utf16.count) }
        case "end":
            guard let after = field.documentContextAfterInput else { return false }
            let line = after.split(separator: "\n", omittingEmptySubsequences: false).first ?? ""
            if !line.isEmpty { field.adjustTextPosition(byCharacterOffset: line.utf16.count) }
        default:
            return false
        }
        return true
    }
}

/// Which key a finger is on: the key under it, its rotation undone, or in the gap between keys the
/// nearest one within half a gap and a little (hud/touch.js keyAt).
public enum HitTest {
    /// The chip under a finger, which wins over the keys it sits between.
    public static func chip(at x: Double, _ y: Double, in chips: [Chip]) -> Int? {
        chips.first { abs(x - $0.x) <= $0.size / 2 && abs(y - $0.y) <= $0.size / 2 }?.combo
    }

    public static func key(at x: Double, _ y: Double, in g: Geometry) -> Int? {
        var best: Int?
        var bestD = Double.infinity
        for (idx, k) in g.rects.enumerated() {
            let a = -k.r * Double.pi / 180
            let dx = x - k.centerX, dy = y - k.centerY
            let lx = dx * cos(a) - dy * sin(a), ly = dx * sin(a) + dy * cos(a)
            let d = hypot(max(0, abs(lx) - k.w / 2), max(0, abs(ly) - k.h / 2))
            if d < bestD { bestD = d; best = idx }
        }
        return bestD <= g.gap / 2 + 2 ? best : nil
    }
}
