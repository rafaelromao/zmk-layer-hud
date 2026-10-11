import UIKit
import ZmkHudCore

/// The HUD as the iPad's keyboard: the virtual firmware in JavaScriptCore (ZmkHudCore.JSKbd), the
/// board drawn natively (BoardView), and what it types done to the field (TextSink).
///
/// It asks for no Full Access: it reads its keymap from the App Group the app writes it to, else the
/// one built into it, and sends nothing anywhere.
final class KeyboardViewController: UIInputViewController {
    private let boardView = BoardView()
    private var kbd: JSKbd?
    private var height: NSLayoutConstraint?
    private var timer: Timer?
    private var room: (width: Double, maxHeight: Double) = (0, 0)
    private let epoch = CACurrentMediaTime()
    private lazy var field = ProxyField(self)

    /// The keyboard's share of the screen at most, as on the desktop dock and on Android.
    private static let maxShare: CGFloat = 0.45

    override func viewDidLoad() {
        super.viewDidLoad()
        boardView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(boardView)
        NSLayoutConstraint.activate([
            boardView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            boardView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            boardView.topAnchor.constraint(equalTo: view.topAnchor),
            boardView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        let h = view.heightAnchor.constraint(equalToConstant: 260)
        h.priority = UILayoutPriority(999)
        h.isActive = true
        height = h
        boardView.globe.addTarget(self, action: #selector(handleInputModeList(from:with:)), for: .allTouchEvents)
        boardView.onDown = { [weak self] idx in self?.down(idx) }
        boardView.onUp = { [weak self] idx in self?.up(idx) }
        boardView.onChord = { [weak self] combo, down in
            guard let self, let kbd = self.kbd else { return }
            self.handle(kbd.chord(combo, down: down, at: self.now()))
        }
        load()
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        boardView.globe.isHidden = !needsInputModeSwitchKey
    }

    override func viewWillLayoutSubviews() {
        super.viewWillLayoutSubviews()
        relayout()
    }

    override func viewWillDisappear(_ animated: Bool) {
        if let kbd { handle(kbd.releaseAll(at: now())) }
        super.viewWillDisappear(animated)
    }

    // ---------- the keymap ----------

    private func load() {
        do {
            guard let keymap = Self.keymap() else {
                boardView.problem = "no keymap yet: import one in the ZMK Layer HUD app"
                return
            }
            kbd = try JSKbd(keymap: keymap) { name in
                guard let url = Bundle.main.url(forResource: name, withExtension: nil) else { throw JSKbd.Failure.script(name) }
                return try String(contentsOf: url, encoding: .utf8)
            }
            boardView.problem = nil
        } catch JSKbd.Failure.keymap(let why) {
            boardView.problem = why
        } catch {
            boardView.problem = "the keyboard could not start: \(error)"
        }
    }

    /// The app's keymap, from the App Group they share; else the one this keyboard was built with.
    static func keymap() -> String? {
        if let group = Bundle.main.object(forInfoDictionaryKey: "ZMKHUDAppGroup") as? String,
           let dir = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group),
           let text = try? String(contentsOf: dir.appendingPathComponent(KeymapFile.name), encoding: .utf8) {
            return text
        }
        return Bundle.main.url(forResource: "keymap", withExtension: "json").flatMap { try? String(contentsOf: $0, encoding: .utf8) }
    }

    // ---------- the board ----------

    private func relayout() {
        guard let kbd else { return }
        let screen = view.window?.windowScene?.screen.bounds.height ?? UIScreen.main.bounds.height
        let room = Double(screen * Self.maxShare - BoardView.bannerHeight)
        guard let g = kbd.geometry(width: Double(view.bounds.width), maxHeight: room) else { return }
        self.room = (Double(view.bounds.width), room)
        if boardView.geometry != g {
            boardView.geometry = g
            boardView.board = kbd.board()
            boardView.chips = kbd.chips(width: self.room.width, maxHeight: room)
        }
        height?.constant = CGFloat(g.height) + BoardView.bannerHeight + 4
    }

    // ---------- fingers ----------

    private func now() -> Double { (CACurrentMediaTime() - epoch) * 1000 }

    private func down(_ idx: Int) {
        guard let kbd else { return }
        handle(kbd.down(idx, at: now()))
    }

    private func up(_ idx: Int) {
        guard let kbd else { return }
        handle(kbd.up(idx, at: now()))
    }

    private func handle(_ a: Answer) {
        TextSink.type(a.ops, into: field)
        if !a.refused.isEmpty { boardView.flash(a.refused) }
        if a.changed, let kbd {
            boardView.board = kbd.board()
            boardView.chips = kbd.chips(width: room.width, maxHeight: room.maxHeight)
        }
        schedule(a.next)
    }

    /// The engine's next moment (a hold decided, a key repeated, a dead key let out): one timer.
    private func schedule(_ next: Double?) {
        timer?.invalidate()
        timer = nil
        guard let next else { return }
        let delay = max(0, (next - now()) / 1000)
        timer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            guard let self, let kbd = self.kbd else { return }
            self.handle(kbd.tick(at: self.now()))
        }
    }
}

/// The field as TextSink reaches it: the controller's textDocumentProxy, whichever field is open.
final class ProxyField: TextProxy {
    private weak var controller: UIInputViewController?

    init(_ controller: UIInputViewController) { self.controller = controller }

    private var proxy: UITextDocumentProxy? { controller?.textDocumentProxy }

    var documentContextBeforeInput: String? { proxy?.documentContextBeforeInput }
    var documentContextAfterInput: String? { proxy?.documentContextAfterInput }
    func insertText(_ text: String) { proxy?.insertText(text) }
    func deleteBackward() { proxy?.deleteBackward() }
    func adjustTextPosition(byCharacterOffset offset: Int) { proxy?.adjustTextPosition(byCharacterOffset: offset) }
}
