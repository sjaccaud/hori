import SwiftUI

/// HORI animation system.
///
/// All animations use spring curves — no linear animations, no
/// abrupt state changes. The app feels alive from the first window.
///
/// Three standard curves for different contexts:
/// - snappy: buttons, hovers, small UI feedback (fast, responsive)
/// - balanced: transitions, panel reveals, message bubbles
/// - dramatic: modals, sheet presentations, canvas focus switches
///
/// Every animation checks `accessibilityReduceMotion` and provides
/// a linear fallback. This is a foundational decision (see plan):
/// accessibility is built in from Phase 0, not grafted in later.
enum HoriAnimations {

    /// Snappy — for buttons, hovers, small UI feedback.
    /// 300ms response, 0.8 damping. Feels instant but organic.
    static let snappy = Animation.spring(response: 0.3, dampingFraction: 0.8)

    /// Balanced — for transitions, panel reveals, message bubbles.
    /// 500ms response, 0.825 damping. Smooth, not sluggish.
    static let balanced = Animation.spring(response: 0.5, dampingFraction: 0.825)

    /// Dramatic — for modals, sheet presentations, canvas focus.
    /// 800ms response, 0.9 damping. Deliberate, theatrical.
    static let dramatic = Animation.spring(response: 0.8, dampingFraction: 0.9)

    // MARK: - Reduce Motion Fallbacks

    /// Returns the appropriate animation for the current motion preference.
    /// When Reduce Motion is enabled, replaces springs with linear fades
    /// that preserve the functional transition without decorative motion.
    ///
    /// Usage:
    /// ```
    /// .animation(HoriAnimations.snappy(reduceMotion: reduceMotion))
    /// ```
    static func snappy(reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: 0.2) : snappy
    }

    static func balanced(reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: 0.25) : balanced
    }

    static func dramatic(reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: 0.3) : dramatic
    }

    // MARK: - View Modifier

    /// A view modifier that applies the given animation, respecting
    /// Reduce Motion automatically via the environment.
    struct HoriAnimation: ViewModifier {
        let animation: (Bool) -> Animation
        @Environment(\.accessibilityReduceMotion) var reduceMotion

        func body(content: Content) -> some View {
            content.animation(animation(reduceMotion))
        }
    }
}

extension View {
    /// Applies a HORI animation, automatically respecting Reduce Motion.
    func horiAnimation(_ animation: @escaping (Bool) -> Animation) -> some View {
        modifier(HoriAnimations.HoriAnimation(animation: animation))
    }
}
