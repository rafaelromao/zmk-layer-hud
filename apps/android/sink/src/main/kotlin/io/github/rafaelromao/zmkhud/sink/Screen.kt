package io.github.rafaelromao.zmkhud.sink

/**
 * How big this tablet's screen is, for the board's keys drawn their real size (hud/hud.js dockFit):
 * how many of the WebView's CSS pixels -- dp, Android's density-independent pixels -- make a
 * millimetre. The screen says how many of its pixels are an inch (DisplayMetrics.xdpi); one that
 * says what no screen has is taken at Android's own measure, 160 dp to the inch.
 */
object Screen {
    const val NOMINAL_DP_PER_MM = 160.0 / 25.4

    fun dpPerMm(xdpi: Float, density: Float): Double {
        if (!(density > 0f) || !(xdpi in 50f..1000f)) return NOMINAL_DP_PER_MM
        val perMm = xdpi / 25.4 / density
        return if (perMm in 2.0..30.0) perMm else NOMINAL_DP_PER_MM
    }
}
