# The tablets

Three ways to type with the HUD on a tablet, from the least to set up to the most:

| | What it is | What it types into | Set up |
|---|---|---|---|
| **Remote mode** | any tablet's browser, paired with a link | the computer | `zmk-layer-hud remote on` |
| **Android keyboard** | an input method: the tablet's own keyboard | the tablet's apps | build and sideload `apps/android` |
| **iPad keyboard** | a keyboard extension: the iPad's own keyboard | the iPad's apps | build and sign `apps/ios` in Xcode |

All three run the same virtual firmware as the desktop HUD (`hud/engine.js`, [docs/touch.md](touch.md)),
so a key does on glass what it does on your keyboard. The keyboards draw the keymap
`zmk-layer-hud keymap -o board.json` writes on the computer: the same message the HUD draws, with
what every key does compiled into it.

## Remote mode

Nothing to install. On the computer, with the HUD running:

```bash
zmk-layer-hud remote on
```

It prints a link, `http://<this computer>:8768/#k=<key>`, and a code to scan when `qrencode` is
installed. Open it on the tablet, on the same network: the tablet's browser shows the board docked,
and what you tap is typed on the computer. On Android, add it to the home screen to have it as an
app (on an iPad a home-screen copy may keep storage of its own, without the key: use Safari). The link
is the pairing: keep it to yourself, and `zmk-layer-hud remote unpair` makes every link given so far
useless. On a network you do not trust, serve it on your Tailscale address instead
(`remote on --bind 100.x.y.z`): [what plain HTTP leaves open](touch.md#remote-mode).

## Android

**Build.** You need the Android SDK (Android Studio installs it; or the command-line tools with
`platforms;android-36` and `build-tools`), a JDK 17 or newer, and Gradle 9.7 or newer. Point the
build at the SDK with `ANDROID_HOME`, or `sdk.dir=` in `apps/android/local.properties`.

```bash
cd apps/android
gradle :app:assembleDebug
```

The app is `app/build/outputs/apk/debug/app-debug.apk`. Install it with `adb install`, or copy it to
the tablet and open it (allow installing from that source when asked). The build copies the HUD's
page from `hud/` into the app, as `apps/hud-files.json` lists it.

**Turn it on.** Open ZMK Layer HUD, tap *Turn the keyboard on* (Android's own screen: switch ZMK
Layer HUD on), then *Switch to it*.

**Your keymap.** On the computer, `zmk-layer-hud keymap -o board.json`; send the file to the tablet
(Quick Share, a cloud drive, a cable) and share it to ZMK Layer HUD, or open it with the app, or use
*Import a keymap…*. A keymap made by a newer zmk-layer-hud than the app knows is refused with a
sentence; one without what its keys do (a board from the landing page) is drawn and does nothing.

**What it does.**

- Text goes into the field as text; Backspace, Delete, Tab, Escape, the arrows, Home and End, the
  page keys and F1 to F12 as keys; ⌘ and Ctrl shortcuts as the key with its modifiers, which most
  apps with a hardware keyboard take.
- Enter is the field's own action where it has one (Go, Search, Send) and a new line where it is a
  multi-line field; Shift+Enter is always the key.
- A terminal's field (Termux) gets key events for what a US layout types from the key, and text for
  the rest, so its own key handling sees the keys.
- In a password field the board's strip shows nothing.
- The 🌐 key in the banner switches to the next keyboard, where Android offers one.
- It takes at most 45% of the screen. Vibration on each key is on, clicks off; the app sets both.

**What it does not.** The keyboard has no network permission: the app imports keymaps from files
only, so no keyboard of yours can reach the network. There are no key codes for F13 to F24, so those
keys do nothing.

**Tests.** `make test-android` runs the sink's tests on any JVM (Gradle and a JDK, no SDK);
`gradle :app:testDebugUnitTest` holds the sink's copies of Android's constants to the SDK's.

## iPad (and iPhone)

**Build.** You need a Mac with Xcode, and [XcodeGen](https://github.com/yonaskolb/XcodeGen)
(`brew install xcodegen`).

```bash
cd apps/ios
cp Local.xcconfig.example Local.xcconfig     # then edit it
xcodegen generate
open ZmkHud.xcodeproj
```

`Local.xcconfig` names what is yours: your team (Xcode › Settings › Accounts; a free personal team
works, though what it signs runs for 7 days), a bundle id prefix you own, and an App Group,
`group.<your prefix>.zmkhud`, which the app and its keyboard share the keymap through. Choose the
ZmkHud scheme and your iPad, and run.

**Turn it on.** Settings › General › Keyboard › Keyboards › Add New Keyboard… › ZMK Layer HUD. It
asks for no Full Access, and needs none: it sends nothing anywhere. Switch to it with 🌐.

**Your keymap.** In the app: *Import a keymap…* (Files), or AirDrop `board.json` to the iPad and
open it with ZMK Layer HUD. Or, with the HUD running on the computer:

```bash
zmk-layer-hud remote on --app
```

and open the link it prints on the iPad (scan its code with the camera): the app pairs with the
computer as a tablet's browser would, takes its keymap, and goes. The app asks for local network
access for this; the keyboard has none.

A keyboard without Full Access may be kept from the App Group the app writes to. If the keyboard
says it has no keymap after an import, put the keymap in `apps/ios/Keyboard/keymap.json` and build
again: it is then built into the keyboard.

**What it does.** Text into the field; a new line for Enter (the field makes it Go or Send itself);
Tab, Backspace and Delete; the cursor left and right by whole characters, and Home and End to the
ends of the line. Combos are chips, as on the other boards.

**What it does not.** An iOS keyboard extension can send no ⌘ or Ctrl shortcuts, no Escape, no up or
down, no page keys and no function keys: those keys do nothing there. Password and phone-number
fields always use the system keyboard. Without Full Access there are no key clicks. Glyphs are
drawn as their text spelling.

**Tests.** `make test-ios` runs Core's tests on the Mac (`swift test` in `apps/ios/Core`, no Xcode
needed): the golden answers replayed in JavaScriptCore, the text sink, hit-testing, the keymap
check and the pairing.

## How the pieces fit

| | Android | iPad |
|---|---|---|
| The board | the HUD's own page in a WebView, from the app's assets | drawn natively from `hud/view.js`'s answers |
| The firmware | `hud/engine.js` in the page | `hud/engine.js` in JavaScriptCore, through `hud/kbd.js` |
| Into the field | `ImeSink` (Kotlin), over an `InputConnection` | `TextSink` (Swift), over the `textDocumentProxy` |
| The keymap | the app's files | the App Group, or built in |
| Tested without the device | `apps/android/sink` | `apps/ios/Core` |

`apps/apps_test.py` (in `make test-apps`) holds both builds to `apps/hud-files.json` and to the key
names the page sends.

## Still to be seen on a device

These were built and tested as far as a machine without the SDKs goes: the Android sink and the iPad
Core pass their tests, the iPad keyboard type-checks against UIKit, and neither app has been built
or run on a tablet yet. On a device, look at:

- how soon the keyboard shows the first time, and whether two thumbs rolling keys are taken in order;
- whether the iPad keyboard can read the App Group without Full Access;
- that the Android keyboard comes back after its WebView's renderer is killed;
- that neither ever takes focus from the field, and that the height suits the screen in both
  orientations.
