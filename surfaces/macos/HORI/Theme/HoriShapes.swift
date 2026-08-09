import SwiftUI

/// HORI shape system — corner radii and other geometric constants.
///
/// Rounded corners everywhere. No sharp edges. The app feels
/// soft and approachable, not clinical or industrial.
///
/// Three sizes for different element types:
/// - small: buttons, input fields, tags
/// - medium: cards, message bubbles, panels
/// - large: windows, sheets, major containers
enum HoriShapes {

    /// Small radius — buttons, input fields, tags.
    /// 8pt — subtle rounding, still feels precise.
    static let small: CGFloat = 8

    /// Medium radius — cards, message bubbles, panels.
    /// 12pt — clearly rounded, feels friendly.
    static let medium: CGFloat = 12

    /// Large radius — windows, sheets, major containers.
    /// 18pt — generously rounded, feels soft.
    static let large: CGFloat = 18

    // MARK: - View Extensions

    /// Applies a small corner radius.
    static func smallCorner<V: View>(_ view: V) -> some View {
        view.cornerRadius(small)
    }

    /// Applies a medium corner radius.
    static func mediumCorner<V: View>(_ view: V) -> some View {
        view.cornerRadius(medium)
    }

    /// Applies a large corner radius.
    static func largeCorner<V: View>(_ view: V) -> some View {
        view.cornerRadius(large)
    }
}
