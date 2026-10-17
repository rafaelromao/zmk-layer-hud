import UIKit
import ZmkHudCore

/// The board, drawn natively: a banner, and every key a layer with its legends -- the tap one in the
/// middle, the hold one under it, the shifted one over it -- placed where hud/view.js says, and
/// marked as the page marks them (a finger on it, a hold latched or locked, a layer below showing
/// through), with the combos as chips between their keys. Fingers are tracked one by one, so two
/// thumbs chord; a finger on a chip is that combo. Minimized, it is its banner alone, a bar.
final class BoardView: UIView {
    static let bannerHeight: CGFloat = 26

    var onDown: ((Int) -> Void)?
    var onUp: ((Int) -> Void)?
    /// A finger on (true) or off a combo's chip.
    var onChord: ((Int, Bool) -> Void)?

    var geometry: Geometry? { didSet { rebuild() } }
    var board: Board? { didSet { paint() } }
    var problem: String? { didSet { paint() } }
    var chips: [Chip] = [] { didSet { paintChips() } }
    /// Down to the banner: the keys put away, and no finger takes one.
    var minimized = false {
        didSet {
            keys.forEach { $0.isHidden = minimized }
            chipLayers.forEach { $0.isHidden = minimized }
            mini.setImage(UIImage(systemName: minimized ? "chevron.up" : "chevron.down"), for: .normal)
            mini.accessibilityLabel = minimized ? "maximize the keyboard" : "minimize the keyboard"
        }
    }

    let globe = UIButton(type: .system)
    /// The banner's two, as the HUD's page has them: minimize, and detach -- the next keyboard, the
    /// system's own working as it always did.
    let mini = UIButton(type: .system)
    let detach = UIButton(type: .system)
    private let name = UILabel()
    private let sub = UILabel()
    private var keys: [KeyLayer] = []
    private var chipLayers: [CATextLayer] = []
    private enum Finger { case key(Int), chip(Int) }
    private var fingers: [ObjectIdentifier: Finger] = [:]

    // The dark keys of hud.css.
    private enum Ink {
        static let board = UIColor(red: 0.10, green: 0.11, blue: 0.15, alpha: 1)
        static let key = UIColor(red: 0.16, green: 0.17, blue: 0.24, alpha: 1)
        static let pressed = UIColor(red: 0.48, green: 0.64, blue: 0.97, alpha: 1)
        static let latch = UIColor(red: 0.88, green: 0.69, blue: 0.41, alpha: 1)
        static let refused = UIColor(red: 0.97, green: 0.46, blue: 0.56, alpha: 1)
        static let text = UIColor(red: 0.78, green: 0.81, blue: 0.92, alpha: 1)
        static let faint = UIColor(red: 0.55, green: 0.58, blue: 0.68, alpha: 1)
    }

    override init(frame: CGRect) {
        super.init(frame: frame)
        isMultipleTouchEnabled = true
        backgroundColor = Ink.board
        name.font = .boldSystemFont(ofSize: 15)
        name.textColor = Ink.text
        sub.font = .systemFont(ofSize: 12)
        sub.textColor = Ink.faint
        globe.setTitle("🌐", for: .normal)
        mini.setImage(UIImage(systemName: "chevron.down"), for: .normal)
        mini.accessibilityLabel = "minimize the keyboard"
        detach.setImage(UIImage(systemName: "keyboard"), for: .normal)
        detach.accessibilityLabel = "detach: the next keyboard"
        for b in [mini, detach] { b.tintColor = Ink.faint }
        for v in [name, sub, globe, mini, detach] as [UIView] { addSubview(v) }
    }

    required init?(coder: NSCoder) { fatalError("BoardView is made in code") }

    override func layoutSubviews() {
        super.layoutSubviews()
        let h = Self.bannerHeight
        globe.frame = globe.isHidden ? .zero : CGRect(x: bounds.width - 40, y: 0, width: 36, height: h)
        let right = globe.isHidden ? bounds.width - 4 : globe.frame.minX
        detach.frame = CGRect(x: right - 36, y: 0, width: 36, height: h)
        mini.frame = CGRect(x: right - 72, y: 0, width: 36, height: h)
        name.frame = CGRect(x: 10, y: 0, width: bounds.width / 2, height: h)
        sub.frame = CGRect(x: bounds.width / 2, y: 0, width: max(0, mini.frame.minX - bounds.width / 2 - 8), height: h)
        sub.textAlignment = .right
    }

