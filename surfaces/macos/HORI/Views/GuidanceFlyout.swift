import SwiftUI

/// A flyout that appears when HORI indicates she can't or shouldn't
/// do something — the "No Wrong Notes" principle made visible.
///
/// In the full vision, this is driven by the CapabilityChecker
/// consulting a topology before any operation. In this slice, we
/// detect COULDN'T/SHOULDN'T patterns client-side from HORI's
/// response text. No server-side safety changes.
///
/// Traces to: docs/roadmap.md MAC-6 (The Emerging Canvas).
struct GuidanceFlyout: View {

    /// The type of guidance to show.
    let type: GuidanceType

    /// The message to display.
    let message: String

    /// Whether the flyout is visible.
    @Binding var isVisible: Bool

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if isVisible {
            HStack(spacing: 10) {
                Image(systemName: type.icon)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(type.color)

                VStack(alignment: .leading, spacing: 2) {
                    Text(type.title)
                        .font(HoriTypography.label)
                        .foregroundStyle(HoriTheme.text(for: colorScheme))

                    Text(message)
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .lineLimit(3)
                }

                Spacer()

                Button {
                    withAnimation(HoriAnimations.snappy(reduceMotion: reduceMotion)) {
                        isVisible = false
                    }
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Dismiss guidance")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(type.backgroundColor(for: colorScheme))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(type.color.opacity(0.3), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.2), radius: 4, y: 2)
            .transition(.move(edge: .top).combined(with: .opacity))
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(type.title): \(message)")
        }
    }

    // MARK: - Guidance Type

    enum GuidanceType {
        case couldnt
        case shouldnt

        var title: String {
            switch self {
            case .couldnt: return "Couldn't do that"
            case .shouldnt: return "Shouldn't do that"
            }
        }

        var icon: String {
            switch self {
            case .couldnt: return "hand.raised.slash"
            case .shouldnt: return "exclamationmark.shield"
            }
        }

        var color: Color {
            switch self {
            case .couldnt: return HoriTheme.semanticWarning
            case .shouldnt: return HoriTheme.semanticError
            }
        }

        func backgroundColor(for scheme: ColorScheme) -> Color {
            switch self {
            case .couldnt: return HoriTheme.surface(for: scheme).opacity(0.95)
            case .shouldnt: return HoriTheme.surface(for: scheme).opacity(0.95)
            }
        }
    }

    // MARK: - Detection

    /// Detects COULDN'T/SHOULDN'T patterns in HORI's response text.
    /// Returns a guidance type and message if detected, nil otherwise.
    /// This is a client-side heuristic — the full vision uses a
    /// CapabilityChecker on the server side.
    static func detect(in text: String) -> (GuidanceType, String)? {
        let lower = text.lowercased()

        // COULDN'T patterns — HORI can't do something
        let couldntPatterns = [
            "i can only read files",
            "i can't create",
            "i can't save",
            "i can't write",
            "i can't modify",
            "i can't delete",
            "i can't access",
            "i don't have permission",
            "i'm not able to create",
            "i'm not able to save",
            "i'm not able to write",
            "i'm not able to modify",
            "i'm not able to delete",
        ]

        for pattern in couldntPatterns {
            if lower.contains(pattern) {
                // Extract a short message from the context
                let message = extractMessage(from: text, around: pattern)
                return (.couldnt, message)
            }
        }

        // SHOULDN'T patterns — HORI shouldn't do something (safety)
        let shouldntPatterns = [
            "i shouldn't",
            "i should not",
            "that would be unsafe",
            "that's not safe",
            "i won't do that",
            "i will not do that",
            "i can't help with that",
        ]

        for pattern in shouldntPatterns {
            if lower.contains(pattern) {
                let message = extractMessage(from: text, around: pattern)
                return (.shouldnt, message)
            }
        }

        return nil
    }

    /// Extracts a short message from the text around the matched pattern.
    private static func extractMessage(from text: String, around pattern: String) -> String {
        // Find the sentence containing the pattern
        let sentences = text.split(separator: ". ")
        for sentence in sentences {
            if sentence.lowercased().contains(pattern) {
                let trimmed = String(sentence).trimmingCharacters(in: .whitespacesAndNewlines)
                // Limit to ~100 chars
                if trimmed.count > 100 {
                    return String(trimmed.prefix(100)) + "..."
                }
                return trimmed
            }
        }
        // Fallback: just return the pattern context
        return "HORI indicated a limitation."
    }
}
