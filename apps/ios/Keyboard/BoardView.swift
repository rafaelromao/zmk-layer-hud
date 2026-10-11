import UIKit
import ZmkHudCore

/// The board, drawn natively: a banner, and every key a layer with its legends -- the tap one in the
/// middle, the hold one under it, the shifted one over it -- placed where hud/view.js says, and
/// marked as the page marks them (a finger on it, a hold latched or locked, a layer below showing
/// through). Fingers are tracked one by one, so two thumbs chord.
final class BoardView: UIView {
    static let bannerHeight: CGFloat = 26

    var onDown: ((Int) -> Void)?
    var onUp: ((Int) -> Void)?

    var geometry: Geometry? { didSet { rebuild() } }
    var board: Board? { didSet { paint() } }
    var problem: String? { didSet { paint() } }

    let globe = UIButton(type: .system)
    private let name = UILabel()
    private let sub = UILabel()
    private var keys: [KeyLayer] = []
    private var fingers: [ObjectIdentifier: Int] = [:]

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
        for v in [name, sub, globe] as [UIView] { addSubview(v) }
    }

    required init?(coder: NSCoder) { fatalError("BoardView is made in code") }

    override func layoutSubviews() {
        super.layoutSubviews()
        let h = Self.bannerHeight
        globe.frame = globe.isHidden ? .zero : CGRect(x: bounds.width - 40, y: 0, width: 36, height: h)
        name.frame = CGRect(x: 10, y: 0, width: bounds.width / 2, height: h)
        sub.frame = CGRect(x: bounds.width / 2, y: 0, width: bounds.width / 2 - 50, height: h)
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
        guard let g = geometry else { return }
        for t in touches {
            let p = t.location(in: self)
            guard let idx = HitTest.key(at: Double(p.x), Double(p.y - Self.bannerHeight), in: g) else { continue }
            fingers[ObjectIdentifier(t)] = idx
            onDown?(idx)
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) { lift(touches) }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { lift(touches) }

    private func lift(_ touches: Set<UITouch>) {
        for t in touches {
            if let idx = fingers.removeValue(forKey: ObjectIdentifier(t)) { onUp?(idx) }
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