    private func rebuild() {
        keys.forEach { $0.removeFromSuperlayer() }
        keys = []
        guard let g = geometry else { return }
        let scale = traitCollection.displayScale
        for r in g.rects {
            let k = KeyLayer(scale: scale)
            k.bounds = CGRect(x: 0, y: 0, width: r.w, height: r.h)
            k.position = CGPoint(x: r.centerX, y: r.centerY + Self.bannerHeight)
            k.setAffineTransform(CGAffineTransform(rotationAngle: CGFloat(r.r * Double.pi / 180)))
            k.isHidden = minimized
            layer.addSublayer(k)
            keys.append(k)
        }
        paint()
    }

    private func paint() {
        name.text = problem == nil ? board?.banner.name : "ZMK Layer HUD"
        sub.text = problem ?? board?.banner.sub
        guard let faces = board?.keys, faces.count == keys.count else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for (k, f) in zip(keys, faces) {
            k.show(f, ink: (f.pressed ? Ink.pressed : f.locked ? Ink.latch : Ink.key,
                           f.latched || f.locked ? Ink.latch : nil, f.faint ? Ink.faint : Ink.text))
        }
        CATransaction.commit()
    }

    private func paintChips() {
        chipLayers.forEach { $0.removeFromSuperlayer() }
        chipLayers = chips.map { c in
            let l = CATextLayer()
            l.contentsScale = traitCollection.displayScale
            l.frame = CGRect(x: c.x - c.size / 2, y: c.y - c.size / 2 + Self.bannerHeight, width: c.size, height: c.size)
            l.cornerRadius = 6
            l.backgroundColor = (c.pressed ? Ink.pressed : UIColor(red: 0.23, green: 0.26, blue: 0.38, alpha: 1)).cgColor
            l.foregroundColor = (c.pressed ? Ink.board : Ink.text).cgColor
            l.alignmentMode = .center
            l.fontSize = min(c.size * 0.45, 14)
            l.string = c.tap
            l.isHidden = minimized
            layer.addSublayer(l)
            return l
        }
    }

    /// A key that cannot type here, flashed.
    func flash(_ idx: [Int]) {
        for i in idx where i < keys.count {
            let k = keys[i]
            k.backgroundColor = Ink.refused.cgColor
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.paint() }
        }
    }

    // ---------- fingers ----------

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let g = geometry, !minimized else { return }
        for t in touches {
            let p = t.location(in: self)
            let x = Double(p.x), y = Double(p.y - Self.bannerHeight)
            if let combo = HitTest.chip(at: x, y, in: chips) {
                fingers[ObjectIdentifier(t)] = .chip(combo)
                onChord?(combo, true)
            } else if let idx = HitTest.key(at: x, y, in: g) {
                fingers[ObjectIdentifier(t)] = .key(idx)
                onDown?(idx)
            }
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) { lift(touches) }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { lift(touches) }

    private func lift(_ touches: Set<UITouch>) {
        for t in touches {
            switch fingers.removeValue(forKey: ObjectIdentifier(t)) {
            case let .key(idx): onUp?(idx)
            case let .chip(combo): onChord?(combo, false)
            case nil: break
            }
        }
    }
}

/// One key: a rounded layer with its three legends.
final class KeyLayer: CALayer {
    private let tap = CATextLayer()
    private let hold = CATextLayer()
    private let shifted = CATextLayer()

    init(scale: CGFloat) {
        super.init()
        cornerRadius = 6
        for t in [tap, hold, shifted] {
            t.contentsScale = scale
            t.alignmentMode = .center
            t.truncationMode = .end
            addSublayer(t)
        }
    }

    override init(layer: Any) { super.init(layer: layer) }

    required init?(coder: NSCoder) { fatalError("KeyLayer is made in code") }

    override func layoutSublayers() {
        super.layoutSublayers()
        let h = bounds.height, w = bounds.width
        let big = min(h * 0.38, 26), small = min(h * 0.2, 12)
        tap.fontSize = big
        hold.fontSize = small
        shifted.fontSize = small
        tap.frame = CGRect(x: 2, y: (h - big * 1.25) / 2, width: w - 4, height: big * 1.25)
        hold.frame = CGRect(x: 2, y: h - small * 1.4 - 2, width: w - 4, height: small * 1.4)
        shifted.frame = CGRect(x: 2, y: 2, width: w - 4, height: small * 1.4)
    }

    func show(_ f: KeyFace, ink: (fill: UIColor, ring: UIColor?, text: UIColor)) {
        backgroundColor = f.blank ? UIColor.clear.cgColor : ink.fill.cgColor
        borderColor = ink.ring?.cgColor
        borderWidth = ink.ring == nil ? 0 : 2
        // A glyph's legend is its text spelling here: the keyboard draws text alone.
        tap.string = f.tap
        hold.string = f.hold
        shifted.string = f.shifted
        for t in [tap, hold, shifted] { t.foregroundColor = ink.text.cgColor }
        setNeedsLayout()
    }
}
