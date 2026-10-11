import Foundation
import Testing
@testable import ZmkHudCore

/// The repository, from this file: apps/ios/Core/Tests/ZmkHudCoreTests/CoreTests.swift.
let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

func text(_ path: String) throws -> String { try String(contentsOf: repo.appendingPathComponent(path), encoding: .utf8) }

func kbd(_ keymap: String? = nil) throws -> JSKbd {
    try JSKbd(keymap: keymap ?? text("apps/fixtures/kbd-keymap.json")) { try text("hud/\($0)") }
}

struct Golden: Decodable {
    struct Step: Decodable {
        let what: String
        let key: Int?
        let t: Double
        let answer: Answer
        let board: Board
        enum CodingKeys: String, CodingKey { case what = "do", key, t, answer, board }
    }
    struct Shape: Decodable {
        let width: Double
        let maxHeight: Double
        let split: Bool
        let answer: Geometry
    }
    let geometry: Shape
    let steps: [Step]
}

@Suite struct Golden_answers {
    /// apps/fixtures/kbd-golden.json is what hud/kbd.js answers in node; here it runs in
    /// JavaScriptCore and must answer the same, step by step.
    @Test func javaScriptCoreAnswersAsNodeDoes() throws {
        let golden = try JSONDecoder().decode(Golden.self, from: Data(text("apps/fixtures/kbd-golden.json").utf8))
        let k = try kbd()
        for (n, step) in golden.steps.enumerated() {
            let got: Answer
            switch step.what {
            case "down": got = k.down(step.key!, at: step.t)
            case "up": got = k.up(step.key!, at: step.t)
            case "tick": got = k.tick(at: step.t)
            default: got = k.releaseAll(at: step.t)
            }
            #expect(got == step.answer, "step \(n): \(step.what) \(step.key.map(String.init) ?? "") at \(step.t)")
            #expect(k.board() == step.board, "step \(n): the board")
        }
        let g = golden.geometry
        #expect(k.geometry(width: g.width, maxHeight: g.maxHeight, split: g.split) == g.answer)
        #expect(k.title == "Test")
    }

    @Test func aKeymapThatCannotTypeIsItsOwnSentence() throws {
        #expect(throws: JSKbd.Failure.keymap("no keymap captured")) {
            try kbd(#"{"kind":"keymap","actions":{"error":"no keymap captured"},"layout":{"keys":[]}}"#)
        }
        #expect(throws: JSKbd.Failure.self) { try JSKbd(keymap: "{}") { _ in "this is not javascript (" } }
    }
}

final class Field: TextProxy {
    var before: String
    var after: String
    var done: [String] = []

    init(_ before: String = "", _ after: String = "") { self.before = before; self.after = after }

    var documentContextBeforeInput: String? { before }
    var documentContextAfterInput: String? { after }
    func insertText(_ text: String) { done.append("insert \(text)"); before += text }
    func deleteBackward() { done.append("delete"); if !before.isEmpty { before.removeLast() } }
    func adjustTextPosition(byCharacterOffset offset: Int) {
        done.append("move \(offset)")
        if offset < 0 {
            let s = before.utf16.count + offset
            after = String(before.utf16.dropFirst(s))! + after
            before = String(before.utf16.prefix(s))!
        } else {
            before += String(after.utf16.prefix(offset))!
            after = String(after.utf16.dropFirst(offset))!
        }
    }
}

@Suite struct Text_sink {
    @Test func textKeysAndWhatAKeyboardExtensionCannotDo() {
        let f = Field("ab", "cd")
        let undone = TextSink.type([.text("é", usage: 8, mods: 0), .key("backspace", usage: 42, mods: 0),
                                    .key("enter", usage: 40, mods: 0), .key("tab", usage: 43, mods: 0),
                                    .key("escape", usage: 41, mods: 0), .key("up", usage: 82, mods: 0),
                                    .chord(chars: "c", name: nil, usage: 6, mods: 1)], into: f)
        #expect(f.done == ["insert é", "delete", "insert \n", "insert \t"])
        #expect(undone == [.key("escape", usage: 41, mods: 0), .key("up", usage: 82, mods: 0),
                           .chord(chars: "c", name: nil, usage: 6, mods: 1)])
        #expect(f.before == "ab\n\t" && f.after == "cd")
    }

    @Test func theCursorStepsOverWholeCharacters() {
        let f = Field("a😀", "é!")
        TextSink.type([.key("left", usage: 80, mods: 0)], into: f)
        #expect(f.done == ["move -2"] && f.before == "a" && f.after == "😀é!")
        TextSink.type([.key("right", usage: 79, mods: 0), .key("delete", usage: 76, mods: 0)], into: f)
        #expect(f.before == "a😀" && f.after == "!")
    }

    @Test func homeAndEndAreTheLinesEnds() {
        let f = Field("one\ntwo", "three\nfour")
        TextSink.type([.key("home", usage: 74, mods: 0)], into: f)
        #expect(f.before == "one\n" && f.after == "twothree\nfour")
        TextSink.type([.key("end", usage: 77, mods: 0)], into: f)
        #expect(f.before == "one\ntwothree" && f.after == "\nfour")
    }
}

@Suite struct Keymap_file {
    @Test func aKeymapIsCheckedBeforeItIsKept() throws {
        let keys = #""layout":{"keys":[]},"layers":{}"#
        func check(_ s: String) -> KeymapFile.Checked { KeymapFile.check(Data(s.utf8)) }
        #expect(check(#"{"kind":"keymap","schema":1,"title":"Diamond",\#(keys),"actions":{"v":1}}"#) == .ok(title: "Diamond", types: true))
        #expect(check(#"{"kind":"keymap",\#(keys)}"#) == .ok(title: "keymap", types: false))
        #expect(check(#"{"kind":"keymap",\#(keys),"actions":{"error":"x"}}"#) == .ok(title: "keymap", types: false))
        for (s, said) in [(#"{"kind":"keymap","schema":2,\#(keys)}"#, "newer"), (#"{"kind":"layers"}"#, "not a keymap"),
                          ("nope", "not a keymap"), (#"{"kind":"keymap"}"#, "no keys")] {
            guard case let .refused(why) = check(s) else { Issue.record("\(s) was kept"); continue }
            #expect(why.contains(said), "\(why)")
        }
        // The test keymap, and the boards the site publishes, are keymaps.
        #expect(KeymapFile.check(Data(try text("apps/fixtures/kbd-keymap.json").utf8)) == .ok(title: "Test", types: true))
    }
}

@Suite struct Hit_test {
    let g = Geometry(scale: 1, gap: 6, height: 100, rects: [
        KeyRect(x: 3, y: 3, w: 94, h: 94, r: 0), KeyRect(x: 103, y: 3, w: 94, h: 94, r: 0),
        KeyRect(x: 303, y: 3, w: 94, h: 94, r: 45),
    ])

    @Test func theKeyUnderAFingerOrTheNearestAcrossAGap() {
        #expect(HitTest.key(at: 50, 50, in: g) == 0)
        #expect(HitTest.key(at: 101, 50, in: g) == 1)          // in the gap, nearer the second
        #expect(HitTest.key(at: 250, 50, in: g) == nil)        // far from any key
        // A key turned 45°: its corner is outside the box it was drawn in.
        #expect(HitTest.key(at: 350, 50 - 60, in: g) == 2)
        #expect(HitTest.key(at: 304, 4, in: g) == nil)
    }
}
