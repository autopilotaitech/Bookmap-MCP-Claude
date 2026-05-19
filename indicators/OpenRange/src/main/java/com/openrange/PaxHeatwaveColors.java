package com.openrange;

import java.awt.Color;

final class PaxHeatwaveColors {

    static final Color BG_OUTER = new Color(8, 11, 15, 235);
    static final Color BG_INNER = new Color(13, 17, 22, 235);
    static final Color BORDER = new Color(60, 72, 84);
    static final Color SEP = new Color(34, 42, 50);

    static final Color TEXT = new Color(208, 216, 224);
    static final Color TEXT_MUTED = new Color(140, 152, 164);
    static final Color TEXT_DIM = new Color(90, 100, 110);

    static final Color BULL = new Color(72, 200, 140);
    static final Color BEAR = new Color(230, 90, 100);
    static final Color AMBER = new Color(232, 180, 64);
    static final Color NEUTRAL = new Color(150, 160, 172);

    static final Color TINT_BULL = new Color(72, 200, 140, 26);
    static final Color TINT_BEAR = new Color(230, 90, 100, 26);
    static final Color TINT_AMBER = new Color(232, 180, 64, 22);
    static final Color TINT_NEUTRAL = new Color(0, 0, 0, 0);

    static final Color AGE_FRESH = TEXT_MUTED;
    static final Color AGE_WARN = AMBER;
    static final Color AGE_DEAD = BEAR;

    static Color toneText(PaxHeatwaveModel.Tone tone) {
        if (tone == PaxHeatwaveModel.Tone.BULL) {
            return BULL;
        }
        if (tone == PaxHeatwaveModel.Tone.BEAR) {
            return BEAR;
        }
        if (tone == PaxHeatwaveModel.Tone.AMBER) {
            return AMBER;
        }
        return NEUTRAL;
    }

    static Color toneTint(PaxHeatwaveModel.Tone tone) {
        if (tone == PaxHeatwaveModel.Tone.BULL) {
            return TINT_BULL;
        }
        if (tone == PaxHeatwaveModel.Tone.BEAR) {
            return TINT_BEAR;
        }
        if (tone == PaxHeatwaveModel.Tone.AMBER) {
            return TINT_AMBER;
        }
        return TINT_NEUTRAL;
    }

    static Color verdictColor(String verdict) {
        if (verdict == null) {
            return NEUTRAL;
        }
        String v = verdict.toUpperCase();
        if (v.startsWith("ENTER_LONG") || v.equals("LONG_FOLLOW") || v.equals("FOLLOW_LONG")) {
            return BULL;
        }
        if (v.startsWith("ENTER_SHORT") || v.equals("SHORT_FOLLOW") || v.equals("FOLLOW_SHORT") || v.contains("FADE_LONG")) {
            return BEAR;
        }
        if (v.startsWith("STAND_DOWN") || v.equals("NO DATA")) {
            return AMBER;
        }
        return NEUTRAL;
    }

    private PaxHeatwaveColors() {
    }
}
