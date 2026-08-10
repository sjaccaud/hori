import SwiftUI

/// The HORI color palette.
///
/// Warm professionalism: near-black backgrounds (not pure black),
/// off-white in light mode (not clinical white), a single accent
/// color, and semantic colors for state. Colors are defined as
/// asset catalog references so they adapt to dark/light mode
/// automatically, with fallbacks for when the catalog is not
/// available (e.g. in tests or previews without asset loading).
///
/// Design language: "2027 Product, Not Windows 95."
/// Reference apps: Things 3 (warmth), Craft (precision), Bear (themes).
enum HoriTheme {
    // MARK: - Background

    /// Primary window background — warm near-black in dark mode,
    /// warm off-white in light mode. Never pure black or clinical white.
    static let background = Color("HoriBackground", bundle: .main)
    static let backgroundFallbackDark = Color(red: 0.039, green: 0.039, blue: 0.039) // #0A0A0A
    static let backgroundFallbackLight = Color(red: 0.961, green: 0.961, blue: 0.969) // #F5F5F7

    /// Elevated surface — slightly lighter than background.
    /// Used for cards, message bubbles (HORI side), panels.
    static let surface = Color("HoriSurface", bundle: .main)
    static let surfaceFallbackDark = Color(red: 0.090, green: 0.090, blue: 0.090) // #171717
    static let surfaceFallbackLight = Color(red: 1.0, green: 1.0, blue: 1.0) // #FFFFFF

    // MARK: - Text

    /// Primary text — high contrast against background.
    static let text = Color("HoriText", bundle: .main)
    static let textFallbackDark = Color(red: 0.980, green: 0.980, blue: 0.980) // #FAFAFA
    static let textFallbackLight = Color(red: 0.114, green: 0.114, blue: 0.122) // #1D1D1F

    /// Secondary text — for hints, timestamps, metadata.
    static let textSecondary = Color("HoriTextSecondary", bundle: .main)
    static let textSecondaryFallbackDark = Color(red: 0.639, green: 0.639, blue: 0.639) // #A3A3A3
    static let textSecondaryFallbackLight = Color(red: 0.525, green: 0.525, blue: 0.545) // #86868B

    // MARK: - Borders

    /// Subtle border — for dividers, input field outlines.
    static let border = Color("HoriBorder", bundle: .main)
    static let borderFallbackDark = Color.white.opacity(0.10)
    static let borderFallbackLight = Color.black.opacity(0.08)

    // MARK: - Accent

    /// Single accent color — used for interactive elements,
    /// user message bubbles, focus rings, presence highlights.
    /// Violet, matching the existing web surfaces.
    static let accent = Color("HoriAccent", bundle: .main)
    static let accentFallback = Color(red: 0.486, green: 0.620, blue: 1.0) // #7C9EFF

    // MARK: - Semantic

    /// Idle / success — presence dot when HORI is available.
    static let semanticIdle = Color(red: 0.204, green: 0.780, blue: 0.349) // #34C759

    /// Thinking / warning — presence dot when HORI is processing,
    /// or warning flyouts.
    static let semanticThinking = Color(red: 1.0, green: 0.580, blue: 0.0) // #FF9800

    /// Error / destructive — connection errors, WON'T flyouts.
    static let semanticError = Color(red: 1.0, green: 0.231, blue: 0.188) // #FF3B30

    /// Warning — COULDN'T flyouts, capability limitations.
    static let semanticWarning = Color(red: 1.0, green: 0.757, blue: 0.027) // #FFC107

    // MARK: - Fallback Resolver

    /// Returns the appropriate fallback color for the current color scheme.
    /// Used in tests and previews where asset catalog colors may not load.
    static func background(for scheme: ColorScheme) -> Color {
        scheme == .dark ? backgroundFallbackDark : backgroundFallbackLight
    }

    static func surface(for scheme: ColorScheme) -> Color {
        scheme == .dark ? surfaceFallbackDark : surfaceFallbackLight
    }

    static func text(for scheme: ColorScheme) -> Color {
        scheme == .dark ? textFallbackDark : textFallbackLight
    }

    static func textSecondary(for scheme: ColorScheme) -> Color {
        scheme == .dark ? textSecondaryFallbackDark : textSecondaryFallbackLight
    }

    static func border(for scheme: ColorScheme) -> Color {
        scheme == .dark ? borderFallbackDark : borderFallbackLight
    }
}
