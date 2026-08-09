import Testing
import SwiftUI
#if canImport(AppKit)
import AppKit
#endif
@testable import HORI

/// Tests for the HORI theme system.
///
/// Verifies that color palette values are correct for both
/// dark and light mode, and that fallback colors match the
/// hex values specified in the design system.
@Suite("HORI Theme")
struct ThemeTests {

    // MARK: - Dark Mode Fallbacks

    @Test("Dark background is warm near-black, not pure black")
    func darkBackground() {
        let color = HoriTheme.backgroundFallbackDark
            .components(relativeTo: .dark)
        #expect(color.red > 0.03 && color.red < 0.05, "Expected #0A0A0A, got red: \(color.red)")
        #expect(color.green > 0.03 && color.green < 0.05)
        #expect(color.blue > 0.03 && color.blue < 0.05)
    }

    @Test("Light background is warm off-white, not clinical white")
    func lightBackground() {
        let color = HoriTheme.backgroundFallbackLight
            .components(relativeTo: .light)
        #expect(color.red > 0.95 && color.red < 0.97, "Expected #F5F5F7, got red: \(color.red)")
        #expect(color.green > 0.95 && color.green < 0.97)
        #expect(color.blue > 0.96 && color.blue < 0.98)
    }

    @Test("Dark surface is slightly lighter than dark background")
    func darkSurfaceLighterThanBackground() {
        let surface = HoriTheme.surfaceFallbackDark
            .components(relativeTo: .dark)
        let background = HoriTheme.backgroundFallbackDark
            .components(relativeTo: .dark)
        #expect(surface.red > background.red, "Surface should be lighter than background")
    }

    @Test("Accent is violet #7C9EFF")
    func accentColor() {
        let color = HoriTheme.accentFallback
            .components(relativeTo: .dark)
        #expect(abs(color.red - 0.486) < 0.02, "Expected red ~0.486, got \(color.red)")
        #expect(abs(color.green - 0.620) < 0.02)
        #expect(abs(color.blue - 1.0) < 0.02)
    }

    @Test("Semantic idle is green")
    func semanticIdle() {
        let color = HoriTheme.semanticIdle
            .components(relativeTo: .dark)
        #expect(color.green > color.red && color.green > color.blue, "Idle should be green-dominant")
    }

    @Test("Semantic thinking is orange")
    func semanticThinking() {
        let color = HoriTheme.semanticThinking
            .components(relativeTo: .dark)
        #expect(color.red > 0.9 && color.green > 0.5 && color.blue < 0.1, "Thinking should be orange")
    }

    @Test("Semantic error is red")
    func semanticError() {
        let color = HoriTheme.semanticError
            .components(relativeTo: .dark)
        #expect(color.red > 0.9 && color.green < 0.3 && color.blue < 0.3, "Error should be red")
    }

    // MARK: - Fallback Resolver

    @Test("Background resolver returns correct fallback for dark mode")
    func backgroundResolverDark() {
        let color = HoriTheme.background(for: .dark)
        let components = color.components(relativeTo: .dark)
        #expect(components.red > 0.03 && components.red < 0.05)
    }

    @Test("Background resolver returns correct fallback for light mode")
    func backgroundResolverLight() {
        let color = HoriTheme.background(for: .light)
        let components = color.components(relativeTo: .light)
        #expect(components.red > 0.95)
    }
}

// MARK: - Color Components Helper

/// Extension to extract RGB components from a Color for testing.
/// SwiftUI Color doesn't provide direct component access, so we
/// convert via NSColor on macOS.
extension Color {
    struct RGBComponents {
        let red: Double
        let green: Double
        let blue: Double
        let alpha: Double
    }

    func components(relativeTo scheme: ColorScheme) -> RGBComponents {
        #if canImport(AppKit)
        let nsColor = NSColor(self).usingColorSpace(.sRGBColorSpace) ?? NSColor.black
        return RGBComponents(
            red: Double(nsColor.redComponent),
            green: Double(nsColor.greenComponent),
            blue: Double(nsColor.blueComponent),
            alpha: Double(nsColor.alphaComponent)
        )
        #else
        return RGBComponents(red: 0, green: 0, blue: 0, alpha: 1)
        #endif
    }
}
