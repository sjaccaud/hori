import SwiftUI

/// Shows the file structure of the current project.
///
/// Displays a flat list of files in the project directory, sorted by path.
/// Clicking a file selects it (future: opens in editor).
///
/// Traces to: docs/roadmap.md MAC-5 (The Workshop).
struct ProjectStructureView: View {

    /// The project store (shared).
    let store: ProjectStore

    /// The current project.
    let project: HoriProject

    /// The list of files in the project.
    @State private var files: [ProjectFile] = []

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text(project.name)
                    .font(HoriTypography.header)
                    .foregroundStyle(HoriTheme.text(for: colorScheme))
                    .lineLimit(1)
                Spacer()
                Button {
                    refreshFiles()
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Refresh files")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(HoriTheme.surface(for: colorScheme))

            Divider()

            // File list
            if files.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "doc")
                        .font(.system(size: 24))
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.5))
                    Text("No files yet")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                    Text("Ask HORI to build something")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.5))
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(files) { file in
                            fileRow(file)
                        }
                    }
                }
            }
        }
        .background(HoriTheme.background(for: colorScheme))
        .onAppear { refreshFiles() }
    }

    // MARK: - File Row

    private func fileRow(_ file: ProjectFile) -> some View {
        HStack(spacing: 8) {
            Image(systemName: fileIcon(for: file.name))
                .font(.system(size: 12))
                .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                .frame(width: 16)

            VStack(alignment: .leading, spacing: 1) {
                Text(file.name)
                    .font(HoriTypography.body)
                    .foregroundStyle(HoriTheme.text(for: colorScheme))
                    .lineLimit(1)

                if file.path.contains("/") {
                    Text(file.path)
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.7))
                        .lineLimit(1)
                }
            }

            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .accessibilityLabel("File: \(file.path)")
    }

    // MARK: - Helpers

    private func refreshFiles() {
        files = (try? store.listFiles(project: project)) ?? []
    }

    /// Returns an SF Symbol icon for a file based on its extension.
    private func fileIcon(for filename: String) -> String {
        let ext = (filename as NSString).pathExtension.lowercased()
        switch ext {
        case "html", "htm": return "globe"
        case "css": return "paintbrush"
        case "js", "mjs": return "curlybraces"
        case "ts", "tsx": return "curlybraces"
        case "swift": return "swift"
        case "py": return "doc.text"
        case "json": return "text.alignleft"
        case "md": return "doc.richtext"
        case "txt": return "doc.plaintext"
        case "log": return "doc.plaintext"
        case "png", "jpg", "jpeg", "gif", "svg": return "photo"
        default: return "doc"
        }
    }
}
