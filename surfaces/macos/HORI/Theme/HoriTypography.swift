import SwiftUI

/// HORI typography system.
///
/// Uses a custom typeface for warmth and personality, with SF Pro
/// as the system fallback. The custom face is chosen in Phase 0
/// by testing candidates in the actual empty state view.
///
/// Candidates evaluated: DM Sans, Space Grotesk, Plus Jakarta Sans.
/// All three are warm, modern, geometric-humanist sans-serifs that
/// avoid the clinical feel of SF Pro while remaining highly legible.
///
/// Weights:
/// - regular: body text, message content
/// - semibold: headers, labels, empty state prompt
/// - bold: display text (large titles, if needed)
///
/// Monospace: SF Mono for code display (used in later phases).
enum HoriTypography {

    /// The custom typeface name. Set once in Phase 0 after testing.
    /// Default: "DM Sans" — warm, rounded, modern, highly legible.
    /// To change: update this constant and ensure the font is in
    /// the asset catalog or bundled with the app.
    static let customFontName = "DM Sans"

    // MARK: - Font Modifiers

    /// Body text — regular weight, 15pt.
    /// Used for message content, descriptions, general UI text.
    static let body = Font.custom(customFontName, size: 15, relativeTo: .body)

    /// Body text — monospace variant for code display.
    static let bodyMono = Font.system(size: 14, weight: .regular, design: .monospaced)

    /// Label — semibold, 13pt.
    /// Used for buttons, field labels, metadata.
    static let label = Font.custom(customFontName, size: 13, weight: .semibold, relativeTo: .caption)

    /// Header — semibold, 17pt.
    /// Used for section headers, project names.
    static let header = Font.custom(customFontName, size: 17, weight: .semibold, relativeTo: .headline)

    /// Display — bold, 24pt.
    /// Used for the empty state prompt, large titles.
    static let display = Font.custom(customFontName, size: 24, weight: .semibold, relativeTo: .largeTitle)

    /// Caption — regular, 12pt, secondary color.
    /// Used for timestamps, hints, subtle metadata.
    static let caption = Font.custom(customFontName, size: 12, weight: .regular, relativeTo: .caption2)

    // MARK: - View Extensions

    /// Applies the body font to a view.
    static func bodyFont<V: View>(_ view: V) -> some View {
        view.font(body)
    }
}
