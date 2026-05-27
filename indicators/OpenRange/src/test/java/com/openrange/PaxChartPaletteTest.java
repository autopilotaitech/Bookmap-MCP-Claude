package com.openrange;

import java.awt.Color;

public class PaxChartPaletteTest {

    public static void main(String[] args) {
        bullIsExactHex3CDC7D();
        bearIsExactHexEB6464();
        standDownIsExactHex9C9C9C();
        severityIsExactHexFFD040();
        colorForDirectionMapsCorrectly();
        shapeForDirectionMapsCorrectly();
        authorizedRecognisesAllPaletteColors();
        authorizedRejectsArbitraryColors();
        authorizedRejectsForbiddenOrangeFromLegacy();
        System.out.println("PaxChartPaletteTest OK");
        System.exit(0);
    }

    private static void bullIsExactHex3CDC7D() {
        assertRgb(PaxChartPalette.BULL, 0x3C, 0xDC, 0x7D);
    }

    private static void bearIsExactHexEB6464() {
        assertRgb(PaxChartPalette.BEAR, 0xEB, 0x64, 0x64);
    }

    private static void standDownIsExactHex9C9C9C() {
        assertRgb(PaxChartPalette.STAND_DOWN, 0x9C, 0x9C, 0x9C);
    }

    private static void severityIsExactHexFFD040() {
        assertRgb(PaxChartPalette.SEVERITY_OUTLINE, 0xFF, 0xD0, 0x40);
    }

    private static void colorForDirectionMapsCorrectly() {
        if (!rgbEq(PaxChartPalette.colorForDirection(PaxLevelEdgeModel.Direction.LONG),
                PaxChartPalette.BULL))
            throw new AssertionError("LONG must map to BULL");
        if (!rgbEq(PaxChartPalette.colorForDirection(PaxLevelEdgeModel.Direction.SHORT),
                PaxChartPalette.BEAR))
            throw new AssertionError("SHORT must map to BEAR");
        if (!rgbEq(PaxChartPalette.colorForDirection(PaxLevelEdgeModel.Direction.WAIT),
                PaxChartPalette.STAND_DOWN))
            throw new AssertionError("WAIT must map to STAND_DOWN");
        if (!rgbEq(PaxChartPalette.colorForDirection(null), PaxChartPalette.STAND_DOWN))
            throw new AssertionError("null direction must map to STAND_DOWN");
    }

    private static void shapeForDirectionMapsCorrectly() {
        if (PaxChartPalette.shapeForDirection(PaxLevelEdgeModel.Direction.LONG)
                != PaxChartPalette.DirectionShape.UP_ARROW)
            throw new AssertionError("LONG must map to UP_ARROW");
        if (PaxChartPalette.shapeForDirection(PaxLevelEdgeModel.Direction.SHORT)
                != PaxChartPalette.DirectionShape.DOWN_ARROW)
            throw new AssertionError("SHORT must map to DOWN_ARROW");
        if (PaxChartPalette.shapeForDirection(PaxLevelEdgeModel.Direction.WAIT)
                != PaxChartPalette.DirectionShape.SHIELD)
            throw new AssertionError("WAIT must map to SHIELD");
    }

    private static void authorizedRecognisesAllPaletteColors() {
        if (!PaxChartPalette.isAuthorized(PaxChartPalette.BULL))
            throw new AssertionError("BULL must be authorized");
        if (!PaxChartPalette.isAuthorized(PaxChartPalette.BEAR))
            throw new AssertionError("BEAR must be authorized");
        if (!PaxChartPalette.isAuthorized(PaxChartPalette.STAND_DOWN))
            throw new AssertionError("STAND_DOWN must be authorized");
        if (!PaxChartPalette.isAuthorized(PaxChartPalette.SEVERITY_OUTLINE))
            throw new AssertionError("SEVERITY_OUTLINE must be authorized");
    }

    private static void authorizedRejectsArbitraryColors() {
        if (PaxChartPalette.isAuthorized(null))
            throw new AssertionError("null must not be authorized");
        if (PaxChartPalette.isAuthorized(new Color(0, 0, 255)))
            throw new AssertionError("blue is not in the palette");
        if (PaxChartPalette.isAuthorized(new Color(128, 0, 128)))
            throw new AssertionError("purple is not in the palette");
    }

    private static void authorizedRejectsForbiddenOrangeFromLegacy() {
        // Legacy drawInstitutionalMarker used (255, 153, 0) for STAND_DOWN
        // markers in the price lane. The closed palette must reject this so
        // any reintroduction is caught.
        if (PaxChartPalette.isAuthorized(new Color(255, 153, 0)))
            throw new AssertionError("legacy orange must NOT be authorized");
    }

    private static void assertRgb(Color c, int r, int g, int b) {
        if (c == null) throw new AssertionError("null color");
        if (c.getRed() != r || c.getGreen() != g || c.getBlue() != b)
            throw new AssertionError("expected (" + r + "," + g + "," + b
                    + ") got (" + c.getRed() + "," + c.getGreen() + "," + c.getBlue() + ")");
    }

    private static boolean rgbEq(Color a, Color b) {
        return a.getRed() == b.getRed() && a.getGreen() == b.getGreen()
                && a.getBlue() == b.getBlue();
    }
}
