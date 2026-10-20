import Foundation

/// What the board types, as hud/hidtext.js textEvents says it: text, a named key, or a ⌘/Ctrl
/// shortcut, each with the HID usage and the modifier byte it came from (docs/touch.md).
public enum Op: Equatable, Sendable, Decodable {
    case text(String, usage: Int, mods: Int)
    case key(String, usage: Int, mods: Int)
    case chord(chars: String, name: String?, usage: Int, mods: Int)

    private enum Field: String, CodingKey { case kind, text, key, chars, name, usage, mods }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Field.self)
        let usage = try c.decodeIfPresent(Int.self, forKey: .usage) ?? 0
        let mods = try c.decodeIfPresent(Int.self, forKey: .mods) ?? 0
        switch try c.decode(String.self, forKey: .kind) {
        case "text": self = .text(try c.decode(String.self, forKey: .text), usage: usage, mods: mods)
        case "key": self = .key(try c.decode(String.self, forKey: .key), usage: usage, mods: mods)
        case "chord":
            self = .chord(chars: try c.decodeIfPresent(String.self, forKey: .chars) ?? "",
                          name: try c.decodeIfPresent(String.self, forKey: .name), usage: usage, mods: mods)
        case let other:
            throw DecodingError.dataCorruptedError(forKey: .kind, in: c, debugDescription: "an op of kind \(other)")
        }
    }
}

/// What a finger, a tick or a release came to (hud/kbd.js): text ops for the field, the keys that
/// cannot type here (to flash), whether the board now looks different, and when to tick next (ms,
/// on the clock the calls were made with).
public struct Answer: Equatable, Sendable, Decodable {
    public let ops: [Op]
    public let refused: [Int]
    public let changed: Bool
    public let next: Double?
}

/// One key as the board draws it (hud/view.js keyFace).
public struct KeyFace: Equatable, Sendable, Decodable {
    public let tap: String
    public let hold: String
    public let shifted: String
    public let glyph: String?
    public let cls: [String]

    public var pressed: Bool { cls.contains("pressed") }
    public var latched: Bool { cls.contains("latched") }
    public var locked: Bool { cls.contains("locked") }
    public var blank: Bool { cls.contains("blank") }
    /// Shown through from a layer below, or left to another layer: drawn faint.
    public var faint: Bool { cls.contains("trans") || cls.contains("ghost") }
}

public struct Banner: Equatable, Sendable, Decodable {
    public let name: String
    public let cls: String
    public let sub: String
}

/// The banner and every key, on the keyboard's layers (hud/view.js board).
public struct Board: Equatable, Sendable, Decodable {
    public let banner: Banner
    public let keys: [KeyFace]
}

/// A key's box before its rotation (degrees, about its centre), in points.
public struct KeyRect: Equatable, Sendable, Decodable {
    public let x: Double
    public let y: Double
    public let w: Double
    public let h: Double
    public let r: Double

    public var centerX: Double { x + w / 2 }
    public var centerY: Double { y + h / 2 }
}

/// A combo as a chip to tap, between its keys (hud/view.js chips): the combo's index (JSKbd.chord),
/// the keys it presses, its centre and size in points, its legend.
public struct Chip: Equatable, Sendable, Decodable {
    public let combo: Int
    public let keys: [Int]
    public let x: Double
    public let y: Double
    public let size: Double
    public let tap: String
    public let cls: [String]

    public var pressed: Bool { cls.contains("pressed") }
}

/// Where the keys go (hud/view.js geometry): each key's box, the gap between keys, the board's height.
public struct Geometry: Equatable, Sendable, Decodable {
    public let scale: Double
    public let gap: Double
    public let height: Double
    public let rects: [KeyRect]
}
