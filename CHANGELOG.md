# Changelog

## [1.2.0](https://github.com/rafaelromao/zmk-layer-hud/compare/v1.1.1...v1.2.0) (2026-11-11)


### Features

* the HUD opens at the keyboard's own size, and a grip makes it any other ([0e4cba2](https://github.com/rafaelromao/zmk-layer-hud/commit/0e4cba20381b5fef14b2677ccb8b4560497eb1e0))
* the HUD opens at the keyboard's own size, and a grip makes it any other ([9138dd5](https://github.com/rafaelromao/zmk-layer-hud/commit/9138dd5bb31762f0a6d3788310fae19ff58cb815))


### Fixes

* a character typed just before a chord is not taken for the chord's tap ([6715a07](https://github.com/rafaelromao/zmk-layer-hud/commit/6715a0765d611dc6e076c122bb59b1917caf79fc))
* a chord waiting as secure input begins draws nothing, and each held pill is let go by its own keys ([59dd2e4](https://github.com/rafaelromao/zmk-layer-hud/commit/59dd2e48495559e4f4e85667bb3588223457e417))
* a combo held for its modifiers says so: its pill shows the hold and its keys ring as the modifier ([8a6644d](https://github.com/rafaelromao/zmk-layer-hud/commit/8a6644d8b4d6bedebffa782f5fc04cdc5226153c))
* a combo held for its modifiers shows the hold, not its tap ([49e550d](https://github.com/rafaelromao/zmk-layer-hud/commit/49e550d3106c2ea2feaef9c6d4fdb1b5f6f49988))
* a combo's pill waits for the chord to say whether it was tapped or held ([12bb9d6](https://github.com/rafaelromao/zmk-layer-hud/commit/12bb9d6d78ee7fab35cb9da7dfac274baefb64d8))
* a key position told twice is one press: a Diamond on USB and BLE at once ([9b0cbbb](https://github.com/rafaelromao/zmk-layer-hud/commit/9b0cbbb3eb759809fa25600c6c5c0dfb0038d3fa))
* export rounds a half step up as the page does, and test the guards the scale relies on ([a0487b4](https://github.com/rafaelromao/zmk-layer-hud/commit/a0487b44c13bd4e3a0d6f216a7e0a6a67f9ad4a0))
* judge the session and speed heatmaps against every layer, not the one shown ([b696fd5](https://github.com/rafaelromao/zmk-layer-hud/commit/b696fd547d8a38aa3f744bb5fb3dad976d8e962c))
* judge the session and speed heatmaps against every layer, not the one shown ([6240746](https://github.com/rafaelromao/zmk-layer-hud/commit/624074640ec1ab06a0400e7462d26b30b5e655cd))
* keep layer mappings aligned with firmware symbols ([54f1ab7](https://github.com/rafaelromao/zmk-layer-hud/commit/54f1ab7eea3e241cc33891f387741519ebb78945))
* make fixture draws the Diamond from its sample, not from this machine's config ([8bad4ef](https://github.com/rafaelromao/zmk-layer-hud/commit/8bad4eff6347cea89b2bd7ee3ad93b887f2b4b25))
* recover layer transport and decode Linux cedilla correctly ([5ec92a4](https://github.com/rafaelromao/zmk-layer-hud/commit/5ec92a4d91f365e04b057aabe8a9952d2a292de8))
* the grip is bottom right on Linux too ([e50f8b5](https://github.com/rafaelromao/zmk-layer-hud/commit/e50f8b511254f9e3b60ad10c24afb2daaf1dd847))
* the host tests and the feed's help as CI's machines run them ([b0dcf02](https://github.com/rafaelromao/zmk-layer-hud/commit/b0dcf02f5ce9e5e80c28d7894b6ff567d76b6f46))
* the Linux panel draws whole again under WebKitGTK 2.54 ([af10997](https://github.com/rafaelromao/zmk-layer-hud/commit/af109973a01b08138d1164dbf51d6820b2f77b19))
* the signal goes only to the host the keyboard is typing to ([37b17db](https://github.com/rafaelromao/zmk-layer-hud/commit/37b17dbf35407d0d4f15a4842b7c1bafbec62f70))
* the signal goes only to the host the keyboard is typing to ([1a1b809](https://github.com/rafaelromao/zmk-layer-hud/commit/1a1b809bd984290bdeddb5e28c13dde451de2c0d))
* zmk-layer-hud poke names itself on every Python 3.14, as the feed does ([638645f](https://github.com/rafaelromao/zmk-layer-hud/commit/638645f6cbb5ee1184866bb76684e8c006200680))


### Documentation

* the published boards carry combo_decide_ms, as make site writes them ([ba46bc9](https://github.com/rafaelromao/zmk-layer-hud/commit/ba46bc9f57bfbd2ea00721c0a9b27f498c003c22))
* the signal goes where the typing goes, and what that takes ([07a9064](https://github.com/rafaelromao/zmk-layer-hud/commit/07a90641eac3152039a2227f91d758948500df6c))


### Chores

* the first release made by release-please is 1.2.0 ([88ecc0a](https://github.com/rafaelromao/zmk-layer-hud/commit/88ecc0a8c06227160413386d7cebcc61909c922e))
